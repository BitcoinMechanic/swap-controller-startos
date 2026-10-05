"""Disposable controller child: all coordinator RPCs use HTTPS; no CLI fallback."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys

sys.path.insert(0, '/controller-assets')
from controller import private_load
from https_rpc import Remote


def main():
    if os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER') != '1':
        raise ValueError('disposable_container_required')
    direction = sys.argv[1]
    if direction not in ('forward', 'reverse'):
        raise ValueError('invalid_direction')
    root = Path(os.environ['REMOTE_TEST_ROOT'])
    config = private_load(root/'remote.json')
    clients = {tuple(item['cli']): Remote(item) for item in config}
    if len(clients) != 2 or {c.network for c in clients.values()} != {'regtest', 'xbt-regtest'}:
        raise ValueError('two_regtest_operators_required')
    # Both identities must be checked before the controller gets any authority.
    for client in clients.values():
        client.call('getinfo')

    def call(cli, *command):
        named = bool(cli and cli[-1] == '-k')
        key = tuple(cli[:-1] if named else cli)
        if key not in clients or not command:
            raise ValueError('unknown_rpc_target')
        method, *args = command
        result = clients[key].call(method, *args, named=named)
        with (root/'remote-audit.jsonl').open('a') as stream:
            stream.write(json.dumps({'network': clients[key].network, 'method': method})+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        if method == 'sendpay' and os.environ.get('REMOTE_DROP_SEND_REPLY') == '1':
            # Remote accepted submission, but the controller never receives the
            # reply. Its pre-submission checkpoint must prevent a second send.
            os._exit(88)
        return result

    sys.path.insert(0, str(root/'modules'))
    import swap_rpc
    swap_rpc.RPC.call = staticmethod(call)
    def no_local_commands(*args, **kwargs):
        raise RuntimeError('controller_local_process_execution_forbidden')
    subprocess.run = no_local_commands
    subprocess.Popen = no_local_commands
    script = root/'modules'/('swap_controller.py' if direction == 'forward' else 'reverse_controller.py')
    sys.argv = [str(script), *sys.argv[2:]]
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"event":"remote_controller_failed","details":"withheld"}', flush=True)
        raise SystemExit(1) from None
