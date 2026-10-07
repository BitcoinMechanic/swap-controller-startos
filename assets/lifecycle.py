"""StartOS worker lifecycle. Dormant without disposable-regtest opt-in."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
from controller import private_load, save, require
import executor


def setup(root):
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    require(not root.is_symlink(), 'invalid_worker_directory')
    (root/'jobs').mkdir(mode=0o700, exist_ok=True)
    require(not (root/'jobs').is_symlink(), 'invalid_jobs_directory')


@contextlib.contextmanager
def locked(root):
    setup(root)
    fd=os.open(root/'lifecycle.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally: os.close(fd)


def jobs(root):
    return sorted((root/'jobs').iterdir())


def inventory(root):
    reports=[]
    for job in jobs(root):
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',job.name) or job.is_symlink() or not job.is_dir():
            reports.append(dict(outcome='unreadable')); continue
        try:
            if (job/'review.json').exists() and not (job/'intent.json').exists():
                from quote_workflow import report
                quote=report(job)
                reports.append(dict(job=job.name,direction='forward',phase=quote['phase'],outcome='unresolved_quote'))
                continue
            intent, state=executor.records(job)
            phase=state['phase']
            outcome=('terminal' if phase in executor.TERMINAL[intent['direction']] else
                     'launch_uncertain' if phase=='prepared' and (job/'launched.json').exists() else
                     'prepared' if phase=='prepared' else 'pending_recovery')
            reports.append(dict(job=job.name, direction=intent['direction'], phase=phase, outcome=outcome))
        except Exception: reports.append(dict(job=job.name, outcome='unreadable'))
    pilot=root/'forward-pilot'/'record.json'
    if pilot.exists():
        try:
            phase=private_load(pilot)['phase']
            reports.append(dict(job='forward-pilot',phase=phase,outcome='terminal' if phase in ('settled','failed') else 'pending_recovery'))
        except Exception:reports.append(dict(job='forward-pilot',outcome='unreadable'))
    from forward_swaps import paths, TERMINAL
    for path in paths(root.parent):
        try:
            phase=private_load(path/'record.json')['phase']
            reports.append(dict(job=path.name,phase=phase,outcome='terminal' if phase in TERMINAL else 'pending_recovery'))
        except Exception: reports.append(dict(job=path.name,outcome='unreadable'))
    return reports



def backup_status(root, restored):
    path=root/'backup-paused.json'
    if not os.path.lexists(path):return False,'none'
    try: marker=private_load(path)
    except Exception:return True,'unreadable'
    if marker=={'paused':True} and restored:
        # Old snapshots included this marker. Do not report it as proof that
        # a backup is currently running. Leave it on disk; execution guards
        # still use marker existence and the independent restore barrier.
        return False,'legacy_restored_marker'
    if marker in ({'paused':True},{'schema':1,'paused':True}):return True,'paused'
    return True,'unreadable'


def snapshot(root, now=None):
    with locked(root):
        now=int(time.time()) if now is None else now
        restored=os.path.lexists(root/'restored.json')
        paused,pause_state=backup_status(root,restored)
        reports=inventory(root)
        result=dict(live_payment_enabled=False, regtest_only=True, restored_block=restored,
                    backup_paused=paused, backup_pause_state=pause_state, jobs=reports, worker_fresh=False)
        try:
            heartbeat=private_load(root/'heartbeat.json')
            result['worker_fresh']=0<=now-heartbeat['checked_at']<=30
            result['worker_mode']=heartbeat['mode'] if heartbeat['mode'] in ('disabled','regtest','restored','backup_paused') else 'unknown'
        except Exception: result['worker_mode']='waiting'
        try:
            from forward_pilot import status as pilot_status
            pilot=pilot_status(root.parent)
            result['forward_pilot']={k:v for k,v in pilot.items() if k not in ('invoice','pilot_id')}
            result['live_payment_enabled']=pilot.get('phase') not in ('not_prepared','review','settled','failed') and not pilot.get('restore_blocked',False)
            result['regtest_only']=not result['live_payment_enabled']
        except Exception:result['forward_pilot']={'phase':'attention'}
        return result


def tick(root):
    with locked(root):
        mode=('restored' if os.path.lexists(root/'restored.json') else
              'backup_paused' if os.path.lexists(root/'backup-paused.json') else
              'regtest' if os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1' else 'disabled')
        save(root/'heartbeat.json', dict(checked_at=int(time.time()), mode=mode))
        selected=jobs(root) if mode=='regtest' else []
    # Each executor step takes the same lifecycle lock for its entire child
    # execution, so backup/restore cannot cross an in-progress submission.
    outcomes={}
    for job in selected:
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',job.name) or job.is_symlink() or not job.is_dir(): continue
        try:
            if (job/'review.json').exists():
                from quote_workflow import advance
                outcomes[job.name]=advance(job)
            else: outcomes[job.name]=executor.step(job)
        except Exception:
            outcomes[job.name]=None
            print('{"event":"worker_job_needs_attention","details":"withheld"}', flush=True)
    if mode in ('disabled','restored') and (root/'forward-pilot').exists():
        try:
            from forward_pilot import tick as pilot_tick
            outcomes['forward-pilot']=pilot_tick(root.parent)
            save(root/'forward-pilot-status.json',dict(needs_attention=False,checked_at=int(time.time())))
        except Exception:
            save(root/'forward-pilot-status.json',dict(needs_attention=True,checked_at=int(time.time())))
            print('{"event":"forward_pilot_needs_attention","details":"withheld"}',flush=True)
    if mode in ('disabled','restored'):
        try:
            from forward_swaps import tick as repeat_tick
            outcomes['forward-swaps']=repeat_tick(root.parent)
        except Exception:
            print('{"event":"repeat_swap_needs_attention","details":"withheld"}',flush=True)
    with locked(root):
        save(root/'heartbeat.json', dict(checked_at=int(time.time()), mode=mode))
    return outcomes


def backup_begin(root):
    with locked(root):
        reports=inventory(root)
        require(all(r['outcome']=='terminal' for r in reports), 'backup_refused_unresolved_execution')
        save(root/'backup-paused.json', dict(schema=1,paused=True))
        # Only this filtered history is included; jobs contain credentials and
        # permits and are excluded wholesale from the package backup.
        save(root/'history.json', dict(jobs=reports))


def backup_end(root):
    with locked(root):
        (root/'backup-paused.json').unlink(missing_ok=True)


def restored(root):
    with locked(root):
        # Commit barrier first. Never remove it automatically or clear it when
        # pairing read-only credentials. Also covers backups predating workers.
        save(root/'restored.json', dict(blocked=True))
        import secrets
        save(root/'forward-pilot-restore-epoch.json',dict(epoch=secrets.token_hex(32)))
        # The restored snapshot's pause belongs to the old backup operation.
        # Commit the independent restore barrier before removing that marker.
        (root/'backup-paused.json').unlink(missing_ok=True)
        (root/'heartbeat.json').unlink(missing_ok=True)


def main():
    os.umask(0o077)
    root=Path(sys.argv[1]); mode=sys.argv[2]
    if mode=='run':
        while True:
            try: tick(root)
            except Exception: print('{"event":"worker_cycle_unavailable"}', flush=True)
            time.sleep(5)
    elif mode=='status': print(json.dumps(snapshot(root)))
    elif mode=='backup-begin': backup_begin(root)
    elif mode=='backup-end': backup_end(root)
    elif mode=='restored': restored(root)
    else: raise ValueError('invalid_mode')


if __name__=='__main__':
    try: main()
    except Exception:
        print('{"event":"worker_operation_refused","details":"withheld"}')
        raise SystemExit(1) from None
