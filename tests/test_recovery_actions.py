import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
import recovery_actions as actions
import recovery_workflow as workflow
import recovery
import test_lost_journal as fixtures
from controller import save,private_load


class ActionTests(unittest.TestCase):
    setUp=fixtures.LostTests.setUp
    fixture=fixtures.LostTests.fixture
    setup_swap=fixtures.LostTests.setup_swap
    clients=fixtures.LostTests.clients
    factory=fixtures.LostTests.factory

    def ready(self):
        self.setup_swap();self.manager=self.root.parent.parent
        self.request=dict(job=self.root.name,expectedDigest=self.token,
                          btcRune='replacement-btc',xbtRune='replacement-xbt',confirmed=True)

    def test_empty_status_does_not_create_directories(self):
        target=self.parent/'absent'
        self.assertEqual(actions.action(target,'status',{}),dict(live_payment_enabled=False,
            recovery_execution_enabled=False,restored_block=False,jobs=[]))
        self.assertFalse(target.exists())

    def test_status_filtered_local_and_confirmation_is_not_readiness(self):
        self.ready();before=(self.root/'state.json').read_bytes()
        save(self.root/'recovery-admission.json',dict(schema=1,digest=self.token,confirmed=['regtest','xbt-regtest']))
        with patch.object(workflow,'Remote',side_effect=AssertionError('network forbidden')):
            result=actions.action(self.manager,'status',{})
        self.assertFalse(result['recovery_execution_enabled'])
        row=result['jobs'][0];self.assertEqual(row['expected_digest'],self.token)
        self.assertTrue(row['fresh_verification_required'])
        self.assertNotIn('connections',json.dumps(result));self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertEqual(before,(self.root/'state.json').read_bytes())

    def test_live_refuses_before_job_or_rpc(self):
        self.ready()
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'),patch.object(actions,'job_path') as job:
            for mode in ('confirm','recover'):
                with self.assertRaisesRegex(ValueError,'regtest_optin_required'):actions.action(self.manager,mode,self.request)
            job.assert_not_called()

    def test_confirm_uses_pinned_endpoints_and_never_saves_runes(self):
        self.ready()
        with patch.object(workflow,'confirm',return_value={'ok':True}) as confirm:
            actions.action(self.manager,'confirm',dict(self.request,network='regtest'))
        conns=confirm.call_args.args[2]
        for original,replacement in zip(self.configs,conns):
            self.assertEqual({k:v for k,v in original.items() if k!='rune'},
                             {k:v for k,v in replacement.items() if k!='rune'})
        self.assertFalse(any(b'replacement-btc' in p.read_bytes() for p in self.manager.rglob('*.json')))

    def test_invalid_request_digest_and_path_refused_before_workflow(self):
        self.ready()
        bad=[dict(self.request,confirmed=False),dict(self.request,job='../other'),
             dict(self.request,expectedDigest='a'*64),dict(self.request,command='sendpay'),
             dict(self.request,btcRune='secret\nheader')]
        with patch.object(workflow,'run') as run:
            for request in bad:
                with self.assertRaises(ValueError):actions.action(self.manager,'recover',request)
            run.assert_not_called()

    def test_symlink_job_and_parent_refused(self):
        self.ready();link=self.manager/'jobs'/'alias';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):actions.job_path(self.manager,'alias')
        self.assertEqual(actions.status(self.manager)['jobs'][0],dict(job='alias',state='unreadable'))
        parent=self.parent/'manager-link';parent.symlink_to(self.manager,target_is_directory=True)
        with self.assertRaises(ValueError):actions.status(parent)

    def test_malformed_record_does_not_leak_private_values(self):
        self.ready();save(self.root/'recovery-admission.json',dict(schema=1,digest=self.token,confirmed=['PRIVATE-RUNE']))
        report=actions.status(self.manager)
        self.assertEqual(report['jobs'],[dict(job=self.root.name,state='unreadable')])
        self.assertNotIn('PRIVATE',json.dumps(report))

    def test_cli_hides_payload_and_never_accepts_live_optin_from_input(self):
        self.ready();env=dict(os.environ);env.pop('BTC_XBT_DISPOSABLE_CONTAINER',None)
        payload=dict(self.request,BTC_XBT_DISPOSABLE_CONTAINER='1')
        result=subprocess.run([sys.executable,str(ROOT/'assets/recovery_actions.py'),str(self.manager),'recover'],
                              input=json.dumps(payload),text=True,capture_output=True,env=env)
        self.assertEqual(result.returncode,1);self.assertEqual(result.stderr,'')
        self.assertEqual(json.loads(result.stdout)['reason'],'regtest_only')
        self.assertNotIn('replacement',result.stdout)

    def test_gateway_resolves_both_directions_once_after_both_confirmations(self):
        confirm=workflow.confirm;admit=workflow.admit;resolve=recovery.resolve
        for direction in ('forward','reverse'):
            self.setup_swap(direction,status='complete');self.manager=self.root.parent.parent
            request=dict(job=self.root.name,expectedDigest=self.token,btcRune='new-btc',xbtRune='new-xbt',confirmed=True)
            with patch.object(workflow,'confirm',side_effect=lambda *a:confirm(*a,verifier=lambda *x:None)), \
                 patch.object(workflow,'admit',side_effect=lambda *a:admit(*a,verifier=lambda *x:None)), \
                 patch.object(recovery,'resolve',side_effect=lambda *a:resolve(*a,factory=self.clients)):
                actions.action(self.manager,'confirm',dict(request,network='regtest'))
                with self.assertRaisesRegex(ValueError,'both_revocations_required'):
                    actions.action(self.manager,'recover',request)
                self.assertEqual(self.mutations,[])
                actions.action(self.manager,'confirm',dict(request,network='xbt-regtest'))
                first=actions.action(self.manager,'recover',request)
                self.assertEqual(first,actions.action(self.manager,'recover',request))
                self.assertEqual(len(self.mutations),1)
                self.assertTrue(private_load(self.manager/'restored.json')['blocked'])
                self.assertEqual(private_load(self.root/'state.json')['phase'],'prepared')

    def test_recovery_runs_only_after_admission_and_keeps_barrier(self):
        self.ready()
        with patch.object(workflow,'run',return_value={'phase':'outgoing_started'}) as run:
            self.assertEqual(actions.action(self.manager,'recover',self.request),{'phase':'outgoing_started'})
        self.assertEqual(run.call_args.args[:2],(self.root,self.token))
        self.assertTrue(private_load(self.manager/'restored.json')['blocked'])


if __name__=='__main__':unittest.main()
