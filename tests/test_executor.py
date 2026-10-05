import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'assets'))
import executor as e
from controller import save, private_load


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, BTC_XBT_DISPOSABLE_CONTAINER='1'); self.env.start(); self.addCleanup(self.env.stop)
        self.connections = [dict(network=network, node_id='02'+str(i)*64, rune='test', ca_pem=None,
                                 url='https://localhost:'+str(9000+i), cli=['cli', '--network='+network])
                            for i, network in enumerate(('regtest', 'xbt-regtest'), 1)]
        self.state = dict(phase='prepared', payment_hash='12'*32, route=[{'amount_msat': 1000}],
                          btc_cli=self.connections[0]['cli'], xbt_cli=self.connections[1]['cli'])
        self.token = e.prepare(self.root, 'forward', self.state, self.connections)
        self.calls = 0

    def permit(self): e.authorize(self.root, self.token, 1100, confirmed=True, now=1000)

    def submitted(self, root, direction, flags):
        self.calls += 1
        value=private_load(root/'state.json'); value['phase']='outgoing_started'; save(root/'state.json', value)
        return subprocess.CompletedProcess([], 88, '', '')

    def test_prepare_repeat_and_changed_amount(self):
        self.assertEqual(e.prepare(self.root, 'forward', self.state, self.connections), self.token)
        changed=copy.deepcopy(self.state); changed['route'][0]['amount_msat']+=1
        with self.assertRaises(ValueError): e.prepare(self.root, 'forward', changed, self.connections)
        self.assertFalse((self.root/'launched.json').exists())

    def test_no_authority_and_recovery_only_never_launch(self):
        with self.assertRaises(FileNotFoundError): e.step(self.root, runner=self.submitted)
        self.permit()
        with self.assertRaises(ValueError): e.step(self.root, recover_only=True, runner=self.submitted)
        self.assertEqual(self.calls, 0)

    def test_exact_authorization_and_expiry(self):
        for digest, confirmed in [('0'*64, True), (self.token, False)]:
            with self.assertRaises(ValueError): e.authorize(self.root, digest, 1100, confirmed, now=1000)
        self.permit()
        with self.assertRaises(ValueError): e.step(self.root, runner=self.submitted, now=1100)
        self.assertEqual(self.calls, 0)

    def test_restart_recovers_after_permit_removed(self):
        self.permit(); self.assertEqual(e.step(self.root, runner=self.submitted, now=1001).returncode, 88)
        (self.root/'permit.json').unlink()
        def pending(root, direction, flags):
            self.assertEqual(private_load(root/'state.json')['phase'], 'outgoing_started')
            return subprocess.CompletedProcess([], 0, '{"outcome":"pending"}', '')
        e.step(self.root, recover_only=True, runner=pending, now=2000)
        self.assertEqual(self.calls, 1)

    def test_crash_before_child_checkpoint_never_relaunches(self):
        self.permit()
        def crash(*args): raise OSError('private detail')
        with self.assertRaises(RuntimeError): e.step(self.root, runner=crash, now=1001)
        with self.assertRaisesRegex(ValueError, 'launch_outcome_unknown'):
            e.step(self.root, runner=self.submitted, now=1002)
        self.assertEqual(self.calls, 0)

    def test_changed_connection_and_state_refused_before_child(self):
        self.permit()
        changed=copy.deepcopy(self.connections); changed[0]['rune']='replacement'
        save(self.root/'remote.json', changed)
        with self.assertRaises(ValueError): e.step(self.root, runner=self.submitted, now=1001)
        save(self.root/'remote.json', self.connections)
        changed=copy.deepcopy(self.state); changed['route'][0]['amount_msat']+=1
        save(self.root/'state.json', changed)
        with self.assertRaises(ValueError): e.step(self.root, runner=self.submitted, now=1001)
        self.assertEqual(self.calls, 0)

    def test_competing_worker_cannot_launch(self):
        self.permit()
        with e.lock(self.root):
            with self.assertRaises(BlockingIOError): e.step(self.root, runner=self.submitted, now=1001)
        self.assertEqual(self.calls, 0)

    def test_terminal_repeat_has_no_rpc(self):
        self.permit(); e.step(self.root, runner=self.submitted, now=1001)
        state=private_load(self.root/'state.json'); state['phase']='btc_failed'; save(self.root/'state.json', state)
        e.step(self.root, runner=self.submitted)
        self.assertEqual(self.calls, 1)
        self.assertNotIn('payment_hash', e.status(self.root))

    def test_live_network_and_cli_substitution_refused(self):
        for network in ('bitcoin', 'xbt'):
            changed=copy.deepcopy(self.connections); changed[0]['network']=network
            with self.assertRaises(ValueError): e.validate('forward', self.state, changed)
        changed=copy.deepcopy(self.state); changed['btc_cli']=['other', '--network=regtest']
        with self.assertRaises(ValueError): e.validate('forward', changed, self.connections)

    def test_worker_continues_after_bad_job(self):
        (self.root/'bad').mkdir(); (self.root/'good').mkdir()
        calls=[]
        def step(root):
            calls.append(root.name)
            if root.name=='bad': raise RuntimeError()
        with patch.object(e, 'step', step), patch('builtins.print'): e.cycle(self.root)
        self.assertEqual(calls, ['bad', 'good'])

    def test_partial_import_cannot_authorize(self):
        (self.root/'intent.json').unlink()
        with self.assertRaises(ValueError): e.prepare(self.root, 'forward', self.state, self.connections)
        with self.assertRaises(FileNotFoundError): self.permit()

    def test_packaged_transport_matches_tested_transport(self):
        root=Path(__file__).resolve().parents[1]
        source=(root/'assets/execution_rpc.py').read_text().split('import json',1)[1]
        tested=(root/'tests/remote/https_rpc.py').read_text().split('import json',1)[1]
        self.assertEqual(source, tested)


if __name__ == '__main__': unittest.main()
