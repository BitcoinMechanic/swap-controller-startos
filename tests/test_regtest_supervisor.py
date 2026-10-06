"""Routing checks; funded scenarios exercise actual packaged transports separately."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
import regtest_supervisor as s
from controller import save


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'SOURCE_COMMIT').write_text(s.executor.PIN)
        self.env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1');self.env.start();self.addCleanup(self.env.stop)
        self.spec=dict(direction='forward',payment_hash='a'*64,groupid=1,partid=0,outgoing_amount_msat=2000)
        save(self.root/'supervisor.json',dict(spec=self.spec))
        self.state=dict(phase='outgoing_started')
        self.initial=dict(direction='forward')
        self.remote=Mock()
        self.payment=dict(payment_hash='a'*64,groupid=1,amount_sent_msat=2000,status='pending')
        self.remote.call.side_effect=lambda *args:dict(payments=[self.payment])
        for name,value in [('binding',lambda *args:(self.initial,self.state)),('clients',lambda *args:{'btc':self.remote,'xbt':self.remote})]:
            p=patch.object(s,name,side_effect=value);p.start();self.addCleanup(p.stop)
        self.execute=patch.object(s.executor,'step',return_value='executor').start()
        self.close=patch.object(s.deadline,'step',return_value='deadline').start()
        self.claim=patch.object(s.claim,'resolve',return_value='claim').start()
        self.addCleanup(patch.stopall)
    def run_step(self,**kw):return s.advance(self.root,modules=self.root,**kw)
    def test_both_directions_dispatch_pending_complete_and_failed(self):
        for direction in ('forward','reverse'):
            self.initial['direction']=direction
            for outcome,expected in (('pending','deadline'),('complete','executor'),('failed','executor')):
                self.payment['status']=outcome
                self.assertEqual(self.run_step(),expected)
        self.assertEqual(self.close.call_count,2)
        self.assertEqual(self.execute.call_count,4)
        self.claim.assert_not_called()
    def test_close_intent_always_selects_claim_including_failure(self):
        save(self.root/'deadline.json',{})
        for status in ('pending','complete','failed'):
            self.payment['status']=status
            self.assertEqual(self.run_step(),'claim')
        self.remote.call.assert_not_called();self.execute.assert_not_called();self.close.assert_not_called()
    def test_ambiguous_changed_and_contradictory_outcomes_refused(self):
        for updates in ({'status':'unknown'},{'groupid':2},{'partid':False},{'amount_sent_msat':2001},{'payment_preimage':'b'*64}):
            original=dict(self.payment);self.payment.update(updates)
            with self.assertRaises(ValueError):self.run_step()
            self.payment=original
        self.remote.call.side_effect=lambda *args:dict(payments=[])
        with self.assertRaises(ValueError):self.run_step()
        self.execute.assert_not_called();self.close.assert_not_called()
    def test_configuration_change_refused(self):
        self.run_step()
        save(self.root/'supervisor.json',dict(spec=self.spec,changed=True))
        with self.assertRaises(ValueError):self.run_step()
        self.assertEqual(self.close.call_count,1)
    def test_restore_and_missing_optin_refuse_before_rpc(self):
        save(self.root/'restored.json',{})
        with self.assertRaises(ValueError):self.run_step()
        (self.root/'restored.json').unlink()
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'):
            with self.assertRaises(ValueError):self.run_step()
        self.remote.call.assert_not_called()
    def test_slow_observation_refused(self):
        with self.assertRaises(ValueError):self.run_step(clock=Mock(side_effect=[0,121]))
        self.close.assert_not_called();self.execute.assert_not_called()
    def test_intermediate_and_terminal_use_executor_without_observation(self):
        for phase in ('xbt_paid','btc_failed','btc_released'):
            self.state['phase']=phase
            self.assertEqual(self.run_step(),'executor')
        self.remote.call.assert_not_called()
    def test_unenrolled_uses_existing_executor(self):
        (self.root/'supervisor.json').unlink()
        self.assertEqual(self.run_step(),'executor');self.remote.call.assert_not_called()
    def test_live_config_refused_before_transport(self):
        for network in ('bitcoin','xbt'):
            with self.assertRaises(ValueError):
                # Call the real constructor helper, not this fixture's mock.
                self.real_clients(dict(spec=dict(direction='forward'),deadline_connections=[dict(network=network),{}]))
    def test_existing_close_record_blocks_direct_executor(self):
        save(self.root/'deadline.json',{})
        with self.assertRaisesRegex(ValueError,'post_close_supervisor_required'):
            self.real_executor(self.root)

    def test_quote_enrollment_binding_rejects_changed_terms_and_identity(self):
        import quote_workflow as q
        spec=dict(direction='forward',node_ids=dict(btc='02'+'a'*64,xbt='03'+'b'*64),
            channel=dict(channel_id='c'*64,funding_txid='d'*64,funding_outnum=0,peer_id='02'+'e'*64,short_channel_id='1x1x1'),
            htlc_id=7,payment_hash='f'*64,expiry=200,incoming_amount_msat=1000,outgoing_amount_msat=2000,groupid=1,partid=0)
        state=dict(payment_hash=spec['payment_hash'],btc_binding=['1x1x1',7],route=[dict(amount_msat=2000)])
        connections=[dict(network=n,node_id=spec['node_ids'][role]) for n,role in (('regtest','btc'),('xbt-regtest','xbt'))]
        initial=dict(direction='forward',state=state,connections=connections)
        save(self.root/'handoff.json',dict(review_digest='token',state=state))
        save(self.root/'launched.json',dict(digest=s.executor.digest(initial)))
        request=dict(intent_digest=s.executor.digest(initial),spec=spec,deadline_connections=[],claim_connections=[])
        with patch.object(s.executor,'records',return_value=(initial,state)), \
             patch.object(q,'review',return_value=(dict(connections=connections),dict(terms=dict(btc_amount_msat=1000)),'token')):
            self.real_binding(self.root,request)
            for key,value in (('payment_hash','0'*64),('htlc_id',8),('incoming_amount_msat',1001),('outgoing_amount_msat',2001)):
                bad=copy.deepcopy(request);bad['spec'][key]=value
                with self.assertRaises(ValueError):self.real_binding(self.root,bad)
            bad=copy.deepcopy(request);bad['spec']['node_ids']['btc']='02'+'1'*64
            with self.assertRaises(ValueError):self.real_binding(self.root,bad)
            bad=dict(request,intent_digest='changed')
            with self.assertRaises(ValueError):self.real_binding(self.root,bad)

    real_binding=staticmethod(s.binding)
    real_executor=staticmethod(s.executor.step)
    real_clients=staticmethod(s.clients)


if __name__=='__main__':unittest.main()
