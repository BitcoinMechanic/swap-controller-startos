"""Regtest adapter only: exercises executor code and controllers baked into image."""
import contextlib
import io
import subprocess
import shutil
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, '/app')
from controller import private_load, save
import executor
import lifecycle
import stale_restore
from partition_check import isolation


def recover_lost(external, direction):
    parent=external.parent
    assert not (parent/'execution').exists(), 'original journal was recreated'
    marker=private_load(parent/'lost-journal.json')
    assert marker['direction']==direction and marker['filename']==external.name
    if os.environ.get('OWNER_FENCE_TEST') == '1':
        from old_owner import check_old_owner
        check_old_owner(parent)
    root=parent/'stale-prepared'/'jobs'/'swap'
    before={name:(root/name).read_bytes() for name in ('intent.json','state.json','remote.json','permit.json')}
    assert lifecycle.tick(root.parent.parent)=={}
    try: executor.step(root)
    except ValueError as exc: assert str(exc)=='restored_execution_blocked'
    else: raise AssertionError('stale executor became enabled')
    if os.environ.get('PACKAGED_RECOVERY') == '1':
        child=subprocess.run([sys.executable, '/app/recovery.py', '--root', str(root),
            '--expected-digest', marker['digest'], '--credentials', str(parent/'remote-recovery.json'),
            '--confirm-resolution-only'], capture_output=True, text=True)
        # The packaged resolver keeps its own audit; merge only newly recorded
        # mutations into the fixture audit, including a discarded reply.
        audit=root.parent.parent/'recovery-audit.jsonl'
        checkpoint=parent/'packaged-recovery-audit-count.json'
        count=private_load(checkpoint)['count'] if checkpoint.exists() else 0
        entries=audit.read_text().splitlines(True) if audit.exists() else []
        assert count <= len(entries)
        with (parent/'remote-audit.jsonl').open('a') as stream:
            stream.writelines(entries[count:]); stream.flush(); os.fsync(stream.fileno())
        save(checkpoint,dict(count=len(entries)))
        dropped=root.parent.parent/'resolution-reply-lost.json'
        if dropped.exists(): save(parent/'resolution-reply-lost.json',private_load(dropped))
        if child.returncode:
            if child.returncode != 89: print('{"event":"packaged_recovery_blocked"}')
            return child.returncode
        output=private_load(root/'recovery-result.json')
        mirror,result=output['mirror'],output['result']
    else:
        import lost_journal
        mirror,result=lost_journal.resolve(root,marker['digest'],private_load(parent/'remote-recovery.json'),parent/'remote-audit.jsonl')
    assert before=={name:(root/name).read_bytes() for name in before}
    assert private_load(root.parent.parent/'restored.json')=={'blocked':True}
    save(external,mirror)  # Output-only compatibility view for the node fixture.
    save(parent/'lost-result.json',dict(phase=mirror['phase'],original_journal_absent=True,
                                     stale_phase=private_load(root/'state.json')['phase'],restored_block=True))
    print(json.dumps(result))
    return 0


def main():
    isolation()
    direction, flag, filename, *flags = sys.argv[1:]
    assert flag == '--state'
    external = Path(filename)
    manager = external.parent/'execution'
    use_worker = os.environ.get('LIFECYCLE_WORKER') == '1'
    stale_test = os.environ.get('STALE_RESTORE_TEST') == '1'
    lost_test = os.environ.get('LOST_JOURNAL_TEST') == '1'
    assert not lost_test or (use_worker and not stale_test)
    if lost_test and (external.parent/'lost-journal.json').exists():
        assert not flags
        return recover_lost(external,direction)
    assert not stale_test or use_worker
    if use_worker:
        lifecycle.setup(manager)
    root = manager/'jobs'/'swap' if use_worker else manager
    root.mkdir(mode=0o700, exist_ok=True)
    if not (root/'intent.json').exists():
        digest = executor.prepare(root, direction, private_load(external), private_load(external.parent/'remote.json'))
        # The automatic recovery path must not start a merely prepared swap.
        try: executor.step(root, recover_only=True)
        except ValueError: pass
        else: raise AssertionError('recovery originated an unauthorized payment')
        assert not (root/'launched.json').exists()
        executor.authorize(root, digest, int(time.time())+600, confirmed=True)
        if stale_test or lost_test: stale_restore.capture(root, external.parent/'stale-prepared')
    else:
        assert private_load(external) == private_load(root/'state.json'), 'fixture cannot replace durable executor state'
    try:
        if stale_test and not flags:
            reports = stale_restore.check_copies(external.parent)
            stale_restore.record(external.parent, reports)
        if use_worker and not flags:
            with contextlib.redirect_stdout(io.StringIO()):
                outcomes=lifecycle.tick(manager)
            result=outcomes.get('swap') or subprocess.CompletedProcess([], 1, '', '')
        else:
            result = executor.step(root, flags=flags)
    finally:
        # These mirrors are solely for the original node-fixture assertions.
        # The executable journal and credential binding stay in execution/.
        save(external, private_load(root/'state.json'))
        audit = root/'remote-audit.jsonl'
        if audit.exists():
            (external.parent/'remote-audit.jsonl').write_bytes(audit.read_bytes())
    if lost_test and flags and result.returncode == 88:
        assert private_load(root/'state.json')['phase']=='outgoing_started'
        token=executor.digest(private_load(root/'intent.json'))
        save(external.parent/'lost-journal.json',dict(digest=token,direction=direction,filename=external.name))
        if os.environ.get('OWNER_FENCE_TEST') == '1':
            shutil.copytree(manager,external.parent/'old-owner')
        shutil.rmtree(manager)
        assert not manager.exists()
    if stale_test and flags and result.returncode == 88:
        assert private_load(root/'state.json')['phase'] == 'outgoing_started'
        stale_restore.capture(root, external.parent/'stale-outgoing_started')
    if stale_test and not flags and result.returncode == 0:
        reports = stale_restore.check_copies(external.parent)
        stale_restore.record(external.parent, reports)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == '__main__': raise SystemExit(main())
