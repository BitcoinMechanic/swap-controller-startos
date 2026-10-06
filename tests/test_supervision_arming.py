"""Pre-submission protection binding and crash boundary, using simulated RPC."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
import executor as e
import regtest_supervisor as s
import quote_workflow as q
from controller import save,private_load


class ArmingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        (self.root/'SOURCE_COMMIT').write_text(e.PIN)
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1');env.start();self.addCleanup(env.stop)
        self.direction='forward';self.calls=[];self.payments=[];self.margin=40;self.corrupt=None
        self.configs=[dict(network=n,node_id='02'+str(i)*64,rune='private',ca_pem=None,
            url='https://localhost:'+str(9000+i),cli=['cli','--network='+n]) for i,n in enumerate(('regtest','xbt-regtest'),1)]
    def prepare(self,reverse=False):
        self.direction='reverse' if reverse else 'forward';incoming='xbt' if reverse else 'btc'
        self.spec=dict(direction=self.direction,node_ids=dict(btc=self.configs[0]['node_id'],xbt=self.configs[1]['node_id']),
            channel=dict(channel_id='c'*64,funding_txid='d'*64,funding_outnum=0,peer_id='02'+'e'*64,short_channel_id='1x1x1'),
            htlc_id=7,payment_hash='f'*64,expiry=200,incoming_amount_msat=1000,outgoing_amount_msat=2000)
        self.state=dict(phase='prepared',payment_hash=self.spec['payment_hash'],route=[dict(amount_msat=2000)],
            btc_cli=self.configs[0]['cli'],xbt_cli=self.configs[1]['cli'],**{incoming+'_binding':['1x1x1',7]})
        self.plan=dict(spec=self.spec,deadline_connections=self.configs,claim_connections=self.configs)
        save(self.root/'supervisor-required.json',dict(plan_digest=e.digest(self.plan)))
        save(self.root/'supervisor-plan.json',self.plan)
        token=e.prepare(self.root,self.direction,self.state,self.configs)
        save(self.root/'handoff.json',dict(review_digest='token',state=self.state))
        p=patch.object(q,'review',return_value=(dict(connections=self.configs),dict(terms={incoming+'_amount_msat':1000}),'token'))
        p.start();self.addCleanup(p.stop)
        e.authorize(self.root,token,1100,confirmed=True,now=1000)
        owner=self
        class Remote:
            def __init__(self,role):
                self.role=role;self.network='regtest' if role=='btc' else 'xbt-regtest';self.node_id=owner.spec['node_ids'][role]
            def call(self,method,*args):
                owner.calls.append((self.role,method))
                if owner.corrupt=='credential':raise ValueError('credential_unavailable')
                if method=='getinfo':return dict(id=self.node_id,network=self.network,blockheight=200-owner.margin)
                if method=='listsendpays':return dict(payments=owner.payments)
                if method=='listpeerchannels':
                    channel=dict(owner.spec['channel'],state='CHANNELD_NORMAL',htlcs=[dict(id=7,direction='in',
                        payment_hash='f'*64,amount_msat=1000,expiry=200,state='RCVD_ADD_ACK_REVOCATION')])
                    if owner.corrupt=='funding':channel['funding_txid']='0'*64
                    if owner.corrupt=='htlc':channel['htlcs'][0]['expiry']=201
                    return dict(channels=[channel])
                return dict(payment_hash='f'*64,binding=['1x1x1',7],cltv_expiry=200,phase='held',hook_ready=True)
        p=patch.object(s,'clients',side_effect=lambda *args:{r:Remote(r) for r in ('btc','xbt')});p.start();self.addCleanup(p.stop)
        original=s.arm
        p=patch.object(s,'arm',side_effect=lambda root,initial,state:original(root,initial,state,modules=root))
        p.start();self.addCleanup(p.stop)
    def runner(self,root,direction,flags):
        initial,state=e.records(root)
        self.assertEqual(private_load(root/'supervisor-armed.json'),dict(intent_digest=e.digest(initial),plan_digest=e.digest(self.plan)))
        self.assertTrue((root/'launched.json').exists())
        save(root/'state.json',dict(state,phase='outgoing_started'))
        return subprocess.CompletedProcess([],88)
    def test_forward_arms_before_child_and_attaches_without_resend(self):
        self.prepare()
        e.step(self.root,runner=self.runner,now=1001)
        self.payments=[dict(payment_hash='f'*64,amount_sent_msat=2000,groupid=42,status='pending')]
        initial,state=e.records(self.root)
        request=s.attach_attempt(self.root,initial,state)
        self.assertEqual(request['spec']['groupid'],42);self.assertEqual(request['spec']['partid'],0)
        self.assertEqual(s.attach_attempt(self.root,initial,state),request)
    def test_reverse_arms_before_child(self):
        self.prepare(True);e.step(self.root,runner=self.runner,now=1001)
    def test_missing_or_changed_plan_never_launches(self):
        self.prepare();(self.root/'supervisor-plan.json').unlink()
        with self.assertRaises(FileNotFoundError):e.step(self.root,runner=self.runner,now=1001)
        save(self.root/'supervisor-plan.json',dict(self.plan,spec=dict(self.spec,expiry=201)))
        with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1001)
        self.assertFalse((self.root/'launched.json').exists());self.assertEqual(self.calls,[])
    def test_bad_credentials_funding_htlc_and_margin_never_launch(self):
        self.prepare()
        for bad in ('credential','funding','htlc'):
            self.corrupt=bad
            with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1001)
            self.assertFalse((self.root/'launched.json').exists())
        self.corrupt=None;self.margin=30
        with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1001)
        self.assertFalse((self.root/'supervisor-armed.json').exists())
    def test_existing_attempt_blocks_first_launch(self):
        self.prepare();self.payments=[dict(status='pending')]
        with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1001)
        self.assertFalse((self.root/'launched.json').exists())
    def test_missing_attempt_after_launch_cannot_relaunch(self):
        self.prepare();e.step(self.root,runner=self.runner,now=1001)
        initial,state=e.records(self.root)
        with self.assertRaises(ValueError):s.attach_attempt(self.root,initial,state)
        self.assertFalse((self.root/'supervisor.json').exists())
    def test_launch_crash_preserves_arming_and_refuses_retry(self):
        self.prepare()
        def crashed(*args):raise OSError('lost child')
        with self.assertRaises(RuntimeError):e.step(self.root,runner=crashed,now=1001)
        self.assertTrue((self.root/'supervisor-armed.json').exists())
        with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1002)
        self.assertEqual(private_load(self.root/'state.json')['phase'],'prepared')
    def test_deleted_arming_record_blocks_recovery(self):
        self.prepare();e.step(self.root,runner=self.runner,now=1001)
        (self.root/'supervisor-armed.json').unlink()
        with self.assertRaises(FileNotFoundError):e.step(self.root,runner=self.runner,now=1002)
        initial,state=e.records(self.root)
        with self.assertRaises(FileNotFoundError):s.attach_attempt(self.root,initial,state)

    def test_restore_blocks_arming(self):
        self.prepare();save(self.root/'restored.json',{})
        with self.assertRaises(ValueError):e.step(self.root,runner=self.runner,now=1001)
        self.assertEqual(self.calls,[]);self.assertFalse((self.root/'launched.json').exists())


if __name__=='__main__':unittest.main()
