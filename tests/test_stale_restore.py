import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'assets'), str(ROOT/'tests/remote')]
import executor
import lifecycle
import stale_restore as stale
from controller import save, private_load


class StaleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name)
        self.env = patch.dict(os.environ, BTC_XBT_DISPOSABLE_CONTAINER='1'); self.env.start(); self.addCleanup(self.env.stop)
        self.preimage = 'ab'*32
        self.hash = hashlib.sha256(bytes.fromhex(self.preimage)).hexdigest()
        self.configs = [dict(network=n, node_id='02'+str(i)*64, rune='PRIVATE', ca_pem=None,
                            url='https://localhost:'+str(9000+i), cli=['cli', '--network='+n])
                        for i,n in enumerate(('regtest', 'xbt-regtest'), 1)]
        self.calls = []
        self.data = {}

    def fixture(self, direction='forward', phase='prepared', status='pending'):
        manager = self.parent/direction
        lifecycle.setup(manager)
        root = manager/'jobs'/'swap'; root.mkdir()
        forward = direction == 'forward'
        state = dict(phase='prepared', payment_hash=self.hash, btc_cli=self.configs[0]['cli'],
                     xbt_cli=self.configs[1]['cli'], route=[dict(id='03'+'9'*64, amount_msat=2000)],
                     quote_gate=True, durable_gate=True, btc_binding=['1x2x3', 4], xbt_binding=['1x2x3', 4],
                     xbt_amount_msat=2000, btc_amount_msat=2000, xbt_invoice='PRIVATE-INVOICE', btc_invoice='PRIVATE-INVOICE',
                     payment_secret='cd'*32, btc_secret='cd'*32, reverse_quote={'pinned': True}, xbt_expiry=200)
        token = executor.prepare(root, direction, state, self.configs)
        executor.authorize(root, token, 1100, True, now=1000)
        state['phase'] = phase; save(root/'state.json', state)
        if phase != 'prepared': save(root/'launched.json', dict(digest=token))
        lifecycle.restored(manager)
        out = 'xbt-regtest' if forward else 'regtest'; inc = 'regtest' if forward else 'xbt-regtest'
        for c in self.configs: self.data[c['network'], 'getinfo'] = dict(id=c['node_id'], network=c['network'])
        self.data[out, 'decode'] = dict(valid=True, type='bolt11 invoice', currency='xbtrt' if forward else 'bcrt',
            payment_hash=self.hash, amount_msat=2000, payee=state['route'][0]['id'], payment_secret='cd'*32)
        self.payment = dict(payment_hash=self.hash, amount_msat=2000, amount_sent_msat=2000,
                            destination=state['route'][0]['id'], bolt11='PRIVATE-INVOICE', status=status)
        if status == 'complete': self.payment['payment_preimage'] = self.preimage
        self.data[out, 'listsendpays'] = dict(payments=[self.payment])
        self.gate = dict(payment_hash=self.hash, binding=['1x2x3',4], phase='held', terms=state['reverse_quote'], cltv_expiry=200)
        self.data[inc, 'xbt-quote-status' if forward else 'reverse-status'] = self.gate
        self.data[inc, 'listpeerchannels'] = dict(channels=[dict(short_channel_id='1x2x3', state='CHANNELD_NORMAL',
            htlcs=[dict(id=4, direction='in', payment_hash=self.hash, amount_msat=2000, expiry=200, state='RCVD_ADD_ACK_REVOCATION')])])
        return root

    def factory(self, config):
        outer = self
        class Client:
            def call(self, method, *args):
                outer.assertIn(method, stale.READS)
                outer.calls.append((config['network'], method))
                return copy.deepcopy(outer.data[config['network'], method])
        return Client()

    def test_stale_prepared_and_submitted_find_pending_without_changes(self):
        for direction, phase in [('forward','prepared'), ('reverse','outgoing_started')]:
            root = self.fixture(direction, phase)
            before = {p.name:p.read_bytes() for p in root.iterdir() if p.suffix=='.json'}
            report = stale.inspect(root, self.factory)
            self.assertEqual(report['outgoing_status'], 'pending')
            self.assertFalse(report['execution_authorized'])
            self.assertEqual(before, {p.name:p.read_bytes() for p in root.iterdir() if p.suffix=='.json'})
            self.assertNotIn('PRIVATE', str(report)); self.assertNotIn(self.hash, str(report))
            for mode in (False,True):
                with self.assertRaisesRegex(ValueError,'restored_execution_blocked'):
                    executor.step(root, recover_only=mode)
            self.assertEqual(lifecycle.tick(root.parent.parent), {})

    def test_complete_requires_matching_preimage_and_returns_no_secret(self):
        root=self.fixture(status='complete')
        self.gate['phase']='resolved'
        result=stale.inspect(root,self.factory)
        self.assertEqual(result['outgoing_status'],'complete')
        self.assertNotIn(self.preimage,str(result))
        self.payment['payment_preimage']='ff'*32
        with self.assertRaises(ValueError): stale.inspect(root,self.factory)

    def test_failed_is_observation_not_refund_authority(self):
        root=self.fixture('reverse',status='failed')
        self.gate['phase']='failed'
        self.assertEqual(stale.inspect(root,self.factory)['outgoing_status'],'failed')
        self.assertFalse(any(m in ('sendpay','reverse-fail','reverse-release') for _,m in self.calls))

    def test_missing_or_multiple_attempts_refused(self):
        root=self.fixture()
        for payments in ([],[self.payment,self.payment]):
            self.data['xbt-regtest','listsendpays']['payments']=payments
            with self.assertRaisesRegex(ValueError,'outgoing_missing_or_ambiguous'): stale.inspect(root,self.factory)

    def test_wrong_operator_blocks_swap_reads(self):
        root=self.fixture()
        self.data['xbt-regtest','getinfo']['id']='03'+'0'*64
        with self.assertRaises(ValueError): stale.inspect(root,self.factory)
        self.assertTrue(all(method=='getinfo' for _,method in self.calls))

    def test_changed_invoice_gate_attempt_or_incoming_rejected(self):
        root=self.fixture('reverse')
        mutations=[(self.gate,'binding',['other',4]),(self.gate,'terms',{}),
                   (self.payment,'amount_sent_msat',2001),(self.payment,'destination','other'),
                   (self.payment,'bolt11','other'),(self.payment,'payment_preimage',self.preimage),
                   (self.data['regtest','decode'],'payment_secret','other'),
                   (self.data['xbt-regtest','listpeerchannels']['channels'][0]['htlcs'][0],'id',5)]
        for obj,key,value in mutations:
            with self.subTest(key=key):
                existed=key in obj; old=obj.get(key); obj[key]=value
                with self.assertRaises(ValueError): stale.inspect(root,self.factory)
                if existed: obj[key]=old
                else: del obj[key]

    def test_gate_outcome_inconsistent_with_pending_refused(self):
        root=self.fixture()
        for phase in ('resolved','failed','unknown'):
            self.gate['phase']=phase
            with self.assertRaises(ValueError): stale.inspect(root,self.factory)

    def test_missing_barrier_or_changed_saved_binding_refused_before_rpc(self):
        root=self.fixture()
        barrier=root.parent.parent/'restored.json'; barrier.unlink()
        with self.assertRaises(FileNotFoundError): stale.inspect(root,self.factory)
        lifecycle.restored(root.parent.parent)
        state=private_load(root/'state.json'); state['payment_hash']='ff'*32; save(root/'state.json',state)
        with self.assertRaises(ValueError): stale.inspect(root,self.factory)
        self.assertEqual(self.calls,[])

    def test_observer_rejects_mutations_before_transport(self):
        observer=stale.Observer.__new__(stale.Observer)
        for method in ('sendpay','waitsendpay','xbt-release','xbt-fail','reverse-release','reverse-fail'):
            with self.assertRaisesRegex(ValueError,'inspection_read_only'): observer.call(method)

    def test_capture_keeps_old_authorization_but_worker_cannot_use_it(self):
        source=self.fixture()
        target=self.parent/'stale-prepared'
        stale.capture(source,target)
        copy_root=target/'jobs'/'swap'
        self.assertTrue((copy_root/'permit.json').exists())
        self.assertEqual(lifecycle.tick(target),{})
        with self.assertRaisesRegex(ValueError,'restored_execution_blocked'): executor.step(copy_root,now=1001)
        with self.assertRaises(ValueError): stale.capture(source,target)


if __name__=='__main__': unittest.main()
