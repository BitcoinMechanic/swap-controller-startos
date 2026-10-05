import copy
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
import executor
import lifecycle
import lost_journal as lost
from controller import private_load,save
import test_stale_restore as fixtures


class LostTests(unittest.TestCase):
    setUp=fixtures.StaleTests.setUp
    fixture=fixtures.StaleTests.fixture
    factory=fixtures.StaleTests.factory

    def setup_swap(self,direction='reverse',status='pending'):
        self.root=self.fixture(direction,status=status)
        self.payment.update(id=7,groupid=1,partid=0)
        self.direction=direction
        self.configs2=[dict(c,rune='RECOVERY-'+c['network']) for c in self.configs]
        self.token=executor.digest(private_load(self.root/'intent.json'))
        self.mutations=[];self.fault=None
        if direction=='forward':
            self.data['regtest','xbt-spend-info']=dict(payment_hash=self.hash,binding=['1x2x3',4],
                xbt_invoice='PRIVATE-INVOICE',xbt_amount_msat=2000,btc_amount_msat=100000000,
                min_cltv_delta=100,max_cltv_delta=2000,cltv_expiry=200)
            self.data['regtest','listpeerchannels']['channels'][0]['htlcs'][0]['amount_msat']=100000000

    def clients(self,config):
        ordinary=self.factory(config); outer=self
        class Client:
            def call(self,method,*args):
                if method=='xbt-spend-info': return copy.deepcopy(outer.data[config['network'],method])
                if method in lost.WRITES[outer.direction]:
                    outer.mutations.append((method,args))
                    if outer.fault=='before': raise OSError('private transport failure')
                    release=method.endswith('release')
                    outer.gate['phase']='resolved' if release else 'failed'
                    if outer.fault=='after': raise OSError('private lost reply')
                    return {'released' if release else 'failed':1}
                return ordinary.call(method,*args)
        return Client()

    def resolve(self):
        return lost.resolve(self.root,self.token,self.configs2,self.parent/'audit',factory=self.clients)

    def test_pending_and_expired_original_permit_never_mutate(self):
        self.setup_swap()
        for _ in range(2):
            mirror,result=self.resolve()
            self.assertEqual(result,dict(phase='outgoing_started',outcome='pending'))
            self.assertEqual(mirror['phase'],'outgoing_started')
        self.assertEqual(self.mutations,[])
        self.assertEqual(private_load(self.root/'state.json')['phase'],'prepared')
        self.assertEqual(lifecycle.tick(self.root.parent.parent),{})

    def test_both_directions_settle_once_without_changing_snapshot(self):
        for direction in ('forward','reverse'):
            with self.subTest(direction=direction):
                self.setup_swap(direction,status='complete')
                before=(self.root/'state.json').read_bytes()
                first=self.resolve();second=self.resolve()
                self.assertEqual(first,second)
                self.assertEqual(len(self.mutations),1)
                self.assertEqual(self.mutations[0][0],'xbt-release' if direction=='forward' else 'reverse-release')
                self.assertEqual(before,(self.root/'state.json').read_bytes())
                self.assertTrue(private_load(self.root.parent.parent/'restored.json')['blocked'])

    def test_both_directions_fail_only_definitive_attempt_once(self):
        for direction in ('forward','reverse'):
            self.setup_swap(direction,status='failed')
            self.resolve();self.resolve()
            self.assertEqual(len(self.mutations),1)
            self.assertEqual(self.mutations[0],('xbt-fail' if direction=='forward' else 'reverse-fail',(self.hash,'["1x2x3", 4]')))

    def test_lost_resolution_reply_reconciles_without_second_mutation(self):
        self.setup_swap(status='complete');self.fault='after'
        with self.assertRaises(OSError): self.resolve()
        self.assertEqual(private_load(self.root/'reconciliation.json')['stage'],'resolution_intent')
        self.fault=None
        self.assertEqual(self.resolve()[1],{'phase':'xbt_released'})
        self.assertEqual(len(self.mutations),1)

    def test_unreceived_resolution_requires_inspection_never_retries(self):
        self.setup_swap(status='failed');self.fault='before'
        with self.assertRaises(OSError): self.resolve()
        self.fault=None
        with self.assertRaisesRegex(ValueError,'resolution_outcome_unknown'): self.resolve()
        self.assertEqual(len(self.mutations),1)
        self.assertEqual(self.gate['phase'],'held')

    def test_changed_attempt_after_observation_refused(self):
        self.setup_swap();self.resolve()
        self.payment.update(status='failed',id=8)
        with self.assertRaisesRegex(ValueError,'changed_recovery_attempt'): self.resolve()
        self.assertEqual(self.mutations,[])

    def test_wrong_digest_or_replacement_endpoint_rejected_before_rpc(self):
        self.setup_swap(); token=self.token;self.token='0'*64
        with self.assertRaisesRegex(ValueError,'recovery_digest_mismatch'): self.resolve()
        self.token=token;self.configs2[0]['url']='https://other'
        with self.assertRaises(ValueError): self.resolve()
        self.assertEqual(self.calls,[]);self.assertEqual(self.mutations,[])

    def test_original_spending_credentials_refused(self):
        self.setup_swap();self.configs2=copy.deepcopy(self.configs)
        with self.assertRaisesRegex(ValueError,'separate_resolution_credentials_required'):self.resolve()
        self.assertEqual(self.calls,[])

    def test_missing_payment_and_mismatched_forward_quote_never_resolve(self):
        self.setup_swap('forward',status='failed')
        self.data['regtest','xbt-spend-info']['btc_amount_msat']+=1
        with self.assertRaisesRegex(ValueError,'forward_fixture_quote_mismatch'): self.resolve()
        self.data['xbt-regtest','listsendpays']['payments']=[]
        with self.assertRaisesRegex(ValueError,'outgoing_missing_or_ambiguous'): self.resolve()
        self.assertEqual(self.mutations,[])

    def test_competing_recovery_and_missing_barrier_refused(self):
        self.setup_swap(status='complete')
        with executor.lock(self.root):
            with self.assertRaises(BlockingIOError): self.resolve()
        (self.root.parent.parent/'restored.json').unlink()
        with self.assertRaises(FileNotFoundError): self.resolve()
        self.assertEqual(self.mutations,[])

    def test_resolution_client_has_no_send_path_or_outgoing_gate_writes(self):
        for direction in ('forward','reverse'):
            for network in ('regtest','xbt-regtest'):
                client=lost.ResolutionClient.__new__(lost.ResolutionClient)
                client.methods=lost.allowed(network,direction)
                for method in ('sendpay','pay','withdraw','createrune','waitsendpay'):
                    with self.assertRaisesRegex(ValueError,'resolution_only_method_required'):client.call(method)
                incoming='regtest' if direction=='forward' else 'xbt-regtest'
                if network!=incoming:self.assertFalse(client.methods & set.union(*lost.WRITES.values()))

    def test_adapter_recovers_without_reading_fixture_mirror_or_recreating_executor(self):
        import package_step
        self.setup_swap()
        manager=self.parent/'stale-prepared'
        self.root.parent.parent.rename(manager)
        self.root=manager/'jobs'/'swap'
        external=self.parent/'reverse-state.json'
        external.write_text('invalid mirror: must never be an input')
        save(self.parent/'lost-journal.json',dict(digest=self.token,direction='reverse',filename=external.name))
        save(self.parent/'remote-recovery.json',self.configs2)
        original=lost.resolve
        def observed(root,digest,connections,audit):
            return original(root,digest,connections,audit,factory=self.clients)
        with patch.object(lost,'resolve',observed), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(package_step.recover_lost(external,'reverse'),0)
        self.assertEqual(private_load(external)['phase'],'outgoing_started')
        self.assertFalse((self.parent/'execution').exists())
        self.assertEqual(private_load(self.root/'state.json')['phase'],'prepared')
        self.assertEqual(self.mutations,[])

    def test_network_failure_does_not_create_resolution_intent(self):
        self.setup_swap()
        def unavailable(config):
            class Client:
                def call(self,*args):raise OSError('unreachable')
            return Client()
        with self.assertRaises(OSError):
            lost.resolve(self.root,self.token,self.configs2,self.parent/'audit',factory=unavailable)
        self.assertFalse((self.root/'reconciliation.json').exists())
        self.assertEqual(self.mutations,[])


if __name__=='__main__':unittest.main()
