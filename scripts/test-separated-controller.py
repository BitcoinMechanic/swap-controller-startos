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


def controller_command(repo, shared, image, network, name, job, partition=False):
    validate_job(job)
    command = ['docker', 'run', '--rm', '--name', name, '--network', network,
               '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=16m',
               '-e', 'BTC_XBT_DISPOSABLE_CONTAINER=1', '-e', 'PYTHONDONTWRITEBYTECODE=1',
               '-e', 'SEPARATE_CONTROLLER=1', '-e', 'REMOTE_TEST_ROOT=/controller-state',
               '-e', 'REMOTE_MODULES_DIR=/pinned',
               *mount(shared/'control', '/controller-state'),
               *mount(shared/'modules', '/pinned', True),
               *mount(repo/'assets', '/controller-assets', True),
               *mount(repo/'tests/remote', '/remote-tests', True),
               '--entrypoint', 'python3', image]
    if partition:
        return [*command, '/remote-tests/partition_check.py', job['direction'], job['filename']]
    return [*command, '/remote-tests/run_controller.py', job['direction'],
            '--state', '/controller-state/'+job['filename'], *job['flags']]


def respond(path, result):
    temporary = path.with_suffix('.tmp')
    with temporary.open('x') as stream:
        json.dump(dict(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr), stream)
        stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)


def run_scenario(repo, results, prefix, backend, btc, controller, mode, disconnect):
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
    jobs_seen = set()
    try:
        docker('network', 'create', '--internal', network)
        created_network = True
        docker('run', '-d', '--name', node, '--network', network, '--init',
            '-e', 'BTC_XBT_DISPOSABLE_CONTAINER=1', '-e', 'PYTHONDONTWRITEBYTECODE=1',
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
                        blocked = subprocess.run(controller_command(repo, shared, controller, 'none', child, job, True),
                            capture_output=True, text=True, timeout=25)
                        (work/'partition.log').write_text(blocked.stdout+blocked.stderr)
                        if blocked.returncode or blocked.stdout.strip() != '{"partition_preserved":true}':
                            raise RuntimeError('isolated_recovery_check_failed; inspect disposable container setup')
                        partition_done = True
                        print('PASS: controller network unavailable while payment pending; journal and RPC audit unchanged', flush=True)
                    result = subprocess.run(controller_command(repo, shared, controller, network, child, job),
                        capture_output=True, text=True, timeout=30)
                    respond(request.with_suffix('.response'), result)
                time.sleep(0.05)
            sys.stdout.write(reader.read()); sys.stdout.flush()
        if fixture.returncode != 0 or not jobs_seen:
            raise RuntimeError('separate_controller_fixture_failed; inspect '+str(log))
        if disconnect and not partition_done:
            raise RuntimeError('missing_partition_check')
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
    args = parser.parse_args()
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
            run_scenario(repo, results, prefix, backend, btc, controller, mode, args.disconnect_recovery)


if __name__ == '__main__': main()
