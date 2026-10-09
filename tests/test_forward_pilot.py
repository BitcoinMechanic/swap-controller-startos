"""Integrated contract/node/worker tests; funded transport validation is separate."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'assets'))
import forward_pilot as worker
import lifecycle
from controller import private_load,save
from pilot_contract import digest,PROFILE,PIN
NODE=REPO.parent/'btc-cln-startos/assets/swaps/pilot_node.py'
if not NODE.exists():NODE=Path('/node-assets/pilot_node.py')
sys.path.append(str(NODE.parent))
spec=importlib.util.spec_from_file_location('pilot_node',NODE)
node_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(node_module)


class Fixture:
    def __init__(self,role,clock):
        self.role=role;self.clock=clock;self.id='02'+('1' if role=='btc' else '2')*64
        self.ch=dict(channel_id=('3' if role=='btc' else '4')*64,short_channel_id='1x1x'+('0' if role=='btc' else '1'),
            funding_txid='5'*64,funding_outnum=0,peer_id='03'+'6'*64,state='CHANNELD_NORMAL',peer_connected=True,
            htlcs=[],spendable_msat=600000000,receivable_msat=600000000,feerate={'perkw':253},dust_limit_msat=546000)
        self.preimage='a'*64;self.hash=hashlib.sha256(bytes.fromhex(self.preimage)).hexdigest()
        self.decoded=dict(valid=True,type='bolt11 invoice',currency='xbt',amount_msat=2000000,
            payment_hash=self.hash,payment_secret='b'*64,payee=self.ch['peer_id'],created_at=clock[0],expiry=3600,min_final_cltv_expiry=18)
        self.height=1000;self.terms=None;self.gate=None;self.payments=[];self.calls=[];self.drop=None
    def __call__(self,method,**params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='getinfo':return dict(id=self.id,network='bitcoin' if self.role=='btc' else 'xbt',blockheight=self.height)
        if method=='listpeerchannels':return {'channels':[copy.deepcopy(self.ch)]}
        if method=='listfunds':return {'outputs':[dict(amount_msat=50000000,status='confirmed',reserved=False)]}
        if method=='listsendpays':return {'payments':copy.deepcopy(self.payments)}
        if method=='decode':
            if self.role=='xbt':return copy.deepcopy(self.decoded)
            return dict(valid=True,currency='bc',payee=self.id,payment_hash=self.hash,payment_secret=self.terms['payment_secret'],amount_msat=1000000,min_final_cltv_expiry=300)
        if method=='xbt-pilot-info':return dict(profile='live-pilot-v1',registered_quotes=0 if self.terms is None else 1)
        if method=='createrune':return dict(rune='private-rune',unique_id='7')
        if method=='xbt-register':
            assert self.terms is None
            self.terms=params['quote'];self.gate=dict(payment_hash=self.hash,phase='quoted',binding=None)
            return dict(registered=True)
        if method=='signinvoice':return {'bolt11':'lnbc-test-invoice'}
        if method=='xbt-quote-status':return copy.deepcopy(self.gate)
        if method=='xbt-spend-info':return dict(self.terms,binding=self.gate['binding'],cltv_expiry=1300)
        if method=='sendpay':
            assert not self.payments
            self.payments=[dict(payment_hash=self.hash,groupid=1,partid=0,amount_sent_msat=2000000,status='pending')]
            result={'status':'pending'}
        elif method=='close':
            self.ch['state']='ONCHAIN';result={'type':'unilateral'}
        elif method=='xbt-release-bound':
            assert params==dict(payment_hash=self.hash,preimage=self.preimage)
            self.gate['phase']='resolved';self.ch['htlcs']=[];result={'released':1}
        elif method=='xbt-fail':
            self.gate['phase']='failed';self.ch['htlcs']=[];result={'failed':1}
        else:raise AssertionError(method)
        if self.drop==method:raise RuntimeError('simulated_lost_reply')
        return result


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'controller';self.root.mkdir()
        self.clock=[1700000000];lifecycle.setup(self.root/'execution')
        save(self.root/'execution/heartbeat.json',dict(checked_at=self.clock[0],mode='disabled'))
        self.rpc={k:Fixture(k,self.clock) for k in ('btc','xbt')}
        self.nodes={}
        for role in self.rpc:
            p=Path(self.tmp.name)/role;p.mkdir()
            self.nodes[role]=node_module.Node(p,role,self.rpc[role],lambda:self.clock[0])
        self.config=dict(generation='c'*32,nodes={k:dict(node_id=v.id,role=k) for k,v in self.rpc.items()})
        p=patch.object(worker,'load_config',lambda root:self.config);p.start();self.addCleanup(p.stop)
        p=patch.dict(sys.modules,swap_invoice=types.SimpleNamespace(unsigned_invoice=lambda *a,**k:'unsigned-fixture'));p.start();self.addCleanup(p.stop)
        owner=self
        class Inspection:
            def __init__(self,node,rune):self.rpc=owner.rpc[node['role']]
            def call(self,method,**params):return self.rpc(method,**params)
        class Remote:
            def __init__(self,node,rune):self.node=owner.nodes[node['role']]
            def call(self,method,**params):
                with node_module.locked(self.node.root):
                    return (self.node.observe if method=='swap-pilot-observe' else self.node.step)(**params)
        self.inspect=Inspection;self.remote=Remote
    def prepare(self):
        req=dict(invoice='lnxbt-test-invoice',incomingChannel='1x1x0',outgoingChannel='1x1x1',btcRune='read',xbtRune='read')
        self.prepared=worker.prepare(self.root,req,factory=self.inspect,now=self.clock[0])
        for node in self.nodes.values():node.authorize(self.prepared['contract'],True)
        return self.prepared
    def approve(self):
        self.prepare()
        return worker.approve(self.root,dict(pilotId=self.prepared['pilot_id'],btcRune='cap',xbtRune='cap',confirmed=True),factory=self.remote,now=self.clock[0])
    def hold(self):
        b=self.rpc['btc'];b.gate.update(phase='held',binding=['1x1x0',7])
        b.ch['htlcs']=[dict(id=7,direction='in',payment_hash=b.hash,amount_msat=1000000,expiry=1300,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
    def tick(self):return worker.tick(self.root,factory=self.remote,now=self.clock[0])
    def count(self,role,method):return sum(m==method for m,_ in self.rpc[role].calls)
    def complete(self):self.rpc['xbt'].payments[0].update(status='complete',payment_preimage=self.rpc['xbt'].preimage)
    def test_no_mutation_before_approval_and_complete_once(self):
        self.prepare();self.tick()
        self.assertEqual(self.count('btc','xbt-register'),0);self.assertEqual(self.count('xbt','sendpay'),0)
        worker.approve(self.root,dict(pilotId=self.prepared['pilot_id'],btcRune='cap',xbtRune='cap',confirmed=True),factory=self.remote,now=self.clock[0])
        self.tick();self.assertEqual(self.count('xbt','sendpay'),0)
        self.hold();self.tick();self.complete();self.tick();self.assertEqual(self.tick()['phase'],'settled');self.tick()
        self.assertEqual(self.count('xbt','sendpay'),1);self.assertEqual(self.count('btc','xbt-release-bound'),1)
    def test_lost_send_and_release_replies_are_not_repeated(self):
        self.approve();self.hold();self.rpc['xbt'].drop='sendpay'
        with self.assertRaises(RuntimeError):self.tick()
        self.rpc['xbt'].drop=None;self.complete();self.rpc['btc'].drop='xbt-release-bound'
        with self.assertRaises(RuntimeError):self.tick()
        self.assertEqual(self.tick()['phase'],'settled')
        self.assertEqual(self.count('xbt','sendpay'),1);self.assertEqual(self.count('btc','xbt-release-bound'),1)
    def test_pending_closes_original_once_and_recovers(self):
        self.approve();self.hold();self.tick();self.rpc['btc'].height=1227;self.tick()
        self.assertEqual(self.count('btc','close'),0)
        self.rpc['btc'].height=1228;self.rpc['btc'].drop='close'
        with self.assertRaises(RuntimeError):self.tick()
        self.tick();self.assertEqual(self.count('btc','close'),1)
        self.complete();self.tick();self.assertEqual(self.tick()['phase'],'onchain_recovery')
        self.assertEqual(self.count('btc','xbt-release-bound'),1)
    def test_definitive_failure_without_close(self):
        self.approve();self.hold();self.tick();self.rpc['xbt'].payments[0]['status']='failed';self.tick()
        self.assertEqual(self.tick()['phase'],'failed');self.tick()
        self.assertEqual(self.count('btc','xbt-fail'),1);self.assertEqual(self.count('btc','close'),0)
    def test_failure_after_close_never_refunded(self):
        self.approve();self.hold();self.tick();self.rpc['btc'].height=1228;self.tick()
        self.rpc['xbt'].payments[0]['status']='failed'
        with self.assertRaises(ValueError):self.tick()
        self.assertEqual(self.count('btc','xbt-fail'),0)
    def test_restore_invalidates_authority_and_preserves_old_barrier(self):
        self.approve();lifecycle.restored(self.root/'execution')
        with self.assertRaises(ValueError):self.tick()
        self.assertEqual(private_load(self.root/'execution/restored.json'),{'blocked':True})
        self.assertEqual(self.count('xbt','sendpay'),0)
    def test_new_contract_after_old_readonly_restore_requires_fresh_node_slots(self):
        lifecycle.restored(self.root/'execution')
        save(self.root/'execution/heartbeat.json',dict(checked_at=self.clock[0],mode='restored'))
        self.approve()
        self.assertTrue((self.root/'execution/restored.json').exists())
        self.hold();self.tick();self.assertEqual(self.count('xbt','sendpay'),1)
    def test_node_restore_and_changed_contract_refused(self):
        self.prepare();c=copy.deepcopy(self.prepared['contract']);c['nonce']='f'*64
        with self.assertRaises(ValueError):self.nodes['xbt'].authorize(c,True)
        node_module.save(self.nodes['xbt'].root/'forward-pilot-restored.json',{'blocked':True})
        with self.assertRaises(ValueError):self.nodes['xbt'].observe(self.prepared['pilot_id'])
    def test_low_margin_and_changed_funding_refuse_send(self):
        self.approve();self.hold();self.rpc['btc'].height=1013
        with self.assertRaises(ValueError):self.tick()
        self.rpc['btc'].height=1000;self.rpc['btc'].ch['funding_txid']='f'*64
        with self.assertRaises(ValueError):self.tick()
        self.assertEqual(self.count('xbt','sendpay'),0)
    def test_raw_method_wrong_role_and_changed_preimage_refused(self):
        self.approve();self.hold();pilot=self.prepared['pilot_id']
        for role,operation in [('btc','send'),('xbt','close'),('btc','withdraw')]:
            with self.assertRaises(ValueError):self.nodes[role].step(pilot,operation)
        with self.assertRaises(ValueError):self.nodes['btc'].step(pilot,'release','f'*64)
        self.assertEqual(self.count('xbt','sendpay'),0)
    def test_backups_block_pending_and_allow_clean_terminal(self):
        self.approve()
        with self.assertRaises(ValueError):lifecycle.backup_begin(self.root/'execution')
        self.hold();self.tick();self.complete();self.tick();self.tick()
        lifecycle.backup_begin(self.root/'execution')
        with self.assertRaises(ValueError):self.tick()
    def test_node_send_intent_prevents_repeat_even_without_payment(self):
        self.approve();self.hold();self.tick();self.rpc['xbt'].payments=[]
        with self.assertRaises(ValueError):self.nodes['xbt'].step(self.prepared['pilot_id'],'send')
        self.assertEqual(self.count('xbt','sendpay'),1)
    def test_ambiguous_original_attempt_and_wrong_preimage_refused(self):
        self.approve();self.hold();self.tick();self.complete()
        self.rpc['xbt'].payments[0]['payment_preimage']='d'*64
        with self.assertRaises(ValueError):self.tick()
        self.assertEqual(self.count('btc','xbt-release-bound'),0)


if __name__=='__main__':unittest.main()
