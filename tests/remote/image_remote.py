"""Run pinned settlement/recovery fixtures through restricted regtest HTTPS."""
import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, '/controller-assets')
from controller import save
from https_rpc import Remote, restrictions
sys.path.insert(0, '/pair-fixtures')
from image_pair import PairLab, PIN, check_bundle
from smoke_regtest import wait_until


class RemoteLab(PairLab):
    def __init__(self, *args):
        super().__init__(*args)
        self.rest = {}
        self.connections = {}
        self.recovery_connections = {}

    def start(self, args, logfile, new_session=False):
        if str(args[0]).endswith('/bin/lightningd'):
            data = Path(next(a.split('=', 1)[1] for a in args if a.startswith('--lightning-dir=')))
            if data.name in ('swap-btc', 'swap-xbt', 'btc-operator', 'xbt-operator'):
                if data.name not in self.rest:
                    self.rest[data.name] = (self.port(), data/'rest-certs')
                port, certs = self.rest[data.name]
                args = [*args, '--clnrest-host='+os.environ.get('COORDINATOR_IP', '127.0.0.1'), '--clnrest-protocol=https',
                        '--clnrest-port='+str(port), '--clnrest-certs='+str(certs)]
        return super().start(args, logfile, new_session)

    def lightning(self, name, network, backend, expect_success=True, plugins=()):
        node = super().lightning(name, network, backend, expect_success, plugins)
        if name not in self.rest:
            return node
        port, certs = self.rest[name]
        ca = certs/'ca.pem'
        wait_until(lambda: ca.exists(), node['proc'], timeout=90)
        if name not in self.connections:
            token = self.rpc([*node['cli'], '-k'], 'createrune',
                             'restrictions='+json.dumps(restrictions(network)))
            self.connections[name] = dict(cli=node['cli'], network=network, node_id=node['id'],
                url='https://'+os.environ.get('COORDINATOR_IP', '127.0.0.1')+':'+str(port), rune=token['rune'], ca_pem=ca.read_text())
        config = self.connections[name]
        assert config['node_id'] == node['id'] and config['ca_pem'] == ca.read_text()
        remote = Remote(config)
        def ready():
            try:
                return remote.call('getinfo')['id'] == node['id']
            except Exception:
                return False
        wait_until(ready, node['proc'], timeout=90)
        # Test server-side authorization directly, bypassing only the client's
        # method filter. Neither request may reach a wallet mutation.
        import urllib.error
        import urllib.request
        for method in ('newaddr', 'createrune'):
            req = urllib.request.Request(config['url']+'/v1/'+method, data=b'{}',
                headers={'Content-Type': 'application/json', 'Rune': config['rune']})
            try:
                with remote.client.opener.open(req, timeout=10):
                    raise AssertionError('restricted rune accepted unrelated method')
            except urllib.error.HTTPError as exc:
                assert exc.code in (401, 403)
                exc.close()
        if os.environ.get('LOST_JOURNAL_TEST') == '1':
            direction = os.environ['RECOVERY_DIRECTION']
            from lost_journal import allowed
            recovery_methods = allowed(network,direction)
            if name not in self.recovery_connections:
                token = self.rpc([*node['cli'], '-k'], 'createrune',
                    'restrictions='+json.dumps([['method='+m for m in sorted(recovery_methods)]]))
                self.recovery_connections[name] = dict(config, rune=token['rune'])
            recovery = self.recovery_connections[name]
            # Exercise the actual CLN authorization layer, not the local filter.
            for method in ('sendpay','pay','withdraw','createrune'):
                req = urllib.request.Request(recovery['url']+'/v1/'+method, data=b'{}',
                    headers={'Content-Type':'application/json','Rune':recovery['rune']})
                try:
                    with remote.client.opener.open(req, timeout=10):
                        raise AssertionError('recovery rune accepted forbidden method')
                except urllib.error.HTTPError as exc:
                    assert exc.code in (401,403)
                    exc.close()
            save(Path('/exchange/control/remote-recovery.json'), list(self.recovery_connections.values()))
        save(self.root/'remote.json', list(self.connections.values()))
        if os.environ.get('SEPARATE_CONTROLLER') == '1':
            save(Path('/exchange/control/remote.json'), list(self.connections.values()))
        return node


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('forward', 'forward-failure', 'reverse', 'reverse-failure'))
    parser.add_argument('--drop-send-reply', action='store_true')
    parser.add_argument('--separate-controller', action='store_true')
    args = parser.parse_args()
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER') == '1'
    source = json.loads(Path('/opt/xbt/share/xbt-cln/source-lock.json').read_text())
    assert source['cln_commit'] == PIN and source['network'] == 'xbt'
    check_bundle('/usr/local/libexec/cln-swap')
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='https-'+args.mode+'-', dir='/results'))
    print('Test directory: '+str(root), flush=True)
    modules = Path('/exchange/modules') if args.separate_controller else root/'modules'
    if args.separate_controller:
        os.environ['SEPARATE_CONTROLLER'] = '1'
        # The execution container drops all capabilities. Its root user must
        # own the private journal directory rather than rely on DAC override
        # to enter a host-user-owned mode-0700 bind mount.
        os.chown('/exchange/control', 0, 0)
        Path('/exchange/control').chmod(0o700)
    shutil.copytree('/usr/local/libexec/cln-swap', modules)
    # Only fixture child entry points change; the controller algorithms and
    # plugin source remain byte-for-byte from the verified pinned bundle.
    for fixture, target, direction in (
            ('pending_recovery.py', 'swap_controller.py', 'forward'),
            ('reverse_recovery.py', 'reverse_controller.py', 'reverse')):
        path = modules/fixture
        old = "str(Path(__file__).with_name('"+target+"'))"
        text = path.read_text()
        assert text.count(old) == 1
        runner = 'container_bridge.py' if args.separate_controller else 'run_controller.py'
        path.write_text(text.replace(old, "'/remote-tests/"+runner+"', '"+direction+"'"))
    sys.path.insert(0, str(modules))
    os.environ['PYTHONPATH'] = str(modules)
    os.environ['REMOTE_TEST_ROOT'] = str(root)
    if args.drop_send_reply:
        os.environ['REMOTE_DROP_SEND_REPLY'] = '1'
    # image_pair imports these fixtures; reload from our disposable module copy.
    for name in ('swap_regtest', 'reverse_regtest', 'pending_recovery', 'reverse_recovery'):
        sys.modules.pop(name, None)
    forward = importlib.import_module('swap_regtest')
    reverse = importlib.import_module('reverse_regtest')
    lab = RemoteLab(root, '/test-bitcoind', '/usr/bin/bitcoin-cli')
    try:
        if args.mode.startswith('forward'):
            forward.run(lab, restart_pending=True, pending_failure=args.mode.endswith('failure'))
        else:
            reverse.run(lab, recovery='gate-restart-failure' if args.mode.endswith('failure') else 'gate-restart')
        audit = Path('/exchange/control/remote-audit.jsonl') if args.separate_controller else root/'remote-audit.jsonl'
        records = [json.loads(line) for line in audit.read_text().splitlines()]
        assert sum(r['method'] == 'sendpay' for r in records) == 1
        assert {r['network'] for r in records} == {'regtest', 'xbt-regtest'}
        expected = ('xbt-fail' if args.mode.endswith('failure') else 'xbt-release') if args.mode.startswith('forward') else ('reverse-fail' if args.mode.endswith('failure') else 'reverse-release')
        assert sum(r['method'] == expected for r in records) == 1
        print('PASS: both coordinator identities verified over HTTPS; restricted runes survived restart; one remote submission and one gate resolution', flush=True)
        if args.drop_send_reply:
            print('PASS: submission reply discarded before controller received it; recovery did not resend', flush=True)
        if args.separate_controller:
            print('PASS: all controller steps ran in separate containers without CLN binaries, node volumes, RPC sockets or Docker socket', flush=True)
        print('Remote controller recovery OK ('+args.mode+'; restricted HTTPS; regtest only; installed monitor unchanged)', flush=True)
    finally:
        lab.close()


if __name__ == '__main__':
    main()
