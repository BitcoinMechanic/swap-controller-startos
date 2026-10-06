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
from controller import Refused, save
from read_only_rpc import ProbeError

class XbtObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.config={'schema':1,'generation':'a'*32,'nodes':{role:dict(url=role,rune='monitor',ca_pem='PRIVATE',node_id=role+'id') for role in ('btc','xbt')}}
        self.calls=[];self.active=True;self.profile='reverse-live-v1';self.fail_xbt=False;self.wrong=False
        owner=self
        class Fake:
            def __init__(self,url,rune,ca_data):self.role=url;self.rune=rune
            def call(self,method):
                owner.calls.append((self.role,self.rune,method))
                if owner.fail_xbt and self.rune=='xbt-observer':raise ValueError('PRIVATE')
                if method=='getinfo':return dict(id='wrong' if owner.wrong else self.role+'id',network='bitcoin' if self.role=='btc' else 'xbt')
                if method=='xbt-pilot-info':
                    assert self.role=='btc';return dict(profile='live-pilot-v1',registered_quotes=0)
                if method=='reverse-pilot-info':
                    assert self.role=='xbt';return dict(profile=owner.profile,gate_active=owner.active,secret='PRIVATE')
                if method=='listpeerchannels':return dict(channels=[dict(state='CHANNELD_NORMAL',peer_connected=True,htlcs=[])])
                raise AssertionError(method)
        self.factory=Fake
        self.loader=patch.object(g,'load_config',return_value=self.config);self.loader.start();self.addCleanup(self.loader.stop)
    def pair(self,role='xbt'):
        return g.pair(self.root,role+'-observer',True,self.factory,self.factory,role=role)
    def inspect(self):
        with patch.object(r,'load_config',return_value=self.config):return r.inspect(self.root,self.factory,gate_factory=self.factory,xbt_gate_factory=self.factory)
    def test_both_profiles_verified_and_btc_pairing_preserved(self):
        self.pair('btc');before=(self.root/g.RECORD).read_bytes()
        result=self.pair();self.assertEqual(before,(self.root/g.RECORD).read_bytes())
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertTrue(result['xbt_gate']['gate_active'])
        self.assertEqual((self.root/'xbt-gate-observation.json').stat().st_mode&0o777,0o600)
        result=self.inspect();self.assertEqual(result['gate_activation'],'both_profiles_verified')
        self.assertFalse(result['live_ready']);self.assertFalse(result['live_payment_enabled'])
        for blocker in ('live_execution_not_supported','dedicated_execution_credentials_required','live_amount_fee_expiry_policy_required','live_cross_chain_timing_policy_required'):
            self.assertIn(blocker,result['blockers'])
        self.assertFalse(any('gate_activation' in b for b in result['blockers']))
        self.assertNotIn('registered_quotes',result['xbt_gate'])
    def test_invalid_role_refused_before_rpc(self):
        with self.assertRaises(Refused):self.pair('../../btc')
        self.assertEqual(self.calls,[])
    def test_profile_inactive_and_truthy_nonboolean_refused(self):
        for profile,active in [('regtest',True),('live-pilot-v1',True),('reverse-live-v1',False),('reverse-live-v1',1),('reverse-live-v1','true')]:
            self.profile,self.active=profile,active
            with self.assertRaises(Refused):self.pair()
        self.assertFalse((self.root/'xbt-gate-observation.json').exists())
    def test_wrong_node_prevents_gate_read(self):
        self.wrong=True
        with self.assertRaises(Refused):self.pair()
        self.assertEqual(len(self.calls),1)
    def test_revoked_xbt_credential_retains_btc_verification(self):
        self.pair('btc');self.pair();self.fail_xbt=True
        result=self.inspect();self.assertEqual(result['gate_activation'],'btc_verified_xbt_not_verified')
        self.assertIn('xbt_gate_activation_not_verified',result['blockers'])
        self.assertNotIn('PRIVATE',json.dumps(result))
    def test_xbt_only_has_btc_blocker(self):
        self.pair();result=self.inspect()
        self.assertEqual(result['gate_activation'],'xbt_verified_btc_not_verified')
        self.assertIn('btc_gate_activation_not_verified',result['blockers'])
    def test_stale_pair_discards_both_and_restore_barrier_retained(self):
        self.pair('btc');self.pair();(self.root/'execution').mkdir();save(self.root/'execution/restored.json',{'blocked':True})
        changed=copy.deepcopy(self.config);changed['generation']='b'*32
        with patch.object(r,'load_config',side_effect=[self.config,changed]):result=r.inspect(self.root,self.factory,gate_factory=self.factory,xbt_gate_factory=self.factory)
        self.assertEqual(result['nodes'],{})
        for role in ('btc','xbt'):self.assertNotEqual(result[role+'_gate']['observation'],'verified')
        self.assertIn('gate_activation_not_verified',result['blockers']);self.assertIn('restored_execution_remains_blocked',result['blockers'])
    def test_swapped_records_refused(self):
        self.pair('btc');(self.root/'xbt-gate-observation.json').write_bytes((self.root/g.RECORD).read_bytes())
        (self.root/'xbt-gate-observation.json').chmod(0o600)
        self.assertEqual(g.observe(self.root,self.config,self.factory,'xbt'),dict(observation='unavailable'))
    def test_xbt_transport_only_has_identity_and_info(self):
        obj=object.__new__(g.XbtGateClient)
        for method in ('xbt-pilot-info','listpeerchannels','sendpay','reverse-register','reverse-release','reverse-fail','xbt-held'):
            with self.assertRaises(ProbeError):obj.call(method)

if __name__=='__main__':unittest.main()
