import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import gate_observation as g
import readiness as r
from controller import save, Refused
from read_only_rpc import Client, ProbeError, METHODS

class GateObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.config={'schema':1,'generation':'a'*32,'nodes':{role:dict(url=role,rune='monitor',ca_pem='PRIVATE',node_id=role+'id') for role in ('btc','xbt')}}
        self.calls=[];self.profile='live-pilot-v1';self.count=0;self.bad_identity=False;self.fail=False
        owner=self
        class Fake:
            def __init__(self,url,rune,ca_data):self.role=url;self.rune=rune
            def call(self,method):
                owner.calls.append((self.role,self.rune,method))
                if owner.fail:raise RuntimeError('PRIVATE secret')
                if method=='getinfo':return dict(id='wrong' if owner.bad_identity else self.role+'id',network='bitcoin' if self.role=='btc' else 'xbt')
                if method=='xbt-pilot-info':return dict(profile=owner.profile,registered_quotes=owner.count,secret='PRIVATE')
                if method=='listpeerchannels':return {'channels':[dict(state='CHANNELD_NORMAL',peer_connected=True,htlcs=[])]}
                raise AssertionError(method)
        self.factory=Fake
        self.loader=patch.object(g,'load_config',return_value=self.config);self.loader.start();self.addCleanup(self.loader.stop)
    def pair(self):return g.pair(self.root,'gate-rune',True,self.factory,self.factory)
    def test_pair_then_observe_filtered_and_private(self):
        result=self.pair();self.assertTrue(result['read_only']);self.assertFalse(result['live_payment_enabled'])
        self.assertEqual([x[2] for x in self.calls],['getinfo','getinfo','getinfo','xbt-pilot-info'])
        self.assertEqual((self.root/g.RECORD).stat().st_mode&0o777,0o600)
        result=g.observe(self.root,self.config,self.factory)
        self.assertEqual(result,dict(observation='verified',profile='live-pilot-v1',registered_quotes=0))
        self.assertNotIn('PRIVATE',json.dumps(result))
    def test_no_confirmation_no_rpc(self):
        with self.assertRaises(Refused):g.pair(self.root,'gate-rune',False,self.factory,self.factory)
        self.assertEqual(self.calls,[])
    def test_invalid_identity_prevents_gate_read_and_save(self):
        self.bad_identity=True
        with self.assertRaises(Refused):self.pair()
        self.assertEqual(len(self.calls),1);self.assertFalse((self.root/g.RECORD).exists())
    def test_profile_and_count_validation_preserve_saved_credential(self):
        self.pair();before=(self.root/g.RECORD).read_bytes()
        for profile,count in [('regtest',0),('live-pilot-v2',0),('live-pilot-v1',True),('live-pilot-v1',-1),('live-pilot-v1','0')]:
            self.profile=profile;self.count=count
            with self.assertRaises(Refused):self.pair()
            self.assertEqual((self.root/g.RECORD).read_bytes(),before)
    def test_pairing_change_and_expiry_do_not_save(self):
        changed=copy.deepcopy(self.config);changed['generation']='b'*32
        with patch.object(g,'load_config',side_effect=[self.config,changed]):
            with self.assertRaises(Refused):self.pair()
        for end in (99,221):
            times=iter([100,end])
            with self.assertRaises(Refused):g.pair(self.root,'gate-rune',True,self.factory,self.factory,clock=lambda:next(times))
        self.assertFalse((self.root/g.RECORD).exists())
    def test_stale_generation_symlink_and_transport_fail_closed(self):
        self.pair();changed=copy.deepcopy(self.config);changed['generation']='b'*32
        self.assertEqual(g.observe(self.root,changed,self.factory),{'observation':'unavailable'})
        self.fail=True;self.assertEqual(g.observe(self.root,self.config,self.factory),{'observation':'unavailable'})
        self.fail=False;(self.root/g.RECORD).unlink();(self.root/g.RECORD).symlink_to(self.root/'missing')
        self.assertEqual(g.observe(self.root,self.config,self.factory),{'observation':'unavailable'})
    def test_gate_credential_change_during_observation_is_discarded(self):
        self.pair();original=g.check
        def mutate(*args):
            report=original(*args);save(self.root/g.RECORD,dict(schema=1,generation='b'*32,node_id='btcid',rune='gate-rune'));return report
        with patch.object(g,'check',side_effect=mutate):self.assertEqual(g.observe(self.root,self.config,self.factory),{'observation':'unavailable'})
    def test_readiness_btc_only_and_barrier_preserved(self):
        self.pair();(self.root/'execution').mkdir();save(self.root/'execution/restored.json',{'blocked':True})
        before=(self.root/'execution/restored.json').read_bytes()
        with patch.object(r,'load_config',return_value=self.config):result=r.inspect(self.root,self.factory,gate_factory=self.factory)
        self.assertEqual(result['gate_activation'],'btc_verified_xbt_not_verified')
        self.assertIn('xbt_gate_activation_not_verified',result['blockers'])
        self.assertFalse(result['live_ready']);self.assertFalse(result['live_payment_enabled'])
        self.assertIn('restored_execution_remains_blocked',result['blockers'])
        self.assertEqual(before,(self.root/'execution/restored.json').read_bytes())
    def test_readiness_stale_probe_discards_gate_success(self):
        self.pair();times=iter([100,221])
        with patch.object(r,'load_config',return_value=self.config):result=r.inspect(self.root,self.factory,clock=lambda:next(times),gate_factory=self.factory)
        self.assertNotEqual(result['btc_gate']['observation'],'verified')
        self.assertIn('gate_activation_not_verified',result['blockers'])
    def test_transport_whitelists_block_writes_before_transport(self):
        self.assertEqual(METHODS,('getinfo','listpeerchannels'))
        for cls in (Client,g.GateClient):
            obj=object.__new__(cls)
            for method in ('sendpay','xbt-pilot-release','xbt-pilot-register','blacklistrune'):
                with self.assertRaises(ProbeError):obj.call(method)
        with self.assertRaises(ProbeError):object.__new__(Client).call('xbt-pilot-info')
        with self.assertRaises(ProbeError):object.__new__(g.GateClient).call('listpeerchannels')

if __name__=='__main__':unittest.main()
