import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import quote_workflow as q
from controller import save, private_load

# Unit tests require the same pinned modules as the package. The VM has the
# source image; the test command accepts an extracted, verified module tree.
MODULES=Path(os.environ.get('SWAP_QUOTE_TEST_MODULES','/opt/swap'))


class QuoteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (MODULES/'SOURCE_COMMIT').exists():
            raise unittest.SkipTest('Run via test-quote-flow.py against the controller image, or set SWAP_QUOTE_TEST_MODULES to the verified pin')
        cls.core=q.service(MODULES)

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.manager=Path(self.tmp.name)/'execution'
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1');env.start();self.addCleanup(env.stop)
        self.now=int(time.time());self.calls=[];self.phase='quoted';self.terms=None
        self.connections=[dict(network=n,node_id='02'+str(i)*64,rune='private-rune',ca_pem=None,
            url='https://localhost:'+str(9000+i),cli=['/not-executed','--network='+n])
            for i,n in enumerate(('regtest','xbt-regtest'),1)]
        self.decoded=dict(valid=True,type='bolt11 invoice',currency='xbtrt',payment_hash='aa'*32,
            payment_secret='bb'*32,amount_msat=200000000,payee='03'+'33'*32,
            created_at=self.now,expiry=3600,min_final_cltv_expiry=18)
        self.channel=dict(peer_id=self.decoded['payee'],short_channel_id='103x1x0',state='CHANNELD_NORMAL',
            peer_connected=True,spendable_msat=300000000,htlcs=[])
        self.binding=['104x1x0',0]
        self.request=dict(connections=self.connections,xbt_invoice='lnxbtrt1test',btc_sats=100000)
        owner=self
        class Fake(q.QuoteRemote):
            def _request(self,method,params):return owner.rpc(self.network,method,params)
        self.factory=Fake

    def rpc(self,network,method,params):
        self.calls.append((network,method,params))
        if method=='getinfo':return dict(id=self.connections[network=='xbt-regtest']['node_id'],network=network,blockheight=110)
        if method=='decode':
            if network=='xbt-regtest':return copy.deepcopy(self.decoded)
            return dict(valid=True,currency='bcrt',payee=self.connections[0]['node_id'],
                payment_hash=self.terms['payment_hash'],payment_secret=self.terms['payment_secret'],
                amount_msat=self.terms['btc_amount_msat'],min_final_cltv_expiry=120)
        if method=='listpeerchannels':
            if network=='xbt-regtest':return {'channels':[copy.deepcopy(self.channel)]}
            return {'channels':[dict(short_channel_id=self.binding[0],state='CHANNELD_NORMAL',htlcs=[dict(
                id=0,direction='in',payment_hash=self.decoded['payment_hash'],state='RCVD_ADD_ACK_REVOCATION',
                amount_msat=100000000,expiry=230)])]}
        if method=='listsendpays':return {'payments':[]}
        if method=='xbt-register':self.terms=params['quote'];return {'registered':True}
        if method=='signinvoice':return {'bolt11':'lnbcrt1signed'}
        if method=='xbt-quote-status':return dict(payment_hash=self.decoded['payment_hash'],phase=self.phase,binding=self.binding if self.phase=='held' else None)
        if method=='xbt-spend-info':return dict(self.terms,binding=self.binding,cltv_expiry=230)
        raise AssertionError(method)

    def prepare(self):return q.prepare(self.manager,'swap',self.request,self.core,self.factory)
    def approve(self,token):return q.approve(self.manager,'swap',token,True,self.core,self.factory)
    def mutations(self):return [m for _,m,_ in self.calls if m in q.WRITES or m=='sendpay']
    def root(self):return self.manager/'jobs'/'swap'

    def test_review_repeat_is_read_only_and_preserves_digest(self):
        result=self.prepare();before=(self.root()/'review.json').read_bytes()
        self.assertEqual(result,self.prepare());self.assertEqual(before,(self.root()/'review.json').read_bytes())
        self.assertEqual(self.mutations(),[]);self.assertNotIn('btc_invoice',result)
        self.assertNotIn('private-rune',json.dumps(result));self.assertNotIn('bb'*32,json.dumps(result))
        self.assertFalse((self.root()/'intent.json').exists())

    def test_explicit_unchanged_review_required_before_publication(self):
        result=self.prepare()
        for token,confirmed in [(result['review_digest'],False),('0'*64,True)]:
            with self.assertRaises(ValueError):q.approve(self.manager,'swap',token,confirmed,self.core,self.factory)
        self.assertEqual(self.mutations(),[])
        self.request['btc_sats']+=1
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.mutations(),[])

    def test_publication_repeat_does_not_register_or_sign_again(self):
        result=self.prepare();issued=self.approve(result['review_digest'])
        self.assertEqual(issued['btc_invoice'],'lnbcrt1signed')
        self.assertEqual(self.mutations(),['xbt-register','signinvoice'])
        self.assertEqual(issued,self.approve(result['review_digest']))
        self.assertEqual(self.mutations(),['xbt-register','signinvoice'])
        self.assertFalse((self.root()/'permit.json').exists())

    def test_changed_quote_or_liquidity_refused_before_registration(self):
        result=self.prepare();self.channel['peer_connected']=False
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertEqual(self.mutations(),[])
        data=private_load(self.root()/'proposal'/'quote.json');data['terms']['btc_amount_msat']+=1000
        save(self.root()/'proposal'/'quote.json',data)
        with self.assertRaises(ValueError):self.approve(result['review_digest'])

    def test_invalid_invoice_and_network_refused(self):
        for field,value in [('currency','xbt'),('amount_msat',0),('expiry',60),('min_final_cltv_expiry',41),('valid',False)]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as tmp:
                old=self.decoded[field];self.decoded[field]=value
                with self.assertRaises(ValueError):q.prepare(Path(tmp),'swap',self.request,self.core,self.factory)
                self.decoded[field]=old
        self.connections[0]['network']='bitcoin'
        with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.mutations(),[])

    def test_both_identities_checked_before_decode_or_writes(self):
        original=self.rpc
        def mismatch(network,method,params):
            result=original(network,method,params)
            if network=='xbt-regtest' and method=='getinfo':result['id']='02'+'ff'*32
            return result
        self.rpc=mismatch
        with self.assertRaises(ValueError):self.prepare()
        self.assertTrue(all(m=='getinfo' for _,m,_ in self.calls))

    def test_waiting_quote_cannot_launch_and_backup_refused(self):
        self.prepare()
        result=q.advance(self.root(),self.core,self.factory)
        self.assertEqual(result['phase'],'review_required');self.assertEqual(self.mutations(),[])
        with self.assertRaises(ValueError):q.lifecycle.backup_begin(self.manager)
        result=self.prepare();self.approve(result['review_digest'])
        self.assertEqual(q.advance(self.root(),self.core,self.factory)['phase'],'waiting_for_btc')
        self.assertFalse((self.root()/'intent.json').exists())

    def test_held_quote_handoff_uses_validated_terms_and_existing_executor(self):
        result=self.prepare();self.approve(result['review_digest']);self.phase='held'
        def runner(root):
            initial,state=q.executor.records(root)
            self.assertEqual(state['btc_binding'],self.binding)
            self.assertEqual(state['payment_secret'],self.decoded['payment_secret'])
            self.assertEqual(state['route'][0]['id'],self.decoded['payee'])
            self.assertNotIn('btc_deadline_guard',state)
            self.assertEqual(private_load(root/'permit.json')['digest'],q.executor.digest(initial))
            return subprocess.CompletedProcess([],0,'{}','')
        with patch.object(q.executor,'step',side_effect=runner) as step:
            q.advance(self.root(),self.core,self.factory)
            q.advance(self.root(),self.core,self.factory)
            self.assertEqual(step.call_count,2)
        self.assertEqual(self.mutations(),['xbt-register','signinvoice'])

    def test_held_terms_mismatch_cannot_authorize(self):
        result=self.prepare();self.approve(result['review_digest']);self.phase='held';self.terms['btc_amount_msat']+=1
        with self.assertRaises(ValueError):q.advance(self.root(),self.core,self.factory)
        self.assertFalse((self.root()/'permit.json').exists())

    def test_restore_pause_and_live_optin_refused_before_rpc(self):
        result=self.prepare();self.calls.clear()
        for name in ('restored.json','backup-paused.json'):
            save(self.manager/name,{'blocked':True})
            with self.assertRaises(ValueError):self.approve(result['review_digest'])
            (self.manager/name).unlink()
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'):
            with self.assertRaises(ValueError):self.prepare()
        self.assertEqual(self.calls,[])

    def test_expiry_and_existing_outgoing_attempt_refuse_publication(self):
        result=self.prepare()
        with patch.object(q.time,'time',return_value=self.now+700):
            with self.assertRaises(ValueError):self.approve(result['review_digest'])
        original=self.rpc
        def attempted(network,method,params):
            if method=='listsendpays':return {'payments':[{'payment_hash':self.decoded['payment_hash'],'status':'pending'}]}
            return original(network,method,params)
        self.rpc=attempted
        with self.assertRaises(ValueError):self.approve(result['review_digest'])
        self.assertEqual(self.mutations(),[])

    def test_lost_registration_reply_keeps_same_quote_and_requires_explicit_resume(self):
        result=self.prepare();original=self.rpc;first=True
        def lost(network,method,params):
            nonlocal first
            value=original(network,method,params)
            if method=='xbt-register' and first:
                first=False
                raise OSError('private transport detail')
            return value
        self.rpc=lost
        with self.assertRaises(OSError):self.approve(result['review_digest'])
        saved_terms=copy.deepcopy(self.terms)
        with self.assertRaisesRegex(ValueError,'finish_quote_publication'):
            q.advance(self.root(),self.core,self.factory)
        self.assertFalse((self.root()/'intent.json').exists())
        self.approve(result['review_digest'])
        self.assertEqual(saved_terms,self.terms)
        self.assertEqual(self.mutations(),['xbt-register','xbt-register','signinvoice'])

    def test_lifecycle_uses_quote_worker_and_restore_blocks_it(self):
        self.prepare()
        with patch.object(q,'advance',return_value={'phase':'review_required'}) as worker:
            self.assertEqual(q.lifecycle.tick(self.manager)['swap']['phase'],'review_required')
            self.assertEqual(worker.call_count,1)
            q.lifecycle.restored(self.manager)
            self.assertEqual(q.lifecycle.tick(self.manager),{})
            self.assertEqual(worker.call_count,1)

    def test_quote_transport_refuses_spending_and_xbt_signing(self):
        for network in self.connections:
            remote=self.factory(network)
            for method in ('sendpay','withdraw','pay','blacklistrune','createrune'):
                with self.assertRaises(ValueError):remote.call(method)
        with self.assertRaises(ValueError):self.factory(self.connections[1]).call('signinvoice','test')
        self.assertEqual(self.calls,[])

    def test_duplicate_invoice_job_and_symlink_refused(self):
        self.prepare()
        with self.assertRaises(ValueError):q.prepare(self.manager,'second',self.request,self.core,self.factory)
        (self.manager/'jobs'/'alias').symlink_to(self.root(),target_is_directory=True)
        with self.assertRaises(ValueError):q.prepare(self.manager,'alias',self.request,self.core,self.factory)


if __name__=='__main__':unittest.main()
