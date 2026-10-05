import copy
import json
import os
from pathlib import Path
import sys
import unittest
import urllib.error
from unittest.mock import patch,MagicMock
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
import recovery_workflow as workflow
import test_lost_journal as fixtures
from controller import private_load,save


class WorkflowTests(unittest.TestCase):
    setUp=fixtures.LostTests.setUp
    fixture=fixtures.LostTests.fixture
    setup_swap=fixtures.LostTests.setup_swap

    def ready(self):
        self.setup_swap();self.calls=[]
        self.verify=lambda a,b:self.calls.append(a['network'])

    def confirm(self,network):
        return workflow.confirm(self.root,self.token,self.configs2,network,True,self.verify)

    def test_partial_confirmation_durable_and_blocks_run_twice(self):
        self.ready();self.confirm('regtest')
        path=self.root/'recovery-admission.json';before=path.read_bytes()
        def offline(*args):raise OSError('private endpoint')
        with self.assertRaises(OSError):workflow.confirm(self.root,self.token,self.configs2,'xbt-regtest',True,offline)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'both_revocations_required'):
                workflow.admit(self.root,self.token,self.configs2,self.verify)
        self.assertEqual(before,path.read_bytes());self.assertEqual(self.calls,['regtest'])

    def test_both_confirmations_rechecked_and_no_secret_report(self):
        self.ready();self.confirm('xbt-regtest');report=self.confirm('regtest')
        self.assertTrue(report['recovery_ready'])
        self.calls.clear();workflow.admit(self.root,self.token,self.configs2,self.verify)
        self.assertEqual(set(self.calls),workflow.NETWORKS)
        record=private_load(self.root/'recovery-admission.json')
        self.assertEqual(set(record),{'schema','digest','credentials_digest','confirmed'})
        self.assertTrue(private_load(self.root.parent.parent/'restored.json')['blocked'])
        self.assertEqual((self.root/'recovery-admission.json').stat().st_mode&0o777,0o600)

    def test_digest_credential_and_node_changes_refused(self):
        self.ready();self.confirm('regtest')
        for key,value in [('rune','changed'),('node_id','02'+'c'*64),('url','https://other.invalid')]:
            changed=copy.deepcopy(self.configs2);changed[0][key]=value
            with self.assertRaises(ValueError):workflow.confirm(self.root,self.token,changed,'xbt-regtest',True,self.verify)
        with self.assertRaises(ValueError):workflow.confirm(self.root,'a'*64,self.configs2,'regtest',True,self.verify)

    def test_explicit_confirmation_restore_and_optin_required(self):
        self.ready()
        with self.assertRaises(ValueError):workflow.confirm(self.root,self.token,self.configs2,'regtest',False,self.verify)
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'):
            with self.assertRaises(ValueError):self.confirm('regtest')
        (self.root.parent.parent/'restored.json').unlink()
        with self.assertRaises(FileNotFoundError):self.confirm('regtest')
        self.assertFalse((self.root/'recovery-admission.json').exists())

    def test_malformed_record_and_failed_reverification_block(self):
        self.ready();self.confirm('regtest');self.confirm('xbt-regtest')
        with self.assertRaises(OSError):workflow.admit(self.root,self.token,self.configs2,lambda *a:(_ for _ in ()).throw(OSError()))
        p=self.root/'recovery-admission.json';s=private_load(p);s['confirmed']=['regtest','regtest'];save(p,s)
        with self.assertRaises(ValueError):workflow.admit(self.root,self.token,self.configs2,self.verify)

    def test_identity_precedes_refusal_probes(self):
        self.ready();original=self.configs[0];replacement=self.configs2[0]
        remote=MagicMock();remote.call.return_value={'id':'wrong','network':original['network']}
        with patch.object(workflow,'Remote',return_value=remote),patch.object(workflow,'refused') as denied:
            with self.assertRaises(ValueError):workflow.verify(original,replacement)
            denied.assert_not_called()
        remote.call.return_value={'id':original['node_id'],'network':original['network']}
        with patch.object(workflow,'Remote',return_value=remote),patch.object(workflow,'refused') as denied:
            workflow.verify(original,replacement)
            self.assertEqual([c.args[1] for c in denied.call_args_list],['getinfo','sendpay','pay','withdraw','createrune','blacklistrune'])

    def test_only_authentication_refusal_counts(self):
        config={'rune':'private'};remote=MagicMock();remote.client.url='https://fixture.invalid';remote.client.rune='private'
        for code in (401,403,400,500):
            remote.client.opener.open.side_effect=urllib.error.HTTPError('private',code,'private',{},None)
            with patch.object(workflow,'Remote',return_value=remote):
                if code in (401,403):workflow.refused(config,'getinfo')
                else:
                    with self.assertRaises(ValueError):workflow.refused(config,'getinfo')
        remote.client.opener.open.side_effect=OSError('private')
        with patch.object(workflow,'Remote',return_value=remote):
            with self.assertRaises(OSError):workflow.refused(config,'getinfo')

    def test_command_does_not_resolve_without_both_confirmations(self):
        self.ready();creds=self.parent/'credentials.json';save(creds,self.configs2)
        with patch.object(workflow.recovery,'resolve') as resolver:
            with self.assertRaises(ValueError):workflow.command(self.root,self.token,creds,'run',confirmed=True)
            resolver.assert_not_called()


if __name__=='__main__':unittest.main()
