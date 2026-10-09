"""Repeat authority and controller tests with simulated node RPC.

Funded transport validation is a separate packaged regtest; these do not claim
on-chain success. All mutation calls are counted and journal files are checked.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT.parent/'btc-cln-startos/assets/swaps'),'/node-assets']
import controller
import reverse_pilot as pilot
import reverse_swaps as swaps
import swap_setup
import lifecycle
import reverse_session
from reverse_node import Node, load, save, locked
from reverse_contract import canonical,digest

class RPC:
    def __init__(self,role):
        self.role=role;self.id='02'+('1' if role=='xbt' else '2')*64;self.height=1000
        self.ch=dict(channel_id=('3' if role=='xbt' else '4')*64,short_channel_id='1x1x'+('0' if role=='xbt' else '1'),
            funding_txid='5'*64,funding_outnum=0,peer_id='03'+'6'*64,state='CHANNELD_NORMAL',peer_connected=True,
            htlcs=[],spendable_msat=600000000,receivable_msat=600000000,feerate={'perkw':253},dust_limit_msat=546000)
        self.quotes={};self.payments={};self.invoices={};self.preimages={};self.calls=[];self.last=None;self.repeat=False;self.drop=None
    def __call__(self,method,**params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='getinfo':return dict(id=self.id,network='xbt' if self.role=='xbt' else 'bitcoin',blockheight=self.height)
        if method=='listpeerchannels':return {'channels':[copy.deepcopy(self.ch)]}
        if method=='listfunds':return {'outputs':[dict(amount_msat=50000000,status='confirmed',reserved=False)]}
        if method=='decode':
            value=params['string']
            if value.startswith('signed-'):
                h=value[7:];return dict(valid=True,currency='xbt',payee=self.id,payment_hash=h,
                    payment_secret=self.quotes[h]['terms']['payment_secret'],amount_msat=3000000,min_final_cltv_expiry=self.quotes[h]['terms']['min_cltv_delta']+24)
            return copy.deepcopy(self.invoices[value])
        if method=='listsendpays':return {'payments':copy.deepcopy(self.payments.get(params['payment_hash'],[]))}
        if method=='reverse-pilot-info':return dict(profile='reverse-live-v1',repeat_profile=reverse_session.PROFILE,gate_active=True)
        if method=='createrune':return dict(rune='private-'+str(len(self.calls)),unique_id=str(len(self.calls)))
        if method=='reverse-repeat-register':
            q=params['quote']
            if q['payment_hash'] in self.quotes:
                assert self.quotes[q['payment_hash']]['terms']==q
                return {'registered':True}
            assert all(g['phase'] in ('resolved','failed','expired') for g in self.quotes.values())
            self.quotes[q['payment_hash']]=dict(terms=q,phase='quoted',payment_hash=q['payment_hash']);self.last=q['payment_hash'];return {'registered':True}
        if method=='signinvoice':return {'bolt11':'signed-'+self.last}
        if method=='reverse-retire-repeat':
            g=self.quotes[params['payment_hash']];assert g['phase'] in ('quoted','expired') and not g.get('binding')
            g['phase']='expired';return {'retired':True}
        if method=='reverse-status':return dict(copy.deepcopy(self.quotes[params['payment_hash']]),cltv_expiry=1300)
        if method=='btc-spend-info':
            g=self.quotes[params['payment_hash']];return dict(g['terms'],binding=g['binding'],cltv_expiry=1300)
        if method=='sendpay':
            h=params['payment_hash'];assert not self.payments.get(h)
            self.payments[h]=[dict(payment_hash=h,groupid=1,partid=0,amount_sent_msat=1500000,status='pending')];result={'submitted':True}
        elif method in ('reverse-release','reverse-fail'):
            h=params['payment_hash'];g=self.quotes[h];assert g['phase']=='held'
            if method=='reverse-release':assert hashlib.sha256(bytes.fromhex(params['preimage'])).hexdigest()==h
            g['phase']='resolved' if method=='reverse-release' else 'failed';self.ch['htlcs']=[];result={'done':True}
        elif method=='close':self.ch['state']='ONCHAIN';result={'type':'unilateral'}
        else:raise AssertionError(method)
        if self.drop==method:raise ValueError('lost_reply')
        return result

class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'controller';self.root.mkdir();lifecycle.setup(self.root/'execution')
        self.rpc={k:RPC(k) for k in ('xbt','btc')};self.nodes={};self.tokens={}
        self.config=dict(schema=1,generation='c'*32,nodes={k:dict(node_id=v.id,role=k) for k,v in self.rpc.items()})
        for module in (swaps,swap_setup,pilot):
            p=patch.object(module,'load_config',lambda root:self.config);p.start();self.addCleanup(p.stop)
        p=patch.dict(sys.modules,swap_invoice=types.SimpleNamespace(unsigned_invoice=lambda *a,**k:'unsigned'))
        p.start();self.addCleanup(p.stop)
        for role in self.rpc:
            root=Path(self.tmp.name)/role;root.mkdir();self.nodes[role]=reverse_session.Session(root,role,self.rpc[role])
        self.counter=0
        owner=self
        class Inspector:
            def __init__(self,node,rune):self.rpc=owner.rpc[node['role']]
            def call(self,method,**params):return self.rpc(method,**params)
        class Remote:
            def __init__(self,node,token):self.session=owner.nodes[node['role']];self.token=json.loads(token)
            def request(self,operation,contract='',pilot_id='',preimage=''):
                with locked(self.session.root):
                    return self.session.call(self.token['session_id'],operation,contract,pilot_id,preimage)
            def call(self,method,**params):
                return self.request('observe',**params) if method=='swap-pilot-observe' else self.request(**params)
        self.inspector=Inspector;self.remote=Remote
    def setup(self,count=3):
        for role,node in self.nodes.items():
            with patch.object(node,'enable_gate',lambda:self.rpc['xbt'].__setattr__('repeat',True)):
                self.tokens[role]=node.enable('',count,True)['credential']
        swap_setup.configure(self.root,dict(xbtRune='read-xbt',btcRune='read-btc',confirmed=True),factory=self.inspector)
        swaps.configure(self.root,dict(xbtCredential=self.tokens['xbt'],btcCredential=self.tokens['btc'],confirmed=True),factory=self.remote)
    def draft(self):
        self.counter+=1;preimage=('%064x'%self.counter);h=hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice='lnbc-invoice-'+str(self.counter);now=int(time.time())
        self.rpc['btc'].invoices[invoice]=dict(valid=True,type='bolt11 invoice',currency='bc',amount_msat=1500000,
            payment_hash=h,payment_secret='b'*64,payee=self.rpc['btc'].ch['peer_id'],created_at=now,expiry=3600,min_final_cltv_expiry=18)
        self.rpc['btc'].preimages[h]=preimage
        result=swaps.prepare(self.root,{'invoice':invoice},factory=self.remote,inspector=self.inspector)
        return result['pilot_id'],h
    def approve(self,swap_id):
        controller.save(self.root/'execution/heartbeat.json',dict(checked_at=int(time.time()),mode='disabled'))
        return swaps.approve(self.root,dict(pilotId=swap_id,confirmed=True),factory=self.remote)
    def hold(self,h):
        gate=self.rpc['xbt'].quotes[h];gate.update(phase='held',binding=['1x1x0',self.counter])
        self.rpc['xbt'].ch['htlcs']=[dict(id=self.counter,direction='in',payment_hash=h,amount_msat=3000000,
            expiry=1300,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
    def complete(self,swap_id,h,failed=False):
        self.hold(h);swaps.tick(self.root,factory=self.remote)
        p=self.rpc['btc'].payments[h][0];p['status']='failed' if failed else 'complete'
        if not failed:p['payment_preimage']=self.rpc['btc'].preimages[h]
        swaps.tick(self.root,factory=self.remote);swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,swap_id)[0]['phase'],'failed' if failed else 'settled')
    def test_lost_send_and_release_replies_never_duplicate(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['btc'].drop='sendpay'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].drop=None;swaps.tick(self.root,factory=self.remote)
        p=self.rpc['btc'].payments[h][0];p.update(status='complete',payment_preimage=self.rpc['btc'].preimages[h])
        self.rpc['xbt'].drop='reverse-release'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['xbt'].drop=None;swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'settled')
        for role,method in [('xbt','reverse-release'),('btc','sendpay')]:
            self.assertEqual(len([c for c in self.rpc[role].calls if c[0]==method]),1)

    def test_expiry_and_pause_preserve_enrolled_recovery(self):
        self.setup();s,h=self.draft();self.approve(s)
        for role,node in self.nodes.items():
            token=json.loads(self.tokens[role]);state=node.read(token['session_id']);state['expires_at']=0;state['paused']=True;node.write(state)
        self.complete(s,h)
    def test_restore_blocks_all_old_execution_and_setup(self):
        self.setup();s,h=self.draft();self.approve(s)
        lifecycle.restored(self.root/'execution')
        with self.assertRaisesRegex(ValueError,'restored_pilot_authority_blocked'):swaps.tick(self.root,factory=self.remote)
        with self.assertRaisesRegex(ValueError,'repeat_pairing_changed'):self.draft()
        for role,node in self.nodes.items():
            save(node.root/'forward-pilot-restored.json',{'blocked':True})
            with self.assertRaisesRegex(ValueError,'restored_authority_blocked'):
                node.call(json.loads(self.tokens[role])['session_id'],'observe','',s,'')

if __name__=='__main__':unittest.main()
