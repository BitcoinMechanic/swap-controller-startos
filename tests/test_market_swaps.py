"""Market quote arithmetic, durable authority and both directional state machines."""
import copy
import hashlib
import json
import time
import unittest
from unittest.mock import patch
import test_routed_swaps as forward
import test_reverse_routed_swaps as reverse
import market_terms as mt
import market_pricing as pricing
from controller import save

LIMITS=dict(max_btc_msat=10000000,max_xbt_msat=500000000,total_btc_msat=20000000,total_xbt_msat=1000000000)

def book(kind):
    if kind=='ticker':return dict(success=True,pair='BTCB2_BTC',ticker=dict(computedAt=int(time.time()*1000),bestBid='0.0099',bestAsk='0.01'))
    return dict(success=True,pair='BTCB2_BTC',asks=[dict(price='0.01',quantity='2')],bids=[dict(price='0.0099',quantity='2')])

class Forward(unittest.TestCase):
    module=forward
    incoming='btc'
    outgoing='xbt'
    amount=100000000
    direction='forward'
    def setUp(self):
        m=self.module;parent=self;incoming=self.incoming
        class RPC(m.RoutedRPC):
            def __call__(self,method,**params):
                if method=='xbt-register' and params['quote']['payment_hash'] in self.quotes:
                    self.calls.append((method,copy.deepcopy(params)))
                    assert self.quotes[params['quote']['payment_hash']]['terms']==params['quote']
                    return {'registered':True}
                if method=='getroutes':
                    self.calls.append((method,copy.deepcopy(params)))
                    n=parent.amount;nodes=[self.id,self.ch['peer_id'],m.RECIPIENT];path=[]
                    for i,scid in enumerate([self.ch['short_channel_id'],'3x3x0']):
                        path.append(dict(short_channel_id_dir=scid+'/'+str(int(nodes[i]>nodes[i+1])),node_id_in=nodes[i],node_id_out=nodes[i+1],amount_in_msat=n+1000,amount_out_msat=n+1000 if i==0 else n,cltv_in=46,cltv_out=46 if i==0 else 40))
                    return dict(routes=[dict(amount_msat=n,final_cltv=40,path=path)])
                result=super().__call__(method,**params)
                if method=='xbt-pilot-info':result['profile']=mt.GATE
                if method=='decode' and params['string'].startswith('signed-'):
                    result['amount_msat']=self.quotes[params['string'][7:]]['terms'][incoming+'_amount_msat']
                return result
        with patch.object(m.base,'RPC',RPC):m.base.RepeatTests.setUp(self)
        if self.direction=='reverse':
            p=patch.object(reverse.reverse_invoice,'unsigned',lambda *a,**kw:'unsigned');p.start();self.addCleanup(p.stop)
        p=patch.object(m.routed_invoice if self.direction=='forward' else m.reverse_invoice,'add',lambda u,r:u);p.start();self.addCleanup(p.stop)
        real_make=pricing.make
        p=patch.object(pricing,'make',lambda root,direction,route:real_make(root,direction,route,reader=book));p.start();self.addCleanup(p.stop)
    def setup(self,limits=LIMITS,market=True):
        m=self.module
        for role,node in self.nodes.items():
            with patch.object(node,'enable_gate',lambda:None):
                kw=dict(routed_grant=True,market_limits=copy.deepcopy(limits) if market else None)
                if self.direction=='reverse':kw['max_delay']=288
                self.tokens[role]=node.enable('',3,True,**kw)['credential']
        m.swap_setup.configure(self.root,dict(btcRune='read-btc',xbtRune='read-xbt',confirmed=True),factory=self.inspector)
        m.swaps.configure(self.root,dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True),factory=self.remote)
    def draft(self):
        m=self.module;self.counter+=1;preimage='%064x'%self.counter;h=hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice=('lnbc-' if self.direction=='reverse' else 'lnxbt-')+str(self.counter)
        self.rpc[self.outgoing].invoices[invoice]=dict(valid=True,type='bolt11 invoice',currency='bc' if self.outgoing=='btc' else 'xbt',amount_msat=self.amount,payment_hash=h,payment_secret='b'*64,payee=m.RECIPIENT,created_at=int(time.time()),expiry=3600,min_final_cltv_expiry=18)
        self.rpc[self.outgoing].preimages[h]=preimage
        r=m.swaps.prepare(self.root,dict(invoice=invoice),factory=self.remote,inspector=self.inspector)
        return r['pilot_id'],h
    def approve(self,s):return self.module.base.RepeatTests.approve(self,s)
    def hold(self,h):
        g=self.rpc[self.incoming].quotes[h];g.update(phase='held',binding=['2x2x0',self.counter])
        self.rpc[self.incoming].extra['htlcs']=[dict(id=self.counter,direction='in',payment_hash=h,amount_msat=g['terms'][self.incoming+'_amount_msat'],expiry=1300,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
    def complete(self,s,h,failed=False):return self.module.base.RepeatTests.complete(self,s,h,failed)
    def test_market_settlement_and_failed_swap_budget_not_refunded(self):
        self.setup();s,h=self.draft();r,_=self.module.pilot.record(self.root,s)
        self.assertEqual(r['contract']['pricing']['markup_bps'],0)
        self.assertEqual(self.module.swaps.review(self.root,s)['admission_until'],r['contract']['pricing']['expires_at'])
        self.assertNotEqual(mt.amounts(r['contract'])[self.incoming],1000000 if self.incoming=='btc' else 3000000)
        self.approve(s);self.complete(s,h)
        s2,h2=self.draft();self.approve(s2);self.complete(s2,h2,True)
        for role,node in self.nodes.items():
            grant=node.read(json.loads(self.tokens[role])['session_id'])
            self.assertEqual(len(grant['market_reserved']),2)
            self.assertEqual(set(grant['market_reserved']),set(grant['enrolled']))
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc[self.outgoing].calls),2)
    def test_markup_change_and_source_outage_do_not_reprice_pending_recovery(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc[self.outgoing].drop='sendpay'
        with self.assertRaises(ValueError):self.module.swaps.tick(self.root,factory=self.remote)
        self.rpc[self.outgoing].drop=None
        original=self.module.pilot.record(self.root,s)[0]['contract']
        pricing.configure(self.root,dict(markupBps=100))
        for role,node in list(self.nodes.items()):self.nodes[role]=type(node)(node.root,role,self.rpc[role])
        with patch.object(pricing,'make',side_effect=AssertionError('reprice')):
            self.module.swaps.tick(self.root,factory=self.remote)
            p=self.rpc[self.outgoing].payments[h][0];p.update(status='complete',payment_preimage=self.rpc[self.outgoing].preimages[h])
            self.module.swaps.tick(self.root,factory=self.remote);self.module.swaps.tick(self.root,factory=self.remote)
        r,_=self.module.pilot.record(self.root,s);self.assertEqual(r['phase'],'settled');self.assertEqual(r['contract'],original)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc[self.outgoing].calls),1)
    def test_held_before_expiry_recovers_original_price_after_expiry(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        c=self.module.pilot.record(self.root,s)[0]['contract'];future=c['pricing']['expires_at']+1
        for n in self.nodes.values():n.clock=lambda:future
        with patch.object(time,'time',return_value=future),patch.object(pricing,'make',side_effect=AssertionError('reprice')):
            self.module.swaps.tick(self.root,factory=self.remote)
        self.assertEqual(self.module.pilot.record(self.root,s)[0]['phase'],'send_intent')
        self.assertEqual(len(self.rpc[self.outgoing].payments[h]),1)
        self.assertEqual(self.module.pilot.record(self.root,s)[0]['contract'],c)
    def test_publication_lost_sign_reply_uses_original_unsigned_quote(self):
        self.setup();s,h=self.draft();node=self.nodes[self.incoming];original=node.rpc;unsigned=[]
        def lost(method,**params):
            reply=original(method,**params)
            if method=='signinvoice':
                unsigned.append(params['invstring'])
                if len(unsigned)==1:raise ValueError('lost_sign_reply')
            return reply
        node.rpc=lost
        with self.assertRaises(ValueError):self.approve(s)
        before=self.module.pilot.record(self.root,s)[0]['contract']
        self.module.swaps.tick(self.root,factory=self.remote)
        self.assertEqual(self.module.pilot.record(self.root,s)[0]['phase'],'waiting_for_'+self.incoming)
        self.assertEqual(unsigned,[unsigned[0]]*2)
        self.assertEqual(self.module.pilot.record(self.root,s)[0]['contract'],before)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc[self.outgoing].calls))
    def test_old_grant_refuses_market_contract_even_at_old_amounts(self):
        self.setup();s,h=self.draft();c=self.module.pilot.record(self.root,s)[0]['contract']
        node=self.nodes[self.outgoing];state=node.read(json.loads(self.tokens[self.outgoing])['session_id'])
        state.pop('market_limits');state.pop('market_reserved')
        with self.assertRaisesRegex(ValueError,'market_grants_required'):node.enroll(state,c)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc[self.outgoing].calls))
    def test_tampered_price_and_route_are_rejected(self):
        self.setup();s,h=self.draft();c=self.module.pilot.record(self.root,s)[0]['contract']
        for field,value in [('markup_bps',100),('pair','XBT_BTC'),('btc_msat',1),('expires_at',1)]:
            bad=copy.deepcopy(c);bad['pricing'][field]=value
            with self.assertRaises(ValueError):self.module.validate(bad)
    def test_expired_review_never_enrolls(self):
        self.setup();s,h=self.draft();r,_=self.module.pilot.record(self.root,s)
        with patch.object(time,'time',return_value=r['contract']['pricing']['expires_at']):
            with self.assertRaisesRegex(ValueError,'market_quote_expired'):self.approve(s)
        self.assertTrue(all(not n.read(json.loads(self.tokens[k])['session_id'])['enrolled'] for k,n in self.nodes.items()))
    def test_expired_partial_authorization_retires_without_payment(self):
        self.setup();s,h=self.draft();m=self.module;r,_=m.pilot.record(self.root,s)
        c=r['contract'];node=self.nodes[self.incoming];state=node.read(json.loads(self.tokens[self.incoming])['session_id']);node.enroll(state,c)
        r['phase']='authorizing';save(m.pilot.directory(self.root,s)/'record.json',r)
        save(m.pilot.directory(self.root,s)/'credentials.json',self.tokens)
        future=c['pricing']['expires_at']+1
        for n in self.nodes.values():n.clock=lambda:future
        with patch.object(time,'time',return_value=future):m.swaps.tick(self.root,factory=self.remote)
        self.assertEqual(m.pilot.record(self.root,s)[0]['phase'],'expired')
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc[self.outgoing].calls))
        for n in self.nodes.values():n.prior()
    def test_aggregate_budget_blocks_second_quote_without_new_slot(self):
        policy=copy.deepcopy(LIMITS);policy['max_btc_msat']=policy['total_btc_msat']=1600000
        policy['max_xbt_msat']=policy['total_xbt_msat']=160000000
        self.setup(policy);s,h=self.draft();self.approve(s);self.complete(s,h)
        with self.assertRaisesRegex(ValueError,'market_budget_exhausted'):self.draft()
        for role,n in self.nodes.items():self.assertEqual(len(n.read(json.loads(self.tokens[role])['session_id'])['enrolled']),1)

class Reverse(Forward):
    module=reverse
    incoming='xbt'
    outgoing='btc'
    amount=1500000
    direction='reverse'

if __name__=='__main__':unittest.main()
