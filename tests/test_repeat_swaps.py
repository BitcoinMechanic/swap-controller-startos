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
import forward_pilot as pilot
import forward_swaps as swaps
import swap_setup
import lifecycle
import swap_session
from pilot_node import Node, load, save, locked
from pilot_contract import canonical,digest

class RPC:
    def __init__(self,role):
        self.role=role;self.id='02'+('1' if role=='btc' else '2')*64;self.height=1000
        self.ch=dict(channel_id=('3' if role=='btc' else '4')*64,short_channel_id='1x1x'+('0' if role=='btc' else '1'),
            funding_txid='5'*64,funding_outnum=0,peer_id='03'+'6'*64,state='CHANNELD_NORMAL',peer_connected=True,
            htlcs=[],spendable_msat=600000000,receivable_msat=600000000,feerate={'perkw':253},dust_limit_msat=546000)
        self.quotes={};self.payments={};self.invoices={};self.preimages={};self.calls=[];self.last=None;self.repeat=False;self.drop=None
    def __call__(self,method,**params):
        self.calls.append((method,copy.deepcopy(params)))
        if method=='getinfo':return dict(id=self.id,network='bitcoin' if self.role=='btc' else 'xbt',blockheight=self.height)
        if method=='listpeerchannels':return {'channels':[copy.deepcopy(self.ch)]}
        if method=='listfunds':return {'outputs':[dict(amount_msat=50000000,status='confirmed',reserved=False)]}
        if method=='decode':
            value=params['string']
            if value.startswith('signed-'):
                h=value[7:];return dict(valid=True,currency='bc',payee=self.id,payment_hash=h,
                    payment_secret=self.quotes[h]['terms']['payment_secret'],amount_msat=1000000,min_final_cltv_expiry=300)
            return copy.deepcopy(self.invoices[value])
        if method=='listsendpays':return {'payments':copy.deepcopy(self.payments.get(params['payment_hash'],[]))}
        if method=='xbt-pilot-info':return dict(profile=swap_session.PROFILE if self.repeat else 'live-pilot-v1',registered_quotes=len(self.quotes))
        if method=='createrune':return dict(rune='private-'+str(len(self.calls)),unique_id=str(len(self.calls)))
        if method=='xbt-register':
            q=params['quote'];assert q['payment_hash'] not in self.quotes
            assert all(g['phase'] in ('resolved','failed','expired') for g in self.quotes.values())
            self.quotes[q['payment_hash']]=dict(terms=q,phase='quoted',payment_hash=q['payment_hash']);self.last=q['payment_hash'];return {'registered':True}
        if method=='signinvoice':return {'bolt11':'signed-'+self.last}
        if method=='xbt-retire-repeat':
            g=self.quotes[params['payment_hash']];assert g['phase'] in ('quoted','expired') and not g.get('binding')
            g['phase']='expired';return {'retired':True}
        if method=='xbt-quote-status':return copy.deepcopy(self.quotes[params['payment_hash']])
        if method=='xbt-spend-info':
            g=self.quotes[params['payment_hash']];return dict(g['terms'],binding=g['binding'],cltv_expiry=1300)
        if method=='sendpay':
            h=params['payment_hash'];assert not self.payments.get(h)
            self.payments[h]=[dict(payment_hash=h,groupid=1,partid=0,amount_sent_msat=2000000,status='pending')];result={'submitted':True}
        elif method in ('xbt-release-bound','xbt-fail'):
            h=params['payment_hash'];g=self.quotes[h];assert g['phase']=='held'
            if method=='xbt-release-bound':assert hashlib.sha256(bytes.fromhex(params['preimage'])).hexdigest()==h
            g['phase']='resolved' if method=='xbt-release-bound' else 'failed';self.ch['htlcs']=[];result={'done':True}
        elif method=='close':self.ch['state']='ONCHAIN';result={'type':'unilateral'}
        else:raise AssertionError(method)
        if self.drop==method:raise ValueError('lost_reply')
        return result

class RepeatTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'controller';self.root.mkdir();lifecycle.setup(self.root/'execution')
        self.rpc={k:RPC(k) for k in ('btc','xbt')};self.nodes={};self.tokens={}
        self.config=dict(schema=1,generation='c'*32,nodes={k:dict(node_id=v.id,role=k) for k,v in self.rpc.items()})
        for module in (swaps,swap_setup,pilot):
            p=patch.object(module,'load_config',lambda root:self.config);p.start();self.addCleanup(p.stop)
        p=patch.dict(sys.modules,swap_invoice=types.SimpleNamespace(unsigned_invoice=lambda *a,**k:'unsigned'))
        p.start();self.addCleanup(p.stop)
        for role in self.rpc:
            root=Path(self.tmp.name)/role;root.mkdir();self.nodes[role]=swap_session.Session(root,role,self.rpc[role])
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
            with patch.object(node,'enable_gate',lambda:self.rpc['btc'].__setattr__('repeat',True)):
                self.tokens[role]=node.enable('',count,True)['credential']
        swap_setup.configure(self.root,dict(btcRune='read-btc',xbtRune='read-xbt',confirmed=True),factory=self.inspector)
        swaps.configure(self.root,dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True),factory=self.remote)
    def draft(self):
        self.counter+=1;preimage=('%064x'%self.counter);h=hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice='lnxbt-invoice-'+str(self.counter);now=int(time.time())
        self.rpc['xbt'].invoices[invoice]=dict(valid=True,type='bolt11 invoice',currency='xbt',amount_msat=2000000,
            payment_hash=h,payment_secret='b'*64,payee=self.rpc['xbt'].ch['peer_id'],created_at=now,expiry=3600,min_final_cltv_expiry=18)
        self.rpc['xbt'].preimages[h]=preimage
        result=swaps.prepare(self.root,{'invoice':invoice},factory=self.remote,inspector=self.inspector)
        return result['pilot_id'],h
    def approve(self,swap_id):
        controller.save(self.root/'execution/heartbeat.json',dict(checked_at=int(time.time()),mode='disabled'))
        return swaps.approve(self.root,dict(pilotId=swap_id,confirmed=True),factory=self.remote)
    def hold(self,h):
        gate=self.rpc['btc'].quotes[h];gate.update(phase='held',binding=['1x1x0',self.counter])
        self.rpc['btc'].ch['htlcs']=[dict(id=self.counter,direction='in',payment_hash=h,amount_msat=1000000,
            expiry=1300,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
    def complete(self,swap_id,h,failed=False):
        self.hold(h);swaps.tick(self.root,factory=self.remote)
        p=self.rpc['xbt'].payments[h][0];p['status']='failed' if failed else 'complete'
        if not failed:p['payment_preimage']=self.rpc['xbt'].preimages[h]
        swaps.tick(self.root,factory=self.remote);swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,swap_id)[0]['phase'],'failed' if failed else 'settled')
    def test_two_swaps_one_setup_with_distinct_records_and_single_mutations(self):
        self.setup();first,h=self.draft();self.approve(first);self.complete(first,h)
        original=(pilot.directory(self.root,first)/'record.json').read_bytes()
        second,h2=self.draft();self.approve(second);self.complete(second,h2)
        self.assertNotEqual(first,second);self.assertEqual(original,(pilot.directory(self.root,first)/'record.json').read_bytes())
        for role in self.nodes:
            self.assertEqual(len(self.nodes[role].read(json.loads(self.tokens[role])['session_id'])['enrolled']),2)
        self.assertEqual(len([x for x in self.rpc['xbt'].calls if x[0]=='sendpay']),2)
        self.assertEqual(len([x for x in self.rpc['btc'].calls if x[0]=='xbt-release-bound']),2)
        before=copy.deepcopy(self.rpc['xbt'].calls);swaps.tick(self.root,factory=self.remote);self.assertEqual(before,self.rpc['xbt'].calls)
    def test_expired_unpaid_invoice_retires_without_spending_then_next_swap(self):
        self.setup();s,h=self.draft();self.approve(s)
        r,_=pilot.record(self.root,s);future=r['terms']['expires_at']+1
        for node in self.nodes.values():node.clock=lambda:future
        with patch.object(swaps.time,'time',lambda:future):
            swaps.tick(self.root,factory=self.remote)
            self.assertEqual(pilot.record(self.root,s)[0]['phase'],'expired')
            self.assertEqual(self.rpc['btc'].quotes[h]['phase'],'expired')
        self.assertFalse(any(m=='sendpay' for m,_ in self.rpc['xbt'].calls))
        for role,node in self.nodes.items():
            with self.assertRaises(ValueError):node.call(json.loads(self.tokens[role])['session_id'],'send' if role=='xbt' else 'publish','',s,'')
            node.clock=time.time
        second,h2=self.draft();self.approve(second);self.complete(second,h2)
    def test_accepted_htlc_cannot_be_retired(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        node=self.nodes['btc'];r,_=pilot.record(self.root,s);node.clock=lambda:r['terms']['expires_at']+1
        with self.assertRaisesRegex(ValueError,'quote_was_accepted'):
            node.call(json.loads(self.tokens['btc'])['session_id'],'retire','',s,'')
        self.assertNotIn('retire',load(node.records()/(s+'.json')))
    def test_limit_failed_swap_and_pause_do_not_reset_budget(self):
        self.setup(1);s,h=self.draft();self.approve(s);self.complete(s,h,True)
        with self.assertRaisesRegex(ValueError,'session_expired_or_exhausted'):self.draft()
        for role in self.nodes:self.assertEqual(len(self.nodes[role].read(json.loads(self.tokens[role])['session_id'])['enrolled']),1)
    def test_uncertain_enrollment_reply_reconciles_without_second_slot(self):
        self.setup();s,h=self.draft();original=self.nodes['xbt'].call
        def lost(*args,**kwargs):
            result=original(*args,**kwargs)
            if args[1]=='enroll':raise ValueError('lost enrollment reply')
            return result
        with patch.object(self.nodes['xbt'],'call',lost):
            with self.assertRaises(ValueError):self.approve(s)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'authorizing')
        with self.assertRaises(ValueError):swaps.cancel(self.root,dict(pilotId=s,confirmed=True))
        self.approve(s);self.complete(s,h)
        for role in self.nodes:self.assertEqual(len(self.nodes[role].read(json.loads(self.tokens[role])['session_id'])['enrolled']),1)
    def test_lost_send_and_release_replies_never_duplicate(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['xbt'].drop='sendpay'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['xbt'].drop=None;swaps.tick(self.root,factory=self.remote)
        p=self.rpc['xbt'].payments[h][0];p.update(status='complete',payment_preimage=self.rpc['xbt'].preimages[h])
        self.rpc['btc'].drop='xbt-release-bound'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].drop=None;swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'settled')
        for role,method in [('btc','xbt-release-bound'),('xbt','sendpay')]:
            self.assertEqual(len([c for c in self.rpc[role].calls if c[0]==method]),1)
    def test_pending_deadline_lost_close_reply_is_not_repeated(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].height=1228;self.rpc['btc'].drop='close'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].drop=None
        swaps.tick(self.root,factory=self.remote)
        self.assertEqual(len([c for c in self.rpc['btc'].calls if c[0]=='close']),1)
        with self.assertRaises(ValueError):self.draft()

    def test_stale_confirmation_pending_swap_and_cancel_safety(self):
        self.setup();s,h=self.draft()
        with self.assertRaisesRegex(ValueError,'finish_current_swap_first'):self.draft()
        swaps.cancel(self.root,dict(pilotId=s,confirmed=True));second,h=self.draft()
        with self.assertRaises(ValueError):self.approve(s)
        self.assertEqual(swaps.approval_input(self.root),{'pilotId':second})
        with self.assertRaises(ValueError):swaps.approve(self.root,dict(pilotId=second,confirmed=False),factory=self.remote)
        self.assertEqual(self.rpc['btc'].quotes,{})
    def test_changed_pin_and_expiry_refused_before_new_enrollment(self):
        self.setup();s,h=self.draft();r,_=pilot.record(self.root,s)
        bad=copy.deepcopy(r['contract']);bad['channels']['btc']['funding_outnum']=1
        node=self.nodes['btc'];token=json.loads(self.tokens['btc'])
        with self.assertRaisesRegex(ValueError,'outside_session_channel'):
            node.call(token['session_id'],'enroll',canonical(bad),'','')
        state=node.read(token['session_id']);state['expires_at']=0;node.write(state)
        with self.assertRaises(ValueError):self.approve(s)
        self.assertEqual(state['enrolled'],{})
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
    def test_original_legacy_pilot_record_preserved(self):
        # Real legacy settlement is covered by test_forward_pilot; seed its exact
        # terminal evidence here to exercise migration without deleting anything.
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h)
        r,_=pilot.record(self.root,s);legacy=pilot.directory(self.root);legacy.mkdir();controller.save(legacy/'record.json',r)
        original=(legacy/'record.json').read_bytes()
        second,h2=self.draft();self.approve(second);self.complete(second,h2)
        self.assertEqual(original,(legacy/'record.json').read_bytes())
    def test_grant_renewal_preserves_old_authority_and_blocks_old_enrollment(self):
        self.setup(1);first,h=self.draft();self.approve(first);self.complete(first,h)
        old_tokens=dict(self.tokens)
        for role,node in self.nodes.items():
            with patch.object(node,'enable_gate',lambda:None):
                self.tokens[role]=node.enable('',2,True,new_grant=True)['credential']
            old=json.loads(old_tokens[role])
            self.assertFalse(node.call(old['session_id'],'info','','','')['current'])
            self.assertEqual(node.call(old['session_id'],'observe','',first,'')['pilot_id'],first)
        swaps.configure(self.root,dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True),factory=self.remote)
        second,h2=self.draft();r,_=pilot.record(self.root,second)
        for role,node in self.nodes.items():
            with self.assertRaisesRegex(ValueError,'session_closed'):
                node.call(json.loads(old_tokens[role])['session_id'],'enroll',canonical(r['contract']),'','')
        self.approve(second);self.complete(second,h2)
    def test_reserved_slot_survives_crash_before_record(self):
        self.setup();s,h=self.draft();r,_=pilot.record(self.root,s);node=self.nodes['btc'];token=json.loads(self.tokens['btc'])
        real=swap_session.save
        def fail_record(path,value):
            if path.parent.name=='forward-swaps':raise OSError('crash')
            return real(path,value)
        with patch.object(swap_session,'save',fail_record),self.assertRaises(OSError):
            node.call(token['session_id'],'enroll',canonical(r['contract']),'','')
        self.assertEqual(len(node.read(token['session_id'])['enrolled']),1)
        node.call(token['session_id'],'enroll',canonical(r['contract']),'','')
        self.assertEqual(len(node.read(token['session_id'])['enrolled']),1)
        self.approve(s);self.complete(s,h)
    def test_backup_refuses_active_repeat_and_restore_invalidates_terminal_history(self):
        self.setup();s,h=self.draft()
        with self.assertRaisesRegex(ValueError,'backup_refused_unresolved_execution'):
            lifecycle.backup_begin(self.root/'execution')
        self.approve(s);self.complete(s,h)
        lifecycle.backup_begin(self.root/'execution')
        self.assertEqual(controller.private_load(self.root/'execution/history.json')['jobs'][0]['outcome'],'terminal')
        lifecycle.backup_end(self.root/'execution')
        self.assertEqual(swaps.status(self.root)['swaps'][0]['phase'],'settled')
    def test_session_rune_bound_and_foreign_parameters_refused(self):
        self.setup()
        for role,node in self.nodes.items():
            calls=[p for m,p in self.rpc[role].calls if m=='createrune'];rules=calls[0]['restrictions']
            token=json.loads(self.tokens[role]);self.assertIn(['pnamesession_id='+token['session_id']],rules)
            self.assertIn(['pnum=5'],rules);self.assertIn(['method=swap-session-call'],rules)
            with self.assertRaises(ValueError):node.call(token['session_id'],'send','','f'*64,'')
            with self.assertRaises(ValueError):node.call(token['session_id'],'info','extra','','')

if __name__=='__main__':unittest.main()
