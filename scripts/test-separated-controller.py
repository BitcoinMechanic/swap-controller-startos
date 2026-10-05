"""Host-only Docker driver. Never mount the Docker socket into a test container."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

MODES = ('forward', 'forward-failure', 'reverse', 'reverse-failure')
FLAGS = {'--crash-after-sendpay', '--crash-after-btc', '--crash-after-xbt-resolution'}


def docker(*args, **kwargs):
    return subprocess.run(['docker', *map(str, args)], check=True, text=True,
                          capture_output=True, timeout=60, **kwargs).stdout.strip()


def image_id(name):
    return docker('image', 'inspect', '--format', '{{.Id}}', name)


def mount(source, target, readonly=False):
    return ['--mount', 'type=bind,src='+str(source)+',dst='+target+(',readonly' if readonly else '')]


def validate_job(job):
    if not isinstance(job, dict) or set(job) != {'direction', 'flags', 'filename', 'phase'}:
        raise ValueError('invalid_job_fields')
    direction = job['direction']
    if direction not in ('forward', 'reverse'):
        raise ValueError('invalid_direction')
    if job['filename'] != ('swap-state.json' if direction == 'forward' else 'reverse-state.json'):
        raise ValueError('invalid_state_filename')
    if (not isinstance(job['flags'], list) or len(job['flags']) > 1
            or any(not isinstance(f, str) or f not in FLAGS for f in job['flags'])):
        raise ValueError('invalid_flags')
    if job['phase'] not in {'prepared', 'outgoing_started', 'xbt_paid', 'xbt_failed',
                            'btc_paid', 'btc_failed', 'btc_released', 'xbt_released'}:
        raise ValueError('invalid_phase')
    return job


def controller_command(repo, shared, image, network, name, job, partition=False, packaged=False, lifecycle=False, stale=False, lost=False, drop_resolution_reply=False, reply_check=None, packaged_recovery=False, owner_fence=False, drop_submission_reply=False, partial_fence=False):
    validate_job(job)
    if partial_fence and not owner_fence: raise ValueError('partial_fence_requires_owner_fence')
    if drop_submission_reply and not owner_fence: raise ValueError('submission_reply_test_requires_owner_fence')
    if owner_fence and not (packaged and lifecycle and lost and packaged_recovery):
        raise ValueError('owner_fence_requires_packaged_recovery')
    if reply_check not in (None,'before','after'): raise ValueError('invalid_reply_check')
    command = ['docker', 'run', '--rm', '--name', name, '--network', network,
               '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m',
               '-e', 'BTC_XBT_DISPOSABLE_CONTAINER=1', '-e', 'PYTHONDONTWRITEBYTECODE=1',
               '-e', 'SEPARATE_CONTROLLER=1', '-e', 'REMOTE_TEST_ROOT=/controller-state',
               '-e', 'REMOTE_MODULES_DIR=/pinned',
               '-e', 'DROP_SUBMISSION_REPLY_TEST='+('1' if drop_submission_reply else '0'),
            '-e', 'OWNER_FENCE_TEST='+('1' if owner_fence else '0'),
               '-e', 'PACKAGED_RECOVERY='+('1' if packaged_recovery else '0'),
               '-e', 'PACKAGED_EXECUTOR='+('1' if packaged else '0'),
               '-e', 'LIFECYCLE_WORKER='+('1' if lifecycle else '0'),
               '-e', 'STALE_RESTORE_TEST='+('1' if stale else '0'),
               '-e', 'LOST_JOURNAL_TEST='+('1' if lost else '0'),
               '-e', 'DROP_RESOLUTION_REPLY='+('1' if drop_resolution_reply else '0'),
               *mount(shared/'control', '/controller-state'),
               *([] if packaged else [*mount(shared/'modules', '/pinned', True),
               *mount(repo/'assets', '/controller-assets', True)]),
               *mount(repo/'tests/remote', '/remote-tests', True),
               '--entrypoint', 'python3', image]
    if partial_fence:
        return [*command, '/remote-tests/partial_fence_check.py',job['direction'],job['filename']]
    if reply_check:
        return [*command, '/remote-tests/resolution_reply_check.py',job['direction'],job['filename'],reply_check]
    if partition:
        return [*command, '/remote-tests/partition_check.py', job['direction'], job['filename']]
    return [*command, '/remote-tests/'+('package_step.py' if packaged else 'run_controller.py'), job['direction'],
            '--state', '/controller-state/'+job['filename'], *job['flags']]


def respond(path, result):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x') as stream:
        json.dump(dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr), stream)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def run_scenario(repo, results, prefix, backend, btc, controller, mode, disconnect, packaged=False, lifecycle=False, stale=False, lost=False, drop_resolution_reply=False, packaged_recovery=False, owner_fence=False, drop_submission_reply=False, partial_fence=False):
    work = results/mode
    work.mkdir()
    shared = work/'exchange'; shared.mkdir()
    for name in ('control', 'jobs'):
        (shared/name).mkdir()
    node_results = work/'nodes'; node_results.mkdir()
    suffix = uuid.uuid4().hex[:12]
    network = 'cln-test-'+suffix
    node = 'cln-nodes-'+suffix
    child = 'cln-controller-'+suffix
    fixture = None
    created_network = False
    partition_done = False
    resolution_reply_done = False
    partial_fence_done = False
    jobs_seen = set()
    try:
        docker('network', 'create', '--internal', network)
        created_network = True
        docker('run', '-d', '--name', node, '--network', network, '--init',
            '-e', 'BTC_XBT_DISPOSABLE_CONTAINER=1', '-e', 'PYTHONDONTWRITEBYTECODE=1',
            '-e', 'LOST_JOURNAL_TEST='+('1' if lost else '0'),
            '-e', 'DROP_SUBMISSION_REPLY_TEST='+('1' if drop_submission_reply else '0'),
            '-e', 'OWNER_FENCE_TEST='+('1' if owner_fence else '0'),
            '-e', 'RECOVERY_DIRECTION='+('forward' if mode.startswith('forward') else 'reverse'),
            *mount(backend, '/test-bitcoind', True), *mount(prefix, '/opt/xbt', True),
            *mount(repo.parent/'btc-cln-startos/tests', '/pair-fixtures', True),
            *mount(repo/'assets', '/controller-assets', True),
            *mount(repo/'tests/remote', '/remote-tests', True),
            *mount(node_results, '/results'), *mount(shared, '/exchange'),
            '--entrypoint', '/usr/bin/python3', btc, '-c', 'import time; time.sleep(3600)')
        inspected = json.loads(docker('inspect', node))[0]
        address = str(ipaddress.IPv4Address(inspected['NetworkSettings']['Networks'][network]['IPAddress']))
        log = work/'fixture.log'
        with log.open('w') as output, log.open() as reader:
            fixture = subprocess.Popen(['docker', 'exec', '-e', 'COORDINATOR_IP='+address,
                node, '/usr/bin/python3', '/remote-tests/image_remote.py', mode,
                '--separate-controller'], stdout=output, stderr=subprocess.STDOUT)
            deadline = time.monotonic()+1200
            while fixture.poll() is None:
                sys.stdout.write(reader.read()); sys.stdout.flush()
                if time.monotonic() > deadline:
                    raise RuntimeError('fixture_timeout')
                for request in sorted((shared/'jobs').glob('*.request')):
                    if request.name in jobs_seen:
                        continue
                    if not re.fullmatch(r'[0-9a-f]{32}\.request', request.name) or request.is_symlink():
                        raise ValueError('invalid_job_path')
                    if request.stat().st_size > 4096:
                        raise ValueError('oversized_job')
                    try:
                        job = validate_job(json.loads(request.read_text()))
                    except PermissionError:
                        continue  # The atomic writer has not yet set metadata permissions.
                    jobs_seen.add(request.name)
                    if disconnect and not partition_done and job['phase'] == 'outgoing_started':
                        blocked = subprocess.run(controller_command(repo, shared, controller, 'none', child, job, True, packaged, lifecycle, stale, lost, packaged_recovery=packaged_recovery, owner_fence=owner_fence, drop_submission_reply=drop_submission_reply),
                            capture_output=True, text=True, timeout=25)
                        (work/'partition.log').write_text(blocked.stdout+blocked.stderr)
                        if blocked.returncode or blocked.stdout.strip() != '{"partition_preserved":true}':
                            raise RuntimeError('isolated_recovery_check_failed; inspect disposable container setup')
                        partition_done = True
                        print('PASS: controller network unavailable while payment pending; journal and RPC audit unchanged', flush=True)
                    result = subprocess.run(controller_command(repo, shared, controller, network, child, job, packaged=packaged, lifecycle=lifecycle, stale=stale, lost=lost, drop_resolution_reply=drop_resolution_reply, packaged_recovery=packaged_recovery, owner_fence=owner_fence, drop_submission_reply=drop_submission_reply),
                        capture_output=True, text=True, timeout=30)
                    if drop_resolution_reply and result.returncode==89:
                        if resolution_reply_done or job['flags'] or job['phase']!='outgoing_started':
                            raise RuntimeError('unexpected_resolution_crash')
                        for stage in ('before','after'):
                            checked=subprocess.run(controller_command(repo,shared,controller,'none',child,job,
                                packaged=True,lifecycle=True,lost=True,reply_check=stage,packaged_recovery=packaged_recovery, owner_fence=owner_fence, drop_submission_reply=drop_submission_reply),
                                capture_output=True,text=True,timeout=15)
                            (work/('resolution-reply-'+stage+'.log')).write_text(checked.stdout+checked.stderr)
                            if checked.returncode or checked.stdout.strip()!='{"lost_resolution_reply_verified":true}':
                                raise RuntimeError('resolution_reply_check_failed; inspect '+str(work))
                            if stage=='before':
                                # A distinct container/process starts with the intent receipt.
                                result=subprocess.run(controller_command(repo,shared,controller,network,child,job,
                                    packaged=True,lifecycle=True,lost=True,packaged_recovery=packaged_recovery, owner_fence=owner_fence, drop_submission_reply=drop_submission_reply),capture_output=True,text=True,timeout=30)
                                if result.returncode:
                                    (work/'resolution-recovery.log').write_text(result.stdout+result.stderr)
                                    raise RuntimeError('fresh_resolution_recovery_failed; inspect '+str(work))
                        resolution_reply_done=True
                        print('PASS: resolution reply discarded by process exit; fresh container reconciled terminal gate; audit unchanged; no repeated resolution',flush=True)
                    if owner_fence and result.returncode==88:
                        if partial_fence:
                            for stage in ('begin','resume'):
                                partial=subprocess.run(['docker','exec',node,'/usr/bin/python3','/remote-tests/partial_fence.py',stage],
                                    capture_output=True,text=True,timeout=15)
                                (work/('partial-fence-'+stage+'.log')).write_text(partial.stdout+partial.stderr)
                                if partial.returncode or partial.stdout.strip()!='{"partial_fence_stage_verified":true}':
                                    raise RuntimeError('partial_fence_stage_failed; inspect '+str(work))
                                if stage=='begin':
                                    blocked=subprocess.run(controller_command(repo,shared,controller,network,child,job,
                                        packaged=True,lifecycle=True,lost=True,packaged_recovery=True,owner_fence=True,partial_fence=True),
                                        capture_output=True,text=True,timeout=10)
                                    (work/'partial-recovery.log').write_text(blocked.stdout+blocked.stderr)
                                    if blocked.returncode or blocked.stdout.strip()!='{"partial_recovery_blocked":true}':
                                        raise RuntimeError('partial_recovery_not_blocked; inspect '+str(work))
                            print('PASS: first rune revoked while second admin socket unavailable; fresh replacement refused twice; recovery action retained first confirmation and blocked recovery',flush=True)
                        fenced=subprocess.run(['docker','exec',node,'/usr/bin/python3','/remote-tests/owner_fence.py'],
                            capture_output=True,text=True,timeout=25)
                        (work/'owner-fence.log').write_text(fenced.stdout+fenced.stderr)
                        if fenced.returncode or fenced.stdout.strip()!='{"coordinator_fence_verified":true}':
                            raise RuntimeError('coordinator_fence_failed; inspect '+str(work))
                        if partial_fence: partial_fence_done=True
                        if drop_submission_reply:
                            print('PASS: sendpay accepted but reply withheld from original controller before both coordinator revocations',flush=True)
                        print('PASS: both coordinators revoked original and derived runes before replacement recovery',flush=True)
                    respond(request.with_suffix('.response'), result)
                time.sleep(0.05)
            sys.stdout.write(reader.read()); sys.stdout.flush()
        if fixture.returncode != 0 or not jobs_seen:
            raise RuntimeError('separate_controller_fixture_failed; inspect '+str(log))
        if disconnect and not partition_done:
            raise RuntimeError('missing_partition_check')
        if drop_resolution_reply and not resolution_reply_done:
            raise RuntimeError('resolution_reply_crash_not_exercised')
        if partial_fence and not partial_fence_done:
            raise RuntimeError('partial_fence_not_exercised')
        if stale:
            report = json.loads(docker('run', '--rm', '--network', 'none',
                *mount(shared/'control', '/controller-state', True), '--entrypoint', 'python3', controller,
                '-c', "from pathlib import Path; print(Path('/controller-state/stale-inspection.json').read_text())"))
            expected = 'failed' if mode.endswith('failure') else 'complete'
            assert set(report['observed_statuses']) == {'pending', expected}
            report = report['reports']
            assert len(report) == 2 and {r['stale_phase'] for r in report} == {'prepared', 'outgoing_started'}
            assert all(r['outgoing_status'] == expected and r['restored_block'] and not r['execution_authorized'] for r in report)
            print('PASS: stale prepared and submitted snapshots inspected original outcome; both remained blocked; original journal alone settled', flush=True)
        if lost:
            report=json.loads(docker('run','--rm','--network','none',
                *mount(shared/'control','/controller-state',True),'--entrypoint','python3',controller,
                '-c', "from pathlib import Path; assert not Path('/controller-state/execution').exists(); print(Path('/controller-state/lost-result.json').read_text())"))
            phase=('btc_' if mode.startswith('forward') else 'xbt_')+('failed' if mode.endswith('failure') else 'released')
            assert report==dict(phase=phase,original_journal_absent=True,stale_phase='prepared',restored_block=True)
            print('PASS: original executor journal deleted; stale prepared record reconciled and resolved original HTLC; restore block retained; recovery runes reject sendpay',flush=True)
        if owner_fence:
            proof=json.loads(docker('run','--rm','--network','none',
                *mount(shared/'control','/controller-state',True),'--entrypoint','python3',controller,
                '-c', "import json; from pathlib import Path; p=Path('/controller-state'); print(json.dumps({'old':json.loads((p/'old-owner-check.json').read_text()),'restarts':json.loads((p/'fence-restarts.json').read_text())}))"))
            assert proof['restarts']=={'networks':['regtest','xbt-regtest']}
            assert proof['old']['checks']>=3 and proof['old']['retained_journal_blocked'] is True
            print('PASS: revocations survived both restarts; retained old executor refused before RPC mutation; unrelated credentials still work',flush=True)
        if packaged_recovery: print('PASS: packaged recovery action ran in a fresh process; restore barrier retained',flush=True)
        print('Separate controller container OK ('+mode+('; recovery outage' if disconnect else '')+')', flush=True)
    finally:
        for name in (child, node):
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=30)
        if fixture is not None:
            try: fixture.wait(timeout=10)
            except subprocess.TimeoutExpired:
                fixture.kill(); fixture.wait()
        if created_network:
            subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('btc_image'); parser.add_argument('xbt_image')
    parser.add_argument('controller_image'); parser.add_argument('bitcoind', type=Path)
    parser.add_argument('mode', nargs='?', default='all', choices=('all', *MODES))
    parser.add_argument('--disconnect-recovery', action='store_true')
    parser.add_argument('--packaged-executor', action='store_true')
    parser.add_argument('--lifecycle-worker', action='store_true')
    parser.add_argument('--stale-restore', action='store_true')
    parser.add_argument('--lost-journal', action='store_true')
    parser.add_argument('--drop-resolution-reply', action='store_true')
    parser.add_argument('--packaged-recovery', action='store_true')
    parser.add_argument('--owner-fence', action='store_true')
    parser.add_argument('--drop-submission-reply', action='store_true')
    parser.add_argument('--partial-fence', action='store_true')
    args = parser.parse_args()
    if args.partial_fence and not args.owner_fence: parser.error('--partial-fence requires --owner-fence')
    if args.drop_submission_reply and not args.owner_fence: parser.error('--drop-submission-reply requires --owner-fence')
    if args.owner_fence and not args.packaged_recovery: parser.error('--owner-fence requires --packaged-recovery')
    if args.packaged_recovery and not args.lost_journal: parser.error('--packaged-recovery requires --lost-journal')
    if args.drop_resolution_reply and not args.lost_journal: parser.error('--drop-resolution-reply requires --lost-journal')
    if args.lost_journal and (not args.lifecycle_worker or args.stale_restore): parser.error('--lost-journal requires --lifecycle-worker and excludes --stale-restore')
    if args.stale_restore and not args.lifecycle_worker: parser.error('--stale-restore requires --lifecycle-worker')
    if args.lifecycle_worker and not args.packaged_executor: parser.error('--lifecycle-worker requires --packaged-executor')
    os.umask(0o077)
    repo = Path(__file__).resolve().parents[1]
    if not (repo.parent/'btc-cln-startos/tests/image_pair.py').is_file():
        parser.error('btc-cln-startos sibling checkout is required')
    backend = args.bitcoind.resolve()
    if not backend.is_file() or not os.access(backend, os.X_OK):
        parser.error('executable regtest bitcoind required')
    btc, xbt, controller = [image_id(name) for name in (args.btc_image, args.xbt_image, args.controller_image)]
    results = Path(tempfile.mkdtemp(prefix='cln-separated-'))
    print('Disposable logs: '+str(results), flush=True)
    print('BTC image: '+btc+'\nXBT image: '+xbt+'\nController image: '+controller, flush=True)
    with tempfile.TemporaryDirectory(prefix='cln-separated-binaries-') as temporary:
        prefix = Path(temporary)
        container = docker('create', '--network', 'none', xbt)
        try: docker('cp', container+':/usr/local/.', str(prefix)+'/')
        finally: docker('rm', container)
        for mode in MODES if args.mode == 'all' else (args.mode,):
            run_scenario(repo, results, prefix, backend, btc, controller, mode, args.disconnect_recovery, args.packaged_executor, args.lifecycle_worker, args.stale_restore, args.lost_journal, args.drop_resolution_reply, args.packaged_recovery, args.owner_fence, args.drop_submission_reply, args.partial_fence)


if __name__ == '__main__': main()
