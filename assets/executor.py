"""Durable regtest executor. Separate from the installed read-only monitor."""
import argparse
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from controller import private_load, save, require
from execution_rpc import Remote

PIN = '81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe'
TERMINAL = {'forward': {'btc_released', 'btc_failed'}, 'reverse': {'xbt_released', 'xbt_failed'}}
PHASES = {'prepared', 'outgoing_started', 'btc_paid', 'btc_failed', 'xbt_paid', 'xbt_failed', 'btc_released', 'xbt_released'}
FLAGS = {'--crash-after-sendpay', '--crash-after-btc', '--crash-after-xbt-resolution'}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def guard():
    require(os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER') == '1', 'regtest_optin_required')


@contextlib.contextmanager
def lock(root):
    require(root.is_dir() and not root.is_symlink(), 'invalid_execution_directory')
    lifecycle_fd = None
    if root.parent.name == 'jobs':
        lifecycle_fd = os.open(root.parent.parent/'lifecycle.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
        try: fcntl.flock(lifecycle_fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            os.close(lifecycle_fd)
            raise
    try:
        fd = os.open(root/'executor.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally: os.close(fd)
    finally:
        if lifecycle_fd is not None: os.close(lifecycle_fd)


def execution_allowed(root):
    if root.parent.name == 'jobs':
        parent=root.parent.parent
        require(not os.path.lexists(parent/'restored.json'), 'restored_execution_blocked')
        require(not os.path.lexists(parent/'backup-paused.json'), 'backup_in_progress')


def validate(direction, state, connections):
    require(direction in TERMINAL and state.get('phase') == 'prepared' and 'preimage' not in state, 'prepared_swap_required')
    require(re.fullmatch('[0-9a-f]{64}', state.get('payment_hash', '')), 'invalid_payment_hash')
    require(isinstance(connections, list) and len(connections) == 2, 'two_regtest_nodes_required')
    by_network = {}
    for c in connections:
        Remote(c)  # Validates TLS configuration, identity and regtest network; no RPC.
        require(c['network'] not in by_network, 'distinct_networks_required')
        by_network[c['network']] = c
    require(set(by_network) == {'regtest', 'xbt-regtest'}, 'regtest_networks_required')
    for role, network in (('btc', 'regtest'), ('xbt', 'xbt-regtest')):
        cli = state.get(role+'_cli')
        require(isinstance(cli, list) and all(isinstance(v, str) for v in cli), 'invalid_node_binding')
        require(cli == by_network[network]['cli'] and '--network='+network in cli, 'node_binding_mismatch')
    require(by_network['regtest']['node_id'] != by_network['xbt-regtest']['node_id'], 'distinct_nodes_required')


def prepare(root, direction, state, connections):
    guard()
    validate(direction, state, connections)
    initial = dict(schema=1, source_commit=PIN, direction=direction, state=state, connections=connections)
    with lock(root):
        execution_allowed(root)
        if (root/'intent.json').exists():
            require(private_load(root/'intent.json') == initial, 'existing_intent_changed')
            return digest(initial)
        # Intent is committed last. An interrupted import cannot become executable.
        require(not any((root/n).exists() for n in ('state.json', 'remote.json', 'permit.json', 'launched.json')), 'incomplete_import_requires_inspection')
        save(root/'state.json', state)
        save(root/'remote.json', connections)
        save(root/'intent.json', initial)
        return digest(initial)


def records(root):
    initial = private_load(root/'intent.json')
    require(initial.get('schema') == 1 and initial.get('source_commit') == PIN, 'invalid_intent')
    validate(initial['direction'], initial['state'], initial['connections'])
    require(private_load(root/'remote.json') == initial['connections'], 'changed_connections')
    state = private_load(root/'state.json')
    require(state.get('phase') in PHASES, 'invalid_phase')
    # These fixtures only allow phase and recovered preimage to change. Advanced
    # deadline/on-chain profiles need their own reviewed mutable-field policy.
    def binding(value):
        value = {k:v for k,v in value.items() if k not in ('phase', 'preimage')}
        if initial['direction'] == 'reverse': value.setdefault('btc_payment_metadata', None)
        return value
    require(binding(state) == binding(initial['state']), 'changed_swap_binding')
    return initial, state


def authorize(root, expected_digest, expires_at, confirmed=False, now=None):
    guard()
    now = int(time.time()) if now is None else now
    require(confirmed is True, 'confirmation_required')
    require(type(expires_at) is int and now < expires_at <= now+3600, 'invalid_authorization_expiry')
    with lock(root):
        execution_allowed(root)
        initial, state = records(root)
        require(state['phase'] == 'prepared' and not (root/'launched.json').exists(), 'already_started')
        require(digest(initial) == expected_digest, 'authorization_digest_mismatch')
        permit = dict(digest=expected_digest, expires_at=expires_at)
        if (root/'permit.json').exists():
            require(private_load(root/'permit.json') == permit, 'authorization_already_recorded')
        else:
            save(root/'permit.json', permit)


def child(root, direction, flags):
    require(Path('/opt/swap/SOURCE_COMMIT').read_text().strip() == PIN, 'source_pin_mismatch')
    env = dict(os.environ, REMOTE_TEST_ROOT=str(root), PYTHONDONTWRITEBYTECODE='1')
    env.pop('REMOTE_DROP_SEND_REPLY', None)
    if os.environ.get('DROP_SUBMISSION_REPLY_TEST') == '1' and '--crash-after-sendpay' in flags:
        guard()
        require(os.environ.get('OWNER_FENCE_TEST') == '1', 'owner_fence_fixture_required')
        env['REMOTE_DROP_SEND_REPLY'] = '1'
    return subprocess.run([sys.executable, '/app/execution_child.py', direction, '--state', str(root/'state.json'), *flags],
                          env=env, capture_output=True, text=True, timeout=25)


def terminal_result(direction, state):
    if direction == 'forward':
        if state['phase'] == 'btc_failed': return dict(phase='btc_failed', outcome='failed')
        return dict(phase='btc_released', payment_preimage=state['preimage'])
    return dict(phase=state['phase'])


def step(root, recover_only=False, flags=(), runner=child, now=None):
    guard()
    require(len(flags) <= 1 and all(f in FLAGS for f in flags), 'invalid_fixture_flags')
    with lock(root):
        execution_allowed(root)
        require(not os.path.lexists(root/'deadline.json'), 'post_close_supervisor_required')
        initial, state = records(root)
        direction = initial['direction']
        if state['phase'] != 'prepared':
            require(private_load(root/'launched.json') == dict(digest=digest(initial)), 'missing_launch_binding')
        if state['phase'] in TERMINAL[direction]:
            return subprocess.CompletedProcess([], 0, json.dumps(terminal_result(direction, state)), '')
        if state['phase'] == 'prepared':
            require(not recover_only, 'prepared_requires_authorized_start')
            require(not (root/'launched.json').exists(), 'launch_outcome_unknown')
            permit = private_load(root/'permit.json')
            now = int(time.time()) if now is None else now
            require(permit['digest'] == digest(initial) and now < permit['expires_at'], 'authorization_missing_changed_or_expired')
            require(state == initial['state'], 'prepared_state_changed')
            # Persist before child launch: a crash before its own send checkpoint
            # must never be treated as permission to repeat the launch.
            save(root/'launched.json', dict(digest=digest(initial)))
        else:
            require(private_load(root/'launched.json') == dict(digest=digest(initial)), 'missing_launch_binding')
        try:
            result = runner(root, direction, flags)
        except (subprocess.SubprocessError, OSError):
            save(root/'report.json', dict(outcome='needs_reconciliation'))
            raise RuntimeError('execution_outcome_unknown') from None
        _, current = records(root)
        save(root/'report.json', dict(phase=current['phase'], outcome='reconciled' if result.returncode == 0 else 'needs_reconciliation'))
        return result


def status(root):
    with lock(root):
        initial, state = records(root)
        return dict(regtest_only=True, live_payment_enabled=False, direction=initial['direction'], phase=state['phase'],
                    terminal=state['phase'] in TERMINAL[initial['direction']])


def cycle(root):
    # Preparation/import uses fixed job names. A bad job cannot prevent recovery
    # of the other jobs; all reports are deliberately privacy-filtered.
    for job in sorted(root.iterdir()):
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}', job.name) or job.is_symlink() or not job.is_dir(): continue
        try:
            step(job)
        except Exception:
            print(json.dumps(dict(event='execution_needs_attention', job=job.name)), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('mode', choices=('prepare', 'authorize', 'step', 'recover', 'status', 'run'))
    parser.add_argument('--digest'); parser.add_argument('--expires-at', type=int)
    parser.add_argument('--confirm', action='store_true')
    args = parser.parse_args()
    guard()
    root = args.root.resolve()
    if args.mode == 'run':
        while True:
            cycle(root); time.sleep(5)
    if args.mode == 'prepare':
        request=json.loads(sys.stdin.read(262145))
        result=dict(digest=prepare(root, request['direction'], request['state'], request['connections']), payment_started=False)
    elif args.mode == 'authorize':
        authorize(root, args.digest, args.expires_at, args.confirm)
        result=dict(authorized=True, payment_started=False)
    elif args.mode in ('step', 'recover'):
        completed=step(root, recover_only=args.mode == 'recover')
        result=status(root)
        result['reconciled']=completed.returncode == 0
    else: result=status(root)
    print(json.dumps(result))


if __name__ == '__main__':
    try: main()
    except Exception:
        print('{"event":"execution_refused_or_uncertain","details":"withheld","live_payment_enabled":false}')
        raise SystemExit(1) from None
