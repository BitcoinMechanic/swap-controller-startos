import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
import owner_fence as fence
import old_owner
import executor
from controller import save,private_load
import test_stale_restore as fixtures


class FenceTests(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1',OWNER_FENCE_TEST='1')
        env.start();self.addCleanup(env.stop)
        self.records=[];self.calls=[];self.revoked=set();self.rejections=[]
        for i,network in enumerate(('regtest','xbt-regtest')):
            config=dict(network=network,node_id='02'+str(i+1)*64,cli=['private-cli'],url='https://private',rune='original',ca_pem=None)
            self.records.append(dict(original=config,unique_id=i,derived_rune='derived',recovery=dict(config,rune='recovery'),unrelated=dict(config,rune='unrelated')))

    def rpc(self,config,method,*args):
        self.calls.append((config['network'],method,args))
        if method=='getinfo':return dict(id=config['node_id'],network=config['network'])
        if args:self.revoked.add((config['network'],args[0]))
        return dict(blacklist=[dict(start=i,end=i) for network,i in self.revoked if network==config['network']])

    def reject(self,config,method):
        self.assertTrue(any(n==config['network'] for n,i in self.revoked))
        self.rejections.append((config['network'],config['rune'],method))

    def factory(self,config):
        outer=self
        class Client:
            def call(self,method):
                outer.assertIn(config['rune'],('recovery','unrelated'))
                return dict(id=config['node_id'],network=config['network'])
        return Client()

    def run_fence(self):return fence.fence(self.records,self.rpc,self.reject,self.factory)

    def test_both_identities_checked_before_any_revocation_and_exact_ids_only(self):
        report=self.run_fence()
        self.assertTrue(all(report.values()))
        self.assertEqual([v[1] for v in self.calls[:2]],['getinfo','getinfo'])
        mutations=[v for v in self.calls if v[2]]
        self.assertEqual(mutations,[('regtest','blacklistrune',(0,0)),('xbt-regtest','blacklistrune',(1,1))])
        self.assertEqual(len(self.rejections),8)

    def test_wrong_second_identity_prevents_both_mutations(self):
        def changed(config,method,*args):
            info=self.rpc(config,method,*args)
            if config['network']=='xbt-regtest':info['id']='wrong'
            return info
        with self.assertRaisesRegex(ValueError,'coordinator_identity_changed'):
            fence.fence(self.records,changed,self.reject,self.factory)
        self.assertEqual(self.revoked,set())

    def test_live_network_missing_peer_and_changed_endpoint_refused(self):
        for kind in ('network','missing','binding'):
            with self.subTest(kind=kind):
                records=copy.deepcopy(self.records)
                if kind=='network':records[1]['original']['network']='bitcoin'
                elif kind=='missing':records.pop()
                else:records[1]['recovery']['url']='https://other'
                with self.assertRaises(ValueError):fence.fence(records,self.rpc,self.reject,self.factory)
                self.assertEqual(self.revoked,set())

    def test_partial_revocation_failure_does_not_report_success(self):
        def fail(config,method,*args):
            if method=='blacklistrune' and args and config['network']=='xbt-regtest':raise OSError('private failure')
            return self.rpc(config,method,*args)
        with self.assertRaises(OSError):fence.fence(self.records,fail,self.reject,self.factory)
        self.assertEqual(self.revoked,{('regtest',0)})
        self.assertEqual(self.rejections,[])

    def test_restart_check_refuses_missing_revocation(self):
        self.run_fence();self.revoked.clear()
        with self.assertRaisesRegex(ValueError,'revocation_missing'):
            fence.check_node(self.records[0],self.rpc,self.reject,self.factory)

    def test_missing_optin_prevents_rpc(self):
        with patch.dict(os.environ,OWNER_FENCE_TEST='0'),self.assertRaises(ValueError):self.run_fence()
        self.assertEqual(self.calls,[])

    def test_http_auth_denial_required_not_timeout_or_validation_error(self):
        config=self.records[0]['original']
        for status in (401,403,400,404,500,None):
            with self.subTest(status=status),patch.object(fence,'Remote') as remote:
                remote.return_value.client.url='https://private'
                error=urllib.error.HTTPError('https://private',status,'private',{},io.BytesIO()) if status else OSError('network')
                remote.return_value.client.opener.open.side_effect=error
                if status in (401,403):fence.refused(config,'sendpay')
                else:
                    with self.assertRaises((ValueError,OSError)):fence.refused(config,'sendpay')


class OldOwnerTests(unittest.TestCase):
    setUp=fixtures.StaleTests.setUp
    fixture=fixtures.StaleTests.fixture

    def prepare(self):
        self.fixture('reverse',phase='outgoing_started')
        source=self.parent/'reverse';source.rename(self.parent/'old-owner')
        self.root=self.parent/'old-owner'/'jobs'/'swap'
        (self.parent/'old-owner'/'restored.json').unlink()
        (self.root/'remote-audit.jsonl').write_text('original audit\n')
        save(self.parent/'owner-fence.json',dict(old_credentials_revoked=True,derived_credentials_rejected=True,recovery_credentials_ready=True,unrelated_credentials_ready=True))
        save(self.parent/'fence-restarts.json',{'networks':['regtest','xbt-regtest']})
        env=patch.dict(os.environ,OWNER_FENCE_TEST='1');env.start();self.addCleanup(env.stop)

    def test_retained_executor_runs_and_must_fail_without_journal_or_audit_change(self):
        self.prepare();calls=[]
        def runner(root,direction,flags):
            self.assertEqual(root,self.root);calls.append(direction)
            return subprocess.CompletedProcess([],1,'','')
        for _ in range(2):old_owner.check_old_owner(self.parent,runner,lambda c,m:None)
        self.assertEqual(calls,['reverse','reverse'])
        self.assertEqual(private_load(self.parent/'old-owner-check.json'),dict(checks=2,retained_journal_blocked=True))

    def test_network_outage_cannot_pass_as_revoked_credential(self):
        self.prepare()
        def unavailable(*args):raise OSError('network')
        with self.assertRaises(OSError):old_owner.check_old_owner(self.parent,reject=unavailable)
        self.assertFalse((self.parent/'old-owner-check.json').exists())

    def test_missing_second_restart_blocks_old_executor_and_recovery(self):
        self.prepare();save(self.parent/'fence-restarts.json',{'networks':['regtest']})
        with self.assertRaisesRegex(ValueError,'restart_fence_verification_required'):
            old_owner.check_old_owner(self.parent)

    def test_successful_old_executor_or_changed_state_rejected(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError,'old_executor_not_refused'):
            old_owner.check_old_owner(self.parent,lambda *a:subprocess.CompletedProcess([],0,'',''),lambda *a:None)
        def changed(root,*args):
            (root/'remote-audit.jsonl').write_text('changed')
            return subprocess.CompletedProcess([],1,'','')
        with self.assertRaisesRegex(ValueError,'old_executor_changed_journal_or_audit'):
            old_owner.check_old_owner(self.parent,changed,lambda *a:None)


if __name__=='__main__':unittest.main()
