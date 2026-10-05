"""Assert an isolated recovery attempt cannot change the pending journal."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def isolation():
    for command in ('lightning-cli', 'lightningd', 'bitcoin-cli', 'docker'):
        if shutil.which(command):
            raise RuntimeError('unexpected_node_or_runtime_binary')
    for path in ('/root/.lightning', '/opt/xbt', '/results', '/var/run/docker.sock'):
        if Path(path).exists():
            raise RuntimeError('unexpected_node_mount')
    if any(Path('/controller-state').rglob('lightning-rpc')):
        raise RuntimeError('unexpected_rpc_socket')


def main():
    isolation()
    direction, filename = sys.argv[1:]
    if direction not in ('forward', 'reverse') or filename not in ('swap-state.json', 'reverse-state.json'):
        raise ValueError('invalid_fixture_command')
    state = Path('/controller-state')/filename
    audit = Path('/controller-state/remote-audit.jsonl')
    before = state.read_bytes()
    before_audit = audit.read_bytes() if audit.exists() else b''
    if json.loads(before)['phase'] != 'outgoing_started':
        raise ValueError('pending_journal_required')
    runner = '/remote-tests/package_step.py' if os.environ.get('PACKAGED_EXECUTOR') == '1' else '/remote-tests/run_controller.py'
    result = subprocess.run([sys.executable, runner, direction,
                             '--state', str(state)], capture_output=True, timeout=20)
    if result.returncode != 1 or state.read_bytes() != before:
        raise RuntimeError('partition_mutated_journal_or_was_not_detected')
    if (audit.read_bytes() if audit.exists() else b'') != before_audit:
        raise RuntimeError('partition_permitted_rpc')
    print('{"partition_preserved":true}')


if __name__ == '__main__': main()
