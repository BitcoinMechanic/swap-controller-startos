import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
import deadline_recovery as r
from controller import save,private_load
from deadline_boundary import initial_state
MODULES=Path(os.environ.get('SWAP_MODULES','/opt/swap'))

class ClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        p=patch.dict(os.environ,{'BTC_XBT_DISPOSABLE_CONTAINER':'1'});p.start();self.addCleanup(p.stop)
    def fixture(self,reverse=False):
        self.preimage='ab'*32;self.hash=hashlib.sha256(bytes.fromhex(self.preimage)).hexdigest()
        self.spec=dict(direction='reverse' if reverse else 'forward',node_ids=dict(btc='02'+'a'*64,xbt='03'+'b'*64),
            channel=dict(channel_id='c'*64,funding_txid='d'*64,funding_outnum=0,peer_id='02'+'e'*64,short_channel_id='1x1x1'),
            htlc_id=7,payment_hash=self.hash,expiry=300,incoming_amount_msat=100000000,
            outgoing_amount_msat=200000000,groupid=1,partid=0)
        self.incoming='xbt' if reverse else 'btc';state=initial_state(self.spec)
        intent=dict(binding=['1x1x1',7],payment_hash=self.hash,expiry=300)
        if reverse:intent['channel']=self.spec['channel']
        else:intent['channel_id']='c'*64
        state[self.incoming+'_close_intent']=intent
        save(self.root/'deadline.json',dict(source_commit=r.PIN,spec=self.spec,state=state))
        self.gate='held';self.status='complete';self.channel_state='ONCHAIN';self.corrupt=False;self.loss=False
        self.calls=[];self.releases=0;self.bad_preimage=False;self.bad_attempt=False;owner=self
        class Fake:
            def __init__(self,role):
                self.role=role;self.network='regtest' if role=='btc' else 'xbt-regtest';self.node_id=owner.spec['node_ids'][role]
            def call(self,method,*args):
                owner.calls.append((self.role,method));s=owner.spec
                if method=='getinfo':return dict(id=self.node_id,network=self.network)
                if method=='listsendpays':
                    p=dict(id=9,payment_hash=owner.hash,groupid=2 if owner.bad_attempt else 1,amount_sent_msat=200000000,status=owner.status)
                    if owner.status=='complete':p['payment_preimage']='cd'*32 if owner.bad_preimage else owner.preimage
                    return dict(payments=[p])
                if method in ('xbt-quote-status','reverse-status'):
                    return dict(payment_hash=owner.hash,binding=['1x1x1',7],phase=owner.gate,cltv_expiry=300,
                                terms=dict(payment_hash=owner.hash,xbt_amount_msat=100000000,btc_amount_msat=200000000))
                if method=='listpeerchannels':
                    c=dict(s['channel'],state=owner.channel_state)
                    if owner.corrupt:c['funding_txid']='f'*64
                    return dict(channels=[c])
                if method=='xbt-spend-info':return dict(payment_hash=owner.hash,binding=['1x1x1',7],cltv_expiry=300,btc_amount_msat=100000000,xbt_amount_msat=200000000)
                if method in ('xbt-release','reverse-release'):
                    assert self.role==owner.incoming
                    assert private_load(owner.root/'claim-receipt.json')['stage']=='release_intent'
                    assert args==((owner.preimage,) if not reverse else (owner.hash,json.dumps(['1x1x1',7]),owner.preimage))
                    owner.releases+=1;owner.gate='resolved'
                    if owner.loss:raise RuntimeError('lost')
                    return dict(released=1)
                raise AssertionError(method)
        self.clients={k:Fake(k) for k in ('btc','xbt')}
    def run_step(self,**kwargs):return r.resolve(self.root,self.spec,self.clients,modules=MODULES,**kwargs)
    def test_both_directions_resolve_once_without_changing_close_record(self):
        for reverse in (False,True):
            self.fixture(reverse);before=(self.root/'deadline.json').read_bytes()
            self.assertEqual(self.run_step()['phase'],'gate_resolved');self.run_step()
            self.assertEqual(self.releases,1);self.assertEqual((self.root/'deadline.json').read_bytes(),before)
            self.assertNotIn(self.preimage,(self.root/'claim-receipt.json').read_text())
            (self.root/'claim-receipt.json').unlink()
    def test_lost_reply_reconciles_terminal_gate_without_resend(self):
        for reverse in (False,True):
            self.fixture(reverse);self.loss=True
            with self.assertRaises(RuntimeError):self.run_step()
            self.assertEqual(private_load(self.root/'claim-receipt.json')['stage'],'release_intent')
            self.run_step();self.assertEqual(self.releases,1)
            (self.root/'claim-receipt.json').unlink()
    def test_uncertain_release_not_retried(self):
        self.fixture();self.loss=True
        with self.assertRaises(RuntimeError):self.run_step()
        self.gate='held'
        with self.assertRaises(ValueError):self.run_step()
        self.assertEqual(self.releases,1)
    def test_pending_does_not_write_or_release(self):
        self.fixture();self.status='pending'
        self.assertEqual(self.run_step()['phase'],'outgoing_pending')
        self.assertFalse((self.root/'claim-receipt.json').exists());self.assertEqual(self.releases,0)
    def test_failed_outgoing_never_refunded(self):
        self.fixture();self.status='failed'
        with self.assertRaises(ValueError):self.run_step()
        self.assertEqual(self.releases,0)
    def test_wrong_preimage_attempt_channel_or_unconfirmed_close_refused(self):
        for field,value in (('bad_preimage',True),('bad_attempt',True),('corrupt',True),('channel_state','AWAITING_UNILATERAL')):
            self.fixture();setattr(self,field,value)
            with self.assertRaises(ValueError):self.run_step()
            self.assertEqual(self.releases,0)
    def test_missing_intent_and_restore_refused(self):
        self.fixture();record=private_load(self.root/'deadline.json');record['state'].pop('btc_close_intent');save(self.root/'deadline.json',record)
        with self.assertRaises(ValueError):self.run_step()
        self.fixture();save(self.root/'restored.json',{'blocked':True})
        with self.assertRaises(ValueError):self.run_step()
        self.assertEqual(self.calls,[])
    def test_stale_observation_refused(self):
        self.fixture();times=iter([0,0,121])
        with self.assertRaises(ValueError):self.run_step(clock=lambda:next(times))
        self.assertEqual(self.releases,0)
    def test_rpc_has_no_close_send_or_refund(self):
        client=r.ClaimRemote.__new__(r.ClaimRemote);client.network='regtest';client.node_id='02'+'a'*64
        client.incoming_network='regtest';client.release='xbt-release';client.payment_hash='f'*64;client.binding=['1x1x1',7]
        with patch.object(client,'_request') as request:
            for method in ('close','sendpay','xbt-fail','reverse-fail','withdraw'):
                with self.assertRaises(ValueError):client.call(method)
            request.assert_not_called()
    def test_forward_transport_uses_only_bound_release(self):
        self.fixture()
        client=r.ClaimRemote.__new__(r.ClaimRemote)
        client.network='regtest';client.node_id=self.spec['node_ids']['btc']
        client.incoming_network='regtest';client.release='xbt-release';client.payment_hash=self.hash;client.binding=['1x1x1',7]
        calls=[]
        def request(method,params):
            calls.append((method,params))
            return dict(id=client.node_id,network='regtest') if method=='getinfo' else dict(released=1)
        with patch.object(client,'_request',side_effect=request):
            client.call('xbt-release',self.preimage)
            self.assertEqual(calls[-1],('xbt-release-bound',dict(payment_hash=self.hash,preimage=self.preimage)))
            self.assertNotIn('xbt-release',[m for m,p in calls])
            before=list(calls)
            with self.assertRaises(ValueError):client.call('xbt-release','00'*32)
            self.assertEqual(calls,before)
    def test_live_network_refused(self):
        with self.assertRaises(ValueError):r.ClaimRemote({'network':'bitcoin'},'forward','a'*64,['1x1x1',1])

if __name__=='__main__':unittest.main()
