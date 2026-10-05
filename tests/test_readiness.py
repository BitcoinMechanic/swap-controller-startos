import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import readiness as r
from controller import save
from read_only_rpc import METHODS,RESTRICTIONS

class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.config=dict(schema=1,generation='a'*32,nodes={role:dict(url=role,rune='PRIVATE',ca_pem='SECRET-CA',node_id=role+'-id') for role in ('btc','xbt')})
        self.calls=[];self.wrong=False;self.warning=False;self.bad=False;self.pending=False
        self.channels={role:[dict(state='CHANNELD_NORMAL',peer_connected=True,htlcs=[])] for role in ('btc','xbt')}
        owner=self
        class Fake:
            def __init__(self,url,rune,ca_data):self.role=url
            def call(self,method):
                owner.calls.append((self.role,method))
                if owner.bad:raise ValueError('PRIVATE SECRET-CA https://secret')
                if method=='getinfo':
                    info=dict(id=self.role+'-id',network='bitcoin' if self.role=='btc' else 'xbt')
                    if owner.wrong and self.role=='xbt':info['id']='wrong'
                    if owner.warning:info['warning_sync']='PRIVATE'
                    return info
                if method=='listpeerchannels':return dict(channels=owner.channels[self.role])
                raise AssertionError(method)
        self.factory=Fake

    def inspect(self):
        with patch.object(r,'load_config',return_value=copy.deepcopy(self.config)):
            return r.inspect(self.root,self.factory,clock=lambda:100)

    def test_unpaired_without_rpc_or_file_creation(self):
        before=list(self.root.iterdir());result=r.inspect(self.root,self.factory)
        self.assertFalse(result['paired']);self.assertIn('pair_nodes_first',result['blockers'])
        self.assertEqual(self.calls,[]);self.assertEqual(before,list(self.root.iterdir()))

    def test_fresh_readonly_checks_both_identities_before_channels(self):
        result=self.inspect()
        self.assertEqual(self.calls,[('btc','getinfo'),('xbt','getinfo'),('btc','listpeerchannels'),('xbt','listpeerchannels')])
        self.assertTrue(result['connection_ready']);self.assertFalse(result['live_ready']);self.assertFalse(result['live_payment_enabled'])
        self.assertEqual(result['gate_activation'],'not_verified_with_read_only_credentials')
        self.assertEqual(result['nodes']['btc']['connected_normal_channels'],1)
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('SECRET',json.dumps(result))

    def test_identity_mismatch_prevents_all_channel_reads(self):
        self.wrong=True;result=self.inspect()
        self.assertFalse(result['connection_ready']);self.assertEqual(len(self.calls),2)
        self.assertIn('xbt_observation_unavailable',result['blockers'])

    def test_warnings_disconnections_and_pending_htlcs(self):
        self.warning=True;self.channels['btc'][0]['peer_connected']=False
        self.channels['xbt'][0]['htlcs']=[{'payment_hash':'PRIVATE'}]
        result=self.inspect()
        for reason in ('btc_node_warning','btc_connected_channel_required','xbt_pending_htlcs_require_review'):
            self.assertIn(reason,result['blockers'])
        self.assertFalse(result['connection_ready']);self.assertNotIn('PRIVATE',json.dumps(result))

    def test_invalid_response_and_transport_errors_are_private(self):
        self.bad=True;result=self.inspect();self.assertFalse(result['connection_ready'])
        self.assertNotIn('PRIVATE',json.dumps(result));self.assertNotIn('secret',json.dumps(result))
        self.bad=False;self.channels['btc']=[dict(state='CHANNELD_NORMAL',peer_connected='yes',htlcs=[])]
        result=self.inspect();self.assertEqual(result['nodes']['btc']['observation'],'channel_observation_unavailable')

    def test_replaced_pairing_discards_observations(self):
        changed=copy.deepcopy(self.config);changed['generation']='b'*32
        with patch.object(r,'load_config',side_effect=[self.config,changed]):result=r.inspect(self.root,self.factory)
        self.assertEqual(result['nodes'],{});self.assertFalse(result['connection_ready'])
        self.assertIn('pairing_changed_or_observation_expired',result['blockers'])

    def test_clock_jump_and_expired_probe_discard_observations(self):
        for end in (99,221):
            with patch.object(r,'load_config',return_value=self.config),patch.object(r.time,'time',side_effect=[100,end]):
                result=r.inspect(self.root,self.factory,clock=r.time.time)
            self.assertEqual(result['nodes'],{});self.assertNotIn('checked_at',result)

    def test_restore_barrier_and_credentials_untouched(self):
        execution=self.root/'execution';execution.mkdir();save(execution/'restored.json',{'blocked':True})
        save(self.root/'pairing.json',{'private':'PRIVATE'});save(self.root/'status.json',{'stale':'PRIVATE'})
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        result=self.inspect();self.assertTrue(result['restore_barrier'])
        self.assertIn('restored_execution_remains_blocked',result['blockers'])
        self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_invalid_pairing_and_cli_errors_are_fixed(self):
        save(self.root/'pairing.json',{'schema':1,'generation':'PRIVATE'})
        result=r.inspect(self.root,self.factory);self.assertIn('pairing_record_unavailable',result['blockers'])
        proc=subprocess.run([sys.executable,str(Path(r.__file__)),str(self.root)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0);self.assertNotIn('PRIVATE',proc.stdout+proc.stderr)

    def test_method_and_credential_scope_unchanged(self):
        self.assertEqual(METHODS,('getinfo','listpeerchannels'))
        self.assertEqual(RESTRICTIONS,[['method=getinfo','method=listpeerchannels'],['pnum=0']])
        result=self.inspect();self.assertEqual(list(self.root.iterdir()),[])
        self.assertTrue(result['read_only'])

if __name__=='__main__':unittest.main()
