"""Fixture-only mailbox to the host Docker launcher, not a controller RPC path.

Only the single controller journal is copied. Node wallets and sockets stay in
the node container. Calls are serialized; copied/concurrent production journals
are explicitly outside this fixture's scope.
"""
import json
import os
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, '/controller-assets')
from controller import private_load, save

EXCHANGE = Path('/exchange')

def main():
    if os.environ.get('SEPARATE_CONTROLLER') != '1':
        raise ValueError('separate_fixture_required')
    direction, *args = sys.argv[1:]
    if direction not in ('forward', 'reverse') or args[:1] != ['--state']:
        raise ValueError('invalid_fixture_command')
    source = Path(args[1])
    flags = args[2:]
    if any(f not in ('--crash-after-sendpay', '--crash-after-btc', '--crash-after-xbt-resolution') for f in flags):
        raise ValueError('invalid_fixture_flags')
    before = source.read_bytes()
    state = private_load(source)
    filename = 'swap-state.json' if direction == 'forward' else 'reverse-state.json'
    destination = EXCHANGE/'control'/filename
    save(destination, state)
    token = uuid.uuid4().hex
    request = EXCHANGE/'jobs'/(token+'.request')
    response = request.with_suffix('.response')
    save(request, dict(direction=direction, flags=flags, filename=filename, phase=state['phase']))
    # Metadata only: the host reads this mailbox. Its parent is private on the
    # host; payment secrets and credentials remain in mode-0600 control files.
    request.chmod(0o644)
    deadline = time.monotonic()+50
    while not response.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('container_controller_timeout')
        time.sleep(0.05)
    result = private_load(response)
    if source.read_bytes() != before:
        raise RuntimeError('fixture_journal_changed_concurrently')
    save(source, private_load(destination))
    # Private child output is returned to the original fixture assertions only.
    sys.stdout.write(result['stdout'])
    sys.stderr.write(result['stderr'])
    return result['returncode']


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception:
        print('{"event":"separate_controller_bridge_failed","details":"withheld"}')
        raise SystemExit(1) from None
