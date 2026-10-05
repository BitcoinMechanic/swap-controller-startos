import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
from controller import private_load,save
import lost_journal
import resolution_reply_check as checker


class ReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.parent=Path(self.temp.name)
        self.root=self.parent/'stale-prepared'/'jobs'/'swap';self.root.mkdir(parents=True)
        save(self.root.parent.parent/'restored.json',{'blocked':True})
        for name in ('intent.json','remote.json','permit.json'):save(self.root/name,{'fixture':True})
        save(self.root/'state.json',{'phase':'prepared'})
        self.direction='forward';self.status='complete';self.filename='swap-state.json'
        self.receipt=dict(digest='test',attempt={'id':7,'groupid':1,'partid':0},binding=['1x2x3',4],
                          outgoing_status=self.status,stage='resolution_intent')

    def prime(self,direction='forward',status='complete'):
        self.direction=direction;self.status=status
        self.filename='swap-state.json' if direction=='forward' else 'reverse-state.json'
        self.receipt['outgoing_status']=status;self.receipt['stage']='resolution_intent'
        save(self.root/'reconciliation.json',self.receipt)
        save(self.parent/self.filename,{'phase':'outgoing_started'})
        self.network='regtest' if direction=='forward' else 'xbt-regtest'
        self.method=('xbt-' if direction=='forward' else 'reverse-')+('release' if status=='complete' else 'fail')
        self.audit=self.parent/'remote-audit.jsonl'
        self.audit.write_text(json.dumps(dict(network='xbt-regtest' if direction=='forward' else 'regtest',method='sendpay'))+'\n')
        for name in ('resolution-reply-witness.json','continued','coordinator-acted.json'):
            (self.parent/name).unlink(missing_ok=True)

    def crash(self):
        # Real fresh Python process, real ResolutionClient.call and os._exit.
        # Only its remote RPC is faked; funded tests replace this with CLN HTTPS.
        code="""
import sys
from pathlib import Path
sys.path[:0]=[sys.argv[1]+'/assets',sys.argv[1]+'/tests/remote']
from controller import save
from lost_journal import ResolutionClient,allowed
parent=Path(sys.argv[2]);network,direction,method=sys.argv[3:]
class Remote:
    def call(self,*args):
        save(parent/'coordinator-acted.json',{'method':args[0]})
        return {'released':1} if args[0].endswith('release') else {'failed':1}
remote=Remote();remote.network=network
client=ResolutionClient.__new__(ResolutionClient)
client.remote=remote;client.methods=allowed(network,direction);client.audit=parent/'remote-audit.jsonl'
client.call(method)
(parent/'continued').touch()
"""
        return subprocess.run([sys.executable,'-c',code,str(ROOT),str(self.parent),self.network,self.direction,self.method],
            env=dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1',DROP_RESOLUTION_REPLY='1',PYTHONDONTWRITEBYTECODE='1'),
            capture_output=True,text=True,timeout=10)

    def before(self):return checker.check(self.parent,self.direction,self.filename,'before')

    def finish(self):
        self.receipt['stage']='terminal';save(self.root/'reconciliation.json',self.receipt)
        phase=('btc_' if self.direction=='forward' else 'xbt_')+('released' if self.status=='complete' else 'failed')
        save(self.parent/self.filename,{'phase':phase})

    def test_real_process_exits_after_each_gate_reply_before_terminal_checkpoint(self):
        for direction in ('forward','reverse'):
            for status in ('complete','failed'):
                with self.subTest(direction=direction,status=status):
                    self.prime(direction,status)
                    result=self.crash()
                    self.assertEqual((result.returncode,result.stdout,result.stderr),(89,'',''))
                    self.assertEqual(private_load(self.parent/'coordinator-acted.json'),{'method':self.method})
                    self.assertFalse((self.parent/'continued').exists())
                    self.assertEqual(self.before(),{'lost_resolution_reply_verified':True})
                    self.finish()
                    self.assertEqual(checker.check(self.parent,direction,self.filename,'after'),{'lost_resolution_reply_verified':True})

    def test_second_resolution_or_send_in_audit_rejected(self):
        self.prime();self.crash();self.before();self.finish()
        before=self.audit.read_text()
        for method in ('sendpay','xbt-release'):
            self.audit.write_text(before+json.dumps(dict(network=self.network,method=method))+'\n')
            with self.assertRaises(ValueError):checker.check(self.parent,self.direction,self.filename,'after')

    def test_changed_snapshot_or_attempt_rejected(self):
        self.prime();self.crash();self.before();self.finish()
        save(self.root/'permit.json',{'changed':True})
        with self.assertRaisesRegex(ValueError,'fresh_recovery_mutated'):checker.check(self.parent,self.direction,self.filename,'after')
        save(self.root/'permit.json',{'fixture':True})
        self.receipt['attempt']['id']=9;save(self.root/'reconciliation.json',self.receipt)
        with self.assertRaisesRegex(ValueError,'fresh_recovery_mutated'):checker.check(self.parent,self.direction,self.filename,'after')

    def test_premature_terminal_receipt_and_recreated_executor_rejected(self):
        self.prime();self.crash();self.finish()
        with self.assertRaisesRegex(ValueError,'terminal_checkpoint_written'):self.before()
        (self.parent/'execution').mkdir()
        with self.assertRaisesRegex(ValueError,'original_executor_recreated'):self.before()

    def test_fault_disabled_returns_reply_normally(self):
        self.prime()
        class Remote:
            network='regtest'
            def call(self,*args):return {'released':1}
        client=lost_journal.ResolutionClient.__new__(lost_journal.ResolutionClient)
        client.remote=Remote();client.methods=lost_journal.allowed('regtest','forward');client.audit=self.audit
        with patch.dict(os.environ,{'DROP_RESOLUTION_REPLY':'0'}),patch.object(os,'_exit') as stop:
            self.assertEqual(client.call('xbt-release'),{'released':1});stop.assert_not_called()
        self.assertFalse((self.parent/'resolution-reply-lost.json').exists())

    def test_read_requests_never_trigger_fault(self):
        self.prime()
        class Remote:
            network='regtest'
            def call(self,*args):return {'network':'regtest'}
        client=lost_journal.ResolutionClient.__new__(lost_journal.ResolutionClient)
        client.remote=Remote();client.methods=lost_journal.allowed('regtest','forward');client.audit=self.audit
        before=self.audit.read_bytes()
        with patch.dict(os.environ,{'DROP_RESOLUTION_REPLY':'1'}),patch.object(os,'_exit') as stop:
            self.assertEqual(client.call('getinfo'),{'network':'regtest'});stop.assert_not_called()
        self.assertEqual(self.audit.read_bytes(),before)


if __name__=='__main__':unittest.main()
