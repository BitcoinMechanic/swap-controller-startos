import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
import deadline_boundary as d
from controller import private_load
MODULES=Path(os.environ.get('SWAP_MODULES','/opt/swap'))

class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'BTC_XBT_DISPOSABLE_CONTAINER':'1'});self.env.start();self.addCleanup(self.env.stop)
    def fixture(self,reverse=False):
        self.calls=[];self.close_count=0;self.lose=False;self.remaining=31;self.channel_state='CHANNELD_NORMAL'
        self.corrupt=None;self.pending=True
        self.spec=dict(direction='reverse' if reverse else 'forward',node_ids={'btc':'02'+'a'*64,'xbt':'03'+'b'*64},
            channel=dict(channel_id='c'*64,funding_txid='d'*64,funding_outnum=0,peer_id='02'+'e'*64,short_channel_id='1x1x1'),
            htlc_id=7,payment_hash='f'*64,expiry=200,incoming_amount_msat=100000000,
            outgoing_amount_msat=200000000,groupid=1,partid=0)
        owner=self;incoming='xbt' if reverse else 'btc'
        class Fake:
            def __init__(self,role):
                self.role=role;self.network='regtest' if role=='btc' else 'xbt-regtest'
                self.node_id=owner.spec['node_ids'][role]
                self.channel_id=owner.spec['channel']['channel_id'] if role==incoming else None
            def call(self,method,*args):
                owner.calls.append((self.role,method))
                s=owner.spec
                if method=='getinfo':return dict(id=self.node_id,network=self.network,blockheight=s['expiry']-owner.remaining)
                if method=='listsendpays':return {'payments':[dict(payment_hash=s['payment_hash'],groupid=1,partid=0,
                    amount_sent_msat=s['outgoing_amount_msat'],status='pending' if owner.pending else 'complete')]}
                if method in ('xbt-quote-status','xbt-spend-info','reverse-status'):
                    return dict(payment_hash=s['payment_hash'],binding=['1x1x1',7],phase='held',cltv_expiry=s['expiry'])
                if method=='listpeerchannels':
                    c=dict(s['channel'],state=owner.channel_state,htlcs=[dict(id=7,direction='in',payment_hash=s['payment_hash'],
                        amount_msat=s['incoming_amount_msat'],expiry=s['expiry'],state='RCVD_ADD_ACK_REVOCATION')])
                    if owner.corrupt:owner.corrupt(c)
                    return {'channels':[c]}
                if method=='close':
                    self.assert_recorded(args)
                    owner.close_count+=1;owner.channel_state='AWAITING_UNILATERAL'
                    if owner.lose:raise RuntimeError('lost reply')
                    return dict(type='unilateral',txid='a'*64)
                raise AssertionError(method)
            def assert_recorded(self,args):
                owner.assertEqual(args,('c'*64,1))
                record=private_load(owner.root/'deadline.json')
                owner.assertEqual(record['spec'],owner.spec)
                owner.assertIn(incoming+'_close_intent',record['state'])
        self.clients={role:Fake(role) for role in ('btc','xbt')}
    def step(self,**kwargs):return d.step(self.root,self.spec,self.clients,modules=MODULES,**kwargs)
    def test_both_guards_threshold_and_original_only(self):
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                self.fixture(reverse)
                result=self.step();self.assertFalse(result['close_intent_recorded']);self.assertEqual(self.close_count,0)
                self.remaining=30;result=self.step();self.assertEqual(self.close_count,1)
                self.assertTrue(result['close_reply_recorded']);self.assertFalse(result['onchain_claim_verified'])
                self.step();self.assertEqual(self.close_count,1)
                self.assertFalse(set(m for _,m in self.calls)&{'sendpay','xbt-fail','reverse-fail','xbt-release','reverse-release'})
                (self.root/'deadline.json').unlink()
    def test_lost_reply_reconciled_without_second_close(self):
        for reverse in (False,True):
            self.fixture(reverse);self.remaining=30;self.lose=True
            with self.assertRaises(RuntimeError):self.step()
            before=(self.root/'deadline.json').read_bytes()
            result=self.step();self.assertFalse(result['close_reply_recorded'])
            self.assertEqual(self.close_count,1);self.assertEqual(before,(self.root/'deadline.json').read_bytes())
            (self.root/'deadline.json').unlink()
    def test_crash_after_intent_before_close(self):
        self.fixture();self.remaining=30
        original=d.save
        def crash(path,record):original(path,record);raise RuntimeError('crash')
        with patch.object(d,'save',side_effect=crash):
            with self.assertRaises(RuntimeError):self.step()
        self.assertEqual(self.close_count,0)
        self.step();self.assertEqual(self.close_count,1)
    def test_changed_funding_or_htlc_refused(self):
        for reverse in (False,True):
            for mutate in (lambda c:c.update(funding_txid='a'*64),lambda c:c['htlcs'][0].update(amount_msat=1),
                           lambda c:c['htlcs'][0].update(local_trimmed=True),lambda c:c['htlcs'][0].update(state='RCVD_ADD_HTLC')):
                self.fixture(reverse);self.remaining=30;self.corrupt=mutate
                with self.assertRaises(ValueError):self.step()
                self.assertEqual(self.close_count,0)
    def test_terminal_outgoing_never_closes(self):
        self.fixture();self.remaining=0;self.pending=False
        with self.assertRaises(ValueError):self.step()
        self.assertEqual(self.close_count,0)
    def test_live_network_rejected_before_rpc(self):
        self.fixture();self.clients['btc'].network='bitcoin'
        with self.assertRaises(ValueError):self.step()
        self.assertEqual(self.calls,[])
    def test_optin_and_restore_block(self):
        self.fixture()
        with patch.dict(os.environ,{'BTC_XBT_DISPOSABLE_CONTAINER':'0'}):
            with self.assertRaises(ValueError):self.step()
        (self.root/'restored.json').write_text('{}')
        with self.assertRaises(ValueError):self.step()
        self.assertEqual(self.calls,[])
    def test_slow_observations_refuse_close(self):
        self.fixture();self.remaining=30
        times=iter([0,121])
        with self.assertRaises(ValueError):self.step(clock=lambda:next(times))
        self.assertEqual(self.close_count,0)
    def test_rpc_exact_target_only_and_no_spend_methods(self):
        remote=d.DeadlineRemote.__new__(d.DeadlineRemote);remote.network='regtest';remote.channel_id='c'*64
        for method,args in [('sendpay',()),('close',('a'*64,1)),('close',('c'*64,0)),('xbt-release',('b'*64,))]:
            with self.assertRaises(ValueError):remote.call(method,*args)
        with self.assertRaises(ValueError):d.DeadlineRemote({'network':'bitcoin'})
    def test_changed_journal_binding_refused(self):
        self.fixture();self.remaining=30;self.step()
        self.spec['channel']['funding_txid']='0'*64
        with self.assertRaises(ValueError):self.step()
        self.assertEqual(self.close_count,1)

if __name__=='__main__':unittest.main()
