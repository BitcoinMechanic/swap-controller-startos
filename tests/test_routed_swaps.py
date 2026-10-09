"""Routed grants, exact attempts and dynamically bound incoming protection."""
import copy
import hashlib
import json
import time
import unittest
from unittest.mock import patch
import test_repeat_swaps as base
from test_repeat_swaps import pilot, swaps, swap_setup, swap_session, load, canonical
from pilot_contract import digest, validate, route, sent_amount, incoming_pins
from routed_plan import plan
import routed_invoice
from pilot_node import RPCError

RECIPIENT='03'+'9'*64

class RoutedRPC(base.RPC):
    def __init__(self,role):
        super().__init__(role)
        self.ch['updates']={'remote':dict(fee_base_msat=1000,fee_proportional_millionths=0,cltv_expiry_delta=6,htlc_minimum_msat=0,htlc_maximum_msat=500000000)}
        self.extra=copy.deepcopy(self.ch)
        self.extra.update(channel_id='8'*64,short_channel_id='2x2x0',funding_txid='a'*64,peer_id='02'+'7'*64)
    def __call__(self,method,**params):
        if method=='listpeerchannels':return dict(channels=copy.deepcopy([self.ch,self.extra] if self.role=='btc' else [self.ch]))
        if method=='getroutes':
            self.calls.append((method,copy.deepcopy(params)))
            assert params['maxparts']==1 and params['maxfee_msat']<=10000 and params['maxdelay']<=80
            nodes=[self.id,self.ch['peer_id'],RECIPIENT];path=[]
            for i,scid in enumerate([self.ch['short_channel_id'],'3x3x0']):
                path.append(dict(short_channel_id_dir=scid+'/'+str(int(nodes[i]>nodes[i+1])),node_id_in=nodes[i],node_id_out=nodes[i+1],
                    amount_in_msat=2001000,amount_out_msat=2001000 if i==0 else 2000000,cltv_in=46,cltv_out=46 if i==0 else 40))
            return dict(routes=[dict(amount_msat=2000000,final_cltv=40,path=path)])
        if method=='close' and params['id']==self.extra['channel_id']:
            self.calls.append((method,copy.deepcopy(params)));self.extra['state']='ONCHAIN'
            if self.drop==method:raise ValueError('lost_reply')
            return dict(type='unilateral')
        try:return super().__call__(method,**params)
        finally:
            if method=='sendpay' and self.payments.get(params['payment_hash']):
                self.payments[params['payment_hash']][0]['amount_sent_msat']=params['route'][0]['amount_msat']
            if method in ('xbt-release-bound','xbt-fail'):self.extra['htlcs']=[]

class RoutedTests(unittest.TestCase):
    def setUp(self):
        with patch.object(base,'RPC',RoutedRPC):base.RepeatTests.setUp(self)
        p=patch.object(routed_invoice,'add',lambda unsigned,routes:unsigned);p.start();self.addCleanup(p.stop)
    approve=base.RepeatTests.approve
    complete=base.RepeatTests.complete
    def setup(self,count=3):
        for role,node in self.nodes.items():
            with patch.object(node,'enable_gate',lambda:self.rpc['btc'].__setattr__('repeat',True)):
                self.tokens[role]=node.enable('',count,True,routed_grant=True)['credential']
        swap_setup.configure(self.root,dict(btcRune='read-btc',xbtRune='read-xbt',confirmed=True),factory=self.inspector)
        swaps.configure(self.root,dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True),factory=self.remote)
    def draft(self):
        self.counter+=1;preimage='%064x'%self.counter;h=hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice='lnxbt-routed-'+str(self.counter)
        self.rpc['xbt'].invoices[invoice]=dict(valid=True,type='bolt11 invoice',currency='xbt',amount_msat=2000000,
            payment_hash=h,payment_secret='b'*64,payee=RECIPIENT,created_at=int(time.time()),expiry=3600,min_final_cltv_expiry=18)
        self.rpc['xbt'].preimages[h]=preimage
        prepared=swaps.prepare(self.root,dict(invoice=invoice),factory=self.remote,inspector=self.inspector)
        self.assertEqual(prepared['routing_fee_msat'],1000);self.assertTrue(prepared['routed'])
        return prepared['pilot_id'],h
    def hold(self,h):
        self.rpc['btc'].quotes[h].update(phase='held',binding=['2x2x0',self.counter])
        self.rpc['btc'].extra['htlcs']=[dict(id=self.counter,direction='in',payment_hash=h,amount_msat=1000000,
            expiry=1300,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
    def test_two_routed_swaps_bind_actual_channel_and_fee_bearing_attempt_once(self):
        self.setup()
        for _ in range(2):
            s,h=self.draft();r,_=pilot.record(self.root,s)
            self.assertNotEqual(r['contract']['channels']['xbt']['peer_id'],RECIPIENT)
            self.assertEqual(r['contract']['channels']['btc']['short_channel_id'],'1x1x0')
            self.approve(s);self.complete(s,h)
            r,_=pilot.record(self.root,s);self.assertEqual(r['incoming_pin']['short_channel_id'],'2x2x0')
            node_record=load(self.nodes['btc'].records()/(s+'.json'))
            self.assertEqual(node_record['incoming_pin'],r['incoming_pin'])
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['xbt'].calls),2)
    def test_high_fee_anchor_swaps_prepare_confirm_and_settle_once(self):
        self.setup()
        for fee in (1255,5000):
            for ch in (self.rpc['btc'].ch,self.rpc['btc'].extra,self.rpc['xbt'].ch):
                ch.update(features=['option_static_remotekey','option_anchors'],feerate={'perkw':fee})
            s,h=self.draft();self.approve(s);self.complete(s,h)
            self.assertEqual(load(self.nodes['btc'].records()/(s+'.json'))['incoming_pin']['short_channel_id'],'2x2x0')
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['xbt'].calls),2)
        self.assertEqual(sum(m=='xbt-release-bound' for m,p in self.rpc['btc'].calls),2)
    def test_anchor_type_never_bypasses_actual_trimmed_htlc_guard(self):
        self.setup()
        for ch in (self.rpc['btc'].ch,self.rpc['btc'].extra,self.rpc['xbt'].ch):
            ch.update(features=['option_anchors'],feerate={'perkw':1255})
        s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['btc'].extra['htlcs'][0]['local_trimmed']=True
        with self.assertRaisesRegex(ValueError,'committed_incoming_htlc_required'):
            swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['xbt'].calls))
    def test_close_uses_actual_incoming_not_preparation_channel_and_lost_reply_not_repeated(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h);swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].height=1228;self.rpc['btc'].drop='close'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].drop=None;swaps.tick(self.root,factory=self.remote)
        closes=[p for m,p in self.rpc['btc'].calls if m=='close']
        self.assertEqual(closes,[dict(id=self.rpc['btc'].extra['channel_id'],unilateraltimeout=1)])
        self.assertEqual(self.rpc['btc'].ch['state'],'CHANNELD_NORMAL')
    def refused_incoming(self,change):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        if change=='funding':self.rpc['btc'].extra['funding_txid']='d'*64
        elif change=='binding':self.rpc['btc'].quotes[h]['binding'][0]='9x9x9'
        else:self.rpc['btc'].extra['htlcs'][0]['state']='RCVD_ADD_HTLC'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['xbt'].calls))
    def test_changed_incoming_funding_refuses_before_send(self):self.refused_incoming('funding')
    def test_unapproved_incoming_channel_refuses_before_send(self):self.refused_incoming('binding')
    def test_uncommitted_incoming_refuses_before_send(self):self.refused_incoming('uncommitted')
    def test_bound_channel_never_rebinds(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h);swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].quotes[h]['binding'][0]='1x1x0'
        with self.assertRaisesRegex(ValueError,'incoming_binding_changed'):swaps.tick(self.root,factory=self.remote)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['xbt'].calls),1)
    def history_rpc(self, role, r, *, archived=False, change=None):
        original=self.rpc[role];pin=r.get('incoming_pin',r['contract']['channels'][role])
        old=copy.deepcopy(original.extra if role=='btc' else original.ch)
        old.update(pin);old.update(state='ONCHAIN',htlcs=[])
        history=[] if 'retire' in r else [dict(short_channel_id=pin['short_channel_id'],
            payment_hash=r['contract']['payment_hash'],id=r.get('incoming_binding',['',0])[1],
            expiry=r.get('incoming_expiry',1100),direction='in' if role=='btc' else 'out',
            amount_msat=1000000 if role=='btc' else sent_amount(r['contract']),
            state='SENT_REMOVE_ACK_REVOCATION' if role=='btc' else 'RCVD_REMOVE_ACK_REVOCATION')]
        if change:change(old,history)
        def rpc(method,**params):
            if method=='listpeerchannels':
                survivors=[copy.deepcopy(c) for c in ([original.ch,original.extra] if role=='btc' else [original.ch]) if c['channel_id']!=pin['channel_id']]
                return dict(channels=survivors+([] if archived else [old]))
            if method=='listclosedchannels':return dict(closedchannels=[old] if archived else [])
            if method=='listhtlcs':
                self.assertEqual(params,dict(id=pin['channel_id']));return dict(htlcs=history)
            return original(method,**params)
        return rpc
    def test_closed_completed_channel_renews_without_rebinding_or_rewriting_history(self):
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h)
        node=self.nodes['btc'];path=node.records()/(s+'.json');before=path.read_bytes();r=load(path)
        old_token=json.loads(self.tokens['btc'])
        node.rpc=self.history_rpc('btc',r)
        with patch.object(node,'enable_gate',lambda:None):
            new=node.enable('',3,True,new_grant=True,routed_grant=True)
        state=node.read(json.loads(new['credential'])['session_id'])
        self.assertEqual(state['channels'],[{k:self.rpc['btc'].ch[k] for k in swap_session.FIELDS}])
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse(node.call(old_token['session_id'],'info','','','')['current'])
        self.assertEqual(node.call(old_token['session_id'],'enroll',canonical(r['contract']),'',''),dict(pilot_id=s))
        self.tokens['btc']=new['credential']
        swaps.configure(self.root,dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True),factory=self.remote)
        # Controller inspection sees the same closed historical channel.
        self.rpc['btc'].extra['state']='ONCHAIN'
        s2,h2=self.draft();self.approve(s2)
        original_hold=self.hold
        self.hold=lambda payment_hash:base.RepeatTests.hold(self,payment_hash)
        try:self.complete(s2,h2)
        finally:self.hold=original_hold
        self.assertEqual(load(node.records()/(s2+'.json'))['incoming_pin']['channel_id'],self.rpc['btc'].ch['channel_id'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['xbt'].calls),2)
    def test_closed_and_archived_terminal_payments_on_both_nodes(self):
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h)
        for role,node in self.nodes.items():
            r=load(node.records()/(s+'.json'))
            for archived in (False,True):
                with self.subTest(role=role,archived=archived):
                    node.rpc=self.history_rpc(role,r,archived=archived)
                    node.terminal(r)
    def test_failed_closed_history_and_unpaid_retirement(self):
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h,True)
        for role,node in self.nodes.items():
            r=load(node.records()/(s+'.json'));node.rpc=self.history_rpc(role,r);node.terminal(r)
            node.rpc=self.rpc[role]
        s,h=self.draft();self.approve(s);future=pilot.record(self.root,s)[0]['terms']['expires_at']+1
        for node in self.nodes.values():node.clock=lambda:future
        with patch.object(swaps.time,'time',lambda:future):swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'expired')
        for role,node in self.nodes.items():
            r=load(node.records()/(s+'.json'))
            # Unpaid BTC quote used the preparation channel, not the extra channel.
            node.rpc=self.history_rpc(role,r,archived=True);node.terminal(r)
    def test_closed_history_refuses_missing_wrong_or_unfinished_htlc(self):
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h)
        changes=[lambda c,hs:hs.clear(),lambda c,hs:hs.append(copy.deepcopy(hs[0])),
            lambda c,hs:hs[0].update(state='RCVD_REMOVE_ACK_COMMIT'),
            lambda c,hs:hs[0].update(amount_msat=1),lambda c,hs:hs[0].update(payment_hash='f'*64),
            lambda c,hs:c.update(funding_txid='e'*64),lambda c,hs:c.update(htlcs=[dict(id=9)])]
        for role,node in self.nodes.items():
            r=load(node.records()/(s+'.json'))
            for change in changes:
                node.rpc=self.history_rpc(role,r,change=change)
                with self.assertRaises(ValueError):node.terminal(r)
        node=self.nodes['btc'];r=load(node.records()/(s+'.json'))
        for field in ('id','expiry'):
            node.rpc=self.history_rpc('btc',r,change=lambda c,hs:hs[0].update({field:999}))
            with self.assertRaisesRegex(ValueError,'previous_htlc_not_terminal'):node.terminal(r)
    def test_archived_history_cannot_bypass_binding_funding_outcome_or_close_guard(self):
        self.setup();s,h=self.draft();self.approve(s);self.complete(s,h)
        node=self.nodes['btc'];r=load(node.records()/(s+'.json'))
        node.rpc=self.history_rpc('btc',r,archived=True,change=lambda c,hs:c.update(funding_outnum=1))
        with self.assertRaisesRegex(ValueError,'previous_channel_history_unavailable'):node.terminal(r)
        node.rpc=self.history_rpc('btc',r,archived=True)
        for changed in [dict(r,close={}),dict(r,incoming_binding=['1x1x0',1]),dict(r,release={}),dict(r,fail={})]:
            with self.assertRaises(ValueError):node.terminal(changed)
        self.rpc['btc'].quotes[h]['phase']='held'
        with self.assertRaisesRegex(ValueError,'previous_gate_not_terminal'):node.terminal(r)
        self.rpc['xbt'].payments[h][0]['status']='pending'
        node=self.nodes['xbt'];r=load(node.records()/(s+'.json'));node.rpc=self.history_rpc('xbt',r,archived=True)
        with self.assertRaisesRegex(ValueError,'previous_attempt_not_terminal'):node.terminal(r)
    def test_unapproved_ready_channel_reports_grant_problem(self):
        self.setup();node=self.nodes['btc'];state=node.read(json.loads(self.tokens['btc'])['session_id'])
        state['channels']=[state['channels'][1]];state['channel']=state['channels'][0];node.write(state)
        self.rpc['btc'].extra['state']='ONCHAIN'
        with self.assertRaisesRegex(ValueError,'incoming_channel_outside_grant'):self.draft()
        self.assertFalse(any(m in ('xbt-register','sendpay') for rpc in self.rpc.values() for m,p in rpc.calls))
    def test_incoming_diagnostics_preserve_fee_liquidity_and_pending_checks(self):
        self.setup()
        cases=[('no_eligible_incoming_channels',dict(receivable_msat=999999)),
               ('incoming_amount_trimmed',dict(feerate=dict(perkw=1000))),
               ('incoming_channels_busy',dict(htlcs=[dict(id=1)])),
               ('incoming_channels_not_ready',dict(peer_connected=False))]
        for reason,change in cases:
            originals=[copy.deepcopy(c) for c in (self.rpc['btc'].ch,self.rpc['btc'].extra)]
            for c in (self.rpc['btc'].ch,self.rpc['btc'].extra):c.update(change)
            with self.assertRaisesRegex(ValueError,reason):self.draft()
            self.rpc['btc'].ch,self.rpc['btc'].extra=originals
    def test_fresh_node_adapters_recover_original_route_after_lost_send_reply(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['xbt'].drop='sendpay'
        with self.assertRaises(ValueError):swaps.tick(self.root,factory=self.remote)
        original=copy.deepcopy(pilot.record(self.root,s)[0])
        plans=sum(m=='getroutes' for m,p in self.rpc['xbt'].calls)
        self.rpc['xbt'].drop=None
        self.nodes={role:swap_session.Session(node.root,role,self.rpc[role]) for role,node in self.nodes.items()}
        for _ in range(2):swaps.tick(self.root,factory=self.remote)
        p=self.rpc['xbt'].payments[h][0]
        p.update(status='complete',payment_preimage=self.rpc['xbt'].preimages[h])
        swaps.tick(self.root,factory=self.remote);swaps.tick(self.root,factory=self.remote)
        recovered=pilot.record(self.root,s)[0]
        self.assertEqual(recovered['phase'],'settled')
        for key in ('contract','binding','expiry','incoming_pin'):
            self.assertEqual(recovered[key],original[key])
        self.assertEqual(sum(m=='getroutes' for m,p in self.rpc['xbt'].calls),plans)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['xbt'].calls),1)
        self.assertEqual(sum(m=='xbt-release-bound' for m,p in self.rpc['btc'].calls),1)
    def test_old_grant_cannot_gain_routed_authority(self):
        self.setup();s,h=self.draft();r,_=pilot.record(self.root,s)
        for role,node in self.nodes.items():
            state=node.read(json.loads(self.tokens[role])['session_id']);state['routed']=False;node.write(state)
            with self.assertRaisesRegex(ValueError,'outside_session_channel'):node.enroll(state,r['contract'])
            with patch.object(node,'enable_gate',lambda:None),self.assertRaisesRegex(ValueError,'existing_grant_differs'):
                node.enable('',3,True,routed_grant=True)
    def test_route_changes_fee_delay_loop_and_mpp_rejected(self):
        self.setup();s,h=self.draft();c=pilot.record(self.root,s)[0]['contract']
        for key,value in [('amount_msat',2010001),('delay',81),('id',c['nodes']['xbt']),('channel','3x3x0')]:
            bad=copy.deepcopy(c);bad['route'][0][key]=value
            with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(c);bad['route']*=3
        with self.assertRaises(ValueError):validate(bad)
        self.approve(s);self.hold(h);swaps.tick(self.root,factory=self.remote)
        self.rpc['xbt'].payments[h].append(copy.deepcopy(self.rpc['xbt'].payments[h][0]))
        with self.assertRaisesRegex(ValueError,'original_attempt_required'):swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='xbt-release-bound' for m,p in self.rpc['btc'].calls))
    test_lost_send_and_release_replies_never_duplicate=base.RepeatTests.test_lost_send_and_release_replies_never_duplicate
    test_restore_blocks_all_old_execution_and_setup=base.RepeatTests.test_restore_blocks_all_old_execution_and_setup
    test_expiry_and_pause_preserve_enrolled_recovery=base.RepeatTests.test_expiry_and_pause_preserve_enrolled_recovery
    def test_private_hint_and_transport_failure_handling(self):
        source='02'+'1'*64;middle='02'+'2'*64
        invoice=dict(valid=True,type='bolt11 invoice',currency='xbt',amount_msat=2000000,payee=RECIPIENT,min_final_cltv_expiry=18,
            routes=[[dict(pubkey=source,short_channel_id='1x1x1',fee_base_msat=0,fee_proportional_millionths=0,cltv_expiry_delta=0),
                     dict(pubkey=middle,short_channel_id='2x2x2',fee_base_msat=1000,fee_proportional_millionths=0,cltv_expiry_delta=6)]])
        def rpc(method,**params):
            if method=='decode':return invoice
            raise RPCError(205)
        hops=plan(rpc,'lnxbt-test',source);self.assertEqual(hops[0]['amount_msat'],2001000)
        self.assertEqual(hops[-1]['id'],RECIPIENT)
        def broken(method,**params):
            if method=='decode':return invoice
            raise RPCError(None)
        with self.assertRaisesRegex(ValueError,'rpc_unavailable'):plan(broken,'lnxbt-test',source)

if __name__=='__main__':unittest.main()
