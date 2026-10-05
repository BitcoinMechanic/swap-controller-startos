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
import quote_actions as a
from controller import private_load,save


class QuoteActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'execution';(self.root/'jobs'/'swap'/'proposal').mkdir(parents=True)
        self.job=self.root/'jobs'/'swap';self.now=int(time.time())
        self.connections=[dict(network=n,node_id='02'+str(i)*64,rune='PRIVATE-RUNE',url='https://PRIVATE',ca_pem='PRIVATE-CA',cli=['/fake','--network='+n])
                          for i,n in enumerate(('regtest','xbt-regtest'),1)]
        self.data=dict(config={},terms=dict(btc_amount_msat=100000000,xbt_amount_msat=200000000,expires_at=self.now+600),
                       controller=dict(route=[dict(id='03'+'33'*32)]))
        record=dict(schema=1,source_commit=a.executor.PIN,connections=self.connections,quote=self.data,request_digest='ff'*32)
        save(self.job/'review.json',record);save(self.job/'proposal'/'quote.json',self.data)
        save(self.root/a.PAIR_FILE,self.connections)
        self.token=a.executor.digest(record)
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1');env.start();self.addCleanup(env.stop)

    def test_status_is_local_filtered_and_empty_does_not_create(self):
        missing=Path(self.tmp.name)/'missing'
        self.assertEqual(a.action(missing,'status',{})['quotes'],[]);self.assertFalse(missing.exists())
        with patch.object(a.workflow,'QuoteRemote',side_effect=AssertionError('network forbidden')):
            report=a.action(self.root,'status',{})
            one=a.action(self.root,'review',{'job':'swap'})
        self.assertEqual(report['quotes'][0]['review_digest'],self.token)
        self.assertEqual(one['btc_price_sats'],100000)
        self.assertNotIn('PRIVATE',json.dumps(report));self.assertNotIn('btc_invoice',one)
        self.assertFalse((self.job/'approval.json').exists())

    def test_live_guard_precedes_configuration_job_and_rpc(self):
        with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'),patch.object(a,'private_load',side_effect=AssertionError('read forbidden')), \
            patch.object(a.workflow,'prepare',side_effect=AssertionError('RPC forbidden')):
            for mode in ('prepare','approve'):
                with self.assertRaisesRegex(ValueError,'regtest_optin_required'):a.action(self.root,mode,{})

    def test_prepare_loads_only_fixed_disposable_config(self):
        expected=a.workflow.report(self.job)
        with patch.object(a.workflow,'prepare',return_value=expected) as prepare:
            request=dict(job='new',xbtInvoice='lnxbtrt1test',btcSats=100000)
            result=a.action(self.root,'prepare',request)
            prepare.assert_called_once_with(self.root,'new',dict(connections=self.connections,xbt_invoice='lnxbtrt1test',btc_sats=100000))
        self.assertEqual(result['job'],'new');self.assertNotIn('PRIVATE',json.dumps(result))
        for key in ('connections','url','rune','manager','BTC_XBT_DISPOSABLE_CONTAINER'):
            with self.assertRaises(ValueError):a.action(self.root,'prepare',dict(request,**{key:'injected'}))

    def test_prepare_rejects_bad_price_invoice_and_job_before_workflow(self):
        valid=dict(job='new',xbtInvoice='lnxbtrt1test',btcSats=100000)
        cases=[dict(valid,job='../outside'),dict(valid,xbtInvoice='lnxbt1live'),dict(valid,btcSats=True),
               dict(valid,btcSats=0),dict(valid,btcSats=1000001),dict(valid,btcSats=1.1)]
        with patch.object(a.workflow,'prepare',side_effect=AssertionError('must not prepare')):
            for request in cases:
                with self.assertRaises(ValueError):a.action(self.root,'prepare',request)

    def test_approval_requires_digest_and_explicit_confirmation(self):
        valid=dict(job='swap',expectedDigest=self.token,confirmed=True)
        with patch.object(a.workflow,'approve',side_effect=AssertionError('must not approve')):
            for request in (dict(valid,confirmed=False),dict(valid,confirmed=1),dict(valid,expectedDigest='bad'),dict(valid,connections=[])):
                with self.assertRaises(ValueError):a.action(self.root,'approve',request)
        result=dict(a.workflow.report(self.job),btc_invoice='lnbcrt1signed',rune='PRIVATE')
        with patch.object(a.workflow,'approve',return_value=result) as approve:
            output=a.action(self.root,'approve',valid)
            approve.assert_called_once_with(self.root,'swap',self.token,True)
        self.assertEqual(output['btc_invoice'],'lnbcrt1signed');self.assertNotIn('PRIVATE',json.dumps(output))

    def test_corrupt_record_or_approval_is_unreadable_without_private_details(self):
        save(self.job/'approval.json',dict(digest='00'*32,expires_at=self.now+600))
        self.assertEqual(a.status(self.root)['quotes'],[dict(job='swap',state='unreadable')])
        (self.job/'approval.json').unlink()
        record=private_load(self.job/'review.json');record['quote']['controller']['route'][0]['id']='PRIVATE'
        save(self.job/'review.json',record);save(self.job/'proposal'/'quote.json',record['quote'])
        self.assertNotIn('PRIVATE',json.dumps(a.status(self.root)))
        self.assertEqual(a.status(self.root)['quotes'][0]['state'],'unreadable')

    def test_symlinks_refused_and_pairing_never_read_by_status(self):
        (self.root/a.PAIR_FILE).unlink();(self.root/a.PAIR_FILE).symlink_to(self.job/'review.json')
        self.assertEqual(a.status(self.root)['quotes'][0]['state'],'recorded')
        with self.assertRaises(OSError):a.action(self.root,'prepare',dict(job='new',xbtInvoice='lnxbtrt1test',btcSats=1))
        (self.root/'jobs'/'alias').symlink_to(self.job,target_is_directory=True)
        with self.assertRaises(ValueError):a.action(self.root,'review',{'job':'alias'})
        outside=Path(self.tmp.name)/'alias';outside.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(ValueError):a.status(outside)

    def test_cli_has_fixed_error_and_no_request_optin(self):
        program=Path(a.__file__);env=dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0')
        result=subprocess.run([sys.executable,str(program),str(self.root),'prepare'],
            input=json.dumps(dict(BTC_XBT_DISPOSABLE_CONTAINER='1',private='PRIVATE')),capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,1);self.assertEqual(json.loads(result.stdout)['reason'],'regtest_only')
        self.assertNotIn('PRIVATE',result.stdout+result.stderr)
        result=subprocess.run([sys.executable,str(program),str(self.root),'status'],input='{}',capture_output=True,text=True,env=env)
        self.assertEqual(result.returncode,0);self.assertFalse(json.loads(result.stdout)['live_payment_enabled'])


if __name__=='__main__':unittest.main()
