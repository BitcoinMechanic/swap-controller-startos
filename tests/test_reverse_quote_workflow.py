import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import quote_workflow as q
import reverse_quote_workflow as r
import quote_actions as actions
from controller import save,private_load
MODULES=Path(os.environ.get('SWAP_QUOTE_TEST_MODULES','/opt/swap'))

class ReverseQuoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (MODULES/'SOURCE_COMMIT').exists():raise unittest.SkipTest('Run inside controller image or set SWAP_QUOTE_TEST_MODULES')
        cls.core=q.service(MODULES)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.manager=Path(self.tmp.name)/'execution';self.root=self.manager/'jobs'/'swap'
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1');env.start();self.addCleanup(env.stop)
        self.now=int(time.time());self.calls=[];self.terms=None;self.phase='quoted';self.existing=[];self.wrong_signed=False
        self.connections=[dict(network=n,node_id='02'+str(i)*64,rune='PRIVATE',ca_pem=None,
            url='https://localhost:'+str(9000+i),cli=['/never-executed','--network='+n])
            for i,n in enumerate(('regtest','xbt-regtest'),1)]
        self.decoded=dict(valid=True,type='bolt11 invoice',currency='bcrt',payment_hash='aa'*32,
            payment_secret='bb'*32,amount_msat=100000000,payee='03'+'33'*32,
            created_at=self.now,expiry=3600,min_final_cltv_expiry=18)
        self.outgoing=dict(peer_id=self.decoded['payee'],short_channel_id='103x1x0',state='CHANNELD_NORMAL',
            peer_connected=True,spendable_msat=300000000,htlcs=[])
        self.incoming=dict(peer_id='03'+'44'*32,short_channel_id='104x1x0',state='CHANNELD_NORMAL',peer_connected=True,htlcs=[])
        self.request=dict(connections=self.connections,btc_invoice='lnbcrt1fixture',xbt_sats=200000)
        owner=self
        class Fake(r.ReverseRemote):
            def _request(self,method,params):return owner.rpc(self.network,method,params)
        self.factory=Fake

    def rpc(self,network,method,params):
        self.calls.append((network,method,params))
        if method=='getinfo':return dict(id=self.connections[network=='xbt-regtest']['node_id'],network=network,blockheight=110)
        if method=='decode':
            if network=='regtest':return copy.deepcopy(self.decoded)
            return dict(valid=True,currency='xbtrt',payee=self.connections[1]['node_id'],
                payment_hash=self.terms['payment_hash'],payment_secret=self.terms['payment_secret'],
                amount_msat=200000001 if self.wrong_signed else 200000000,min_final_cltv_expiry=120,
                created_at=self.now,expiry=self.terms['expires_at']-self.now-1)
        if method=='listpeerchannels':return {'channels':[copy.deepcopy(self.outgoing if network=='regtest' else self.incoming)]}
        if method=='listsendpays':return {'payments':self.existing}
        if method=='reverse-register':self.terms=params['quote'];return {'registered':True}
        if method=='signinvoice':return {'bolt11':'lnxbtrt1signed'}
        if method=='reverse-status':return dict(payment_hash=self.decoded['payment_hash'],phase=self.phase,terms=self.terms,
            binding=['104x1x0',0] if self.phase=='held' else None,cltv_expiry=230,hook_ready=self.phase=='held')
        if method=='xbt-held':return dict(held=[dict(payment_hash=self.decoded['payment_hash'],short_channel_id='104x1x0',
            id=0,amount_msat=200000000,cltv_expiry=230)])
        raise AssertionError(method)

    def prepare(self):return r.prepare(self.manager,'swap',self.request,self.core,self.factory)
    def approve(self,digest):return q.approve(self.manager,'swap',digest,True,self.core,self.factory)
    def writes(self):return [m for _,m,_ in self.calls if m in q.WRITES or m=='sendpay']
    def held(self):
        self.phase='held';self.incoming['htlcs']=[dict(id=0,direction='in',payment_hash=self.decoded['payment_hash'],
            state='RCVD_ADD_ACK_REVOCATION',amount_msat=200000000,expiry=230)]

    def test_prepare_review_repeat_and_approval_return_same_invoice(self):
        review=self.prepare();before=(self.root/'review.json').read_bytes()
        self.assertEqual(review,self.prepare());self.assertEqual(before,(self.root/'review.json').read_bytes())
        self.assertEqual(review['direction'],'reverse');self.assertEqual(review['xbt_price_sats'],200000)
        self.assertEqual(review['btc_amount_msat'],100000000);self.assertEqual(self.writes(),[])
        local=actions.local_review(self.manager,'swap');self.assertEqual(local['review_digest'],review['review_digest'])
        self.assertNotIn('PRIVATE',json.dumps(local));self.assertNotIn('bb'*32,json.dumps(local))
        approved=self.approve(review['review_digest']);self.assertEqual(approved['phase'],'waiting_for_xbt')
        self.assertEqual(approved,self.approve(review['review_digest']))
        self.assertEqual(self.writes(),['reverse-register','signinvoice'])

    def test_action_prepare_review_approve_and_worker_share_saved_direction(self):
        lifecycle=q.lifecycle;lifecycle.setup(self.manager)
        save(self.manager/actions.PAIR_FILE,self.connections)
        owner=self
        with patch.object(q,'service',return_value=self.core), patch.object(r.ReverseRemote,'_request',
                lambda client,method,params:owner.rpc(client.network,method,params)):
            request=dict(job='swap',btcInvoice=self.request['btc_invoice'],xbtSats=200000)
            result=actions.action(self.manager,'prepare-reverse',request)
            self.assertEqual(result['direction'],'reverse')
            self.assertEqual(result,actions.action(self.manager,'prepare-reverse',request))
            approved=actions.action(self.manager,'approve',dict(job='swap',expectedDigest=result['review_digest'],confirmed=True))
            self.assertIn('xbt_invoice',approved);self.assertNotIn('btc_invoice',approved)
            self.assertEqual(actions.status(self.manager)['quotes'][0]['direction'],'reverse')
            before=list(self.writes());lifecycle.tick(self.manager)
            self.assertEqual(before,self.writes())
            self.assertEqual(actions.local_review(self.manager,'swap')['phase'],'waiting_for_xbt')

    def test_fixed_amount_currency_expiry_and_cltv_policy(self):
        for key,value in [('amount_msat',99999999),('currency','bc'),('min_final_cltv_expiry',41),('expiry',60)]:
            old=self.decoded[key];self.decoded[key]=value
            with self.assertRaises(ValueError):self.prepare()
            self.decoded[key]=old
        self.request['xbt_sats']=200001
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.writes(),[])

    def test_both_identities_checked_before_invoice_read(self):
        self.connections[1]['node_id']='02'+'ee'*32
        original=self.rpc
        def wrong(network,method,params):
            result=original(network,method,params)
            if method=='getinfo' and network=='xbt-regtest':result['id']='02'+'22'*32
            return result
        self.rpc=wrong
        with self.assertRaises(ValueError):self.prepare()
        self.assertFalse(any(m=='decode' for _,m,_ in self.calls))

    def test_approval_requires_confirmation_digest_and_unchanged_liquidity(self):
        result=self.prepare()
        for token,confirmed in [(result['review_digest'],False),('0'*64,True)]:
            with self.assertRaises(ValueError):r.approve(self.manager,'swap',token,confirmed,self.core,self.factory)
        self.outgoing['spendable_msat']=0
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertEqual(self.writes(),[])

    def test_worker_waits_then_hands_exact_committed_htlc_to_reverse_executor(self):
        result=self.prepare();self.approve(result['review_digest'])
        self.assertEqual(q.advance(self.root,self.core,self.factory)['phase'],'waiting_for_xbt')
        self.assertFalse((self.root/'intent.json').exists());self.held()
        with patch.object(q.executor,'step',return_value={'phase':'outgoing_started'}) as step:
            q.advance(self.root,self.core,self.factory)
        initial,state=q.executor.records(self.root)
        self.assertEqual(initial['direction'],'reverse');self.assertEqual(initial['state']['xbt_binding'],['104x1x0',0])
        self.assertEqual(initial['state']['reverse_quote'],self.terms);step.assert_called_once()
        self.assertEqual(self.writes(),['reverse-register','signinvoice'])

    def test_changed_held_terms_amount_and_existing_attempt_block_handoff(self):
        result=self.prepare();self.approve(result['review_digest']);self.held()
        self.incoming['htlcs'][0]['amount_msat']+=1
        with self.assertRaises(RuntimeError):q.advance(self.root,self.core,self.factory)
        self.incoming['htlcs'][0]['amount_msat']-=1
        self.existing=[dict(payment_hash=self.decoded['payment_hash'])]
        with self.assertRaises(ValueError):q.advance(self.root,self.core,self.factory)
        self.assertFalse((self.root/'intent.json').exists())

    def test_wrong_signed_invoice_not_published_and_worker_cannot_publish(self):
        result=self.prepare();self.wrong_signed=True
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertNotIn('xbt_invoice',private_load(self.root/'proposal'/'quote.json'))
        before=list(self.calls)
        with self.assertRaises(ValueError):q.advance(self.root,self.core,self.factory)
        self.assertEqual(before,self.calls)

    def test_live_and_restore_barriers_remain_before_rpc(self):
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'):
            with self.assertRaises(ValueError):actions.action(self.manager,'prepare-reverse',{})
        self.assertEqual(self.calls,[])
        result=self.prepare();save(self.manager/'restored.json',{'blocked':True});before=list(self.calls)
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertEqual(self.calls,before)

    def test_reverse_transport_rejects_btc_publication_and_all_spending(self):
        for method,args in [('signinvoice',('unsigned',)),('reverse-register',('{}',)),('sendpay',()),('withdraw',())]:
            with self.assertRaises(ValueError):self.factory(self.connections[0]).call(method,*args)
        self.assertEqual(self.calls,[])

    def test_duplicate_hash_across_directions_and_changed_record_refused(self):
        result=self.prepare()
        with self.assertRaises(ValueError):r.prepare(self.manager,'other',self.request,self.core,self.factory)
        data=private_load(self.root/'proposal'/'quote.json');data['terms']['btc_amount_msat']+=1
        save(self.root/'proposal'/'quote.json',data)
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertEqual(self.writes(),[])

if __name__=='__main__':unittest.main()
