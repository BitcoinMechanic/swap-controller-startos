import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'assets'))
import executor
import execution_child
from controller import save,private_load


class SubmissionReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_real_child_exits_before_controller_receives_reply_both_directions(self):
        code='''
import os,sys,types
from pathlib import Path
sys.path.insert(0,sys.argv[1]+'/assets')
import execution_child as child
from controller import private_load,save
root=Path(sys.argv[2]);direction=sys.argv[3]
class Remote:
    def __init__(self,config):self.network=config['network'];self.node_id=config['node_id']
    def call(self,method,*args,**kwargs):
        if method=='getinfo':return dict(network=self.network,id=self.node_id)
        assert method=='sendpay'
        save(root/'coordinator-accepted.json',dict(network=self.network,accepted=True))
        return {'status':'pending'}
child.Remote=Remote
rpc=types.ModuleType('swap_rpc');rpc.RPC=type('RPC',(),{});sys.modules['swap_rpc']=rpc
connections=private_load(root/'remote.json')
def controller(*args,**kwargs):
    save(root/'state.json',{'phase':'outgoing_started'})
    outgoing='xbt-regtest' if direction=='forward' else 'regtest'
    config=next(c for c in connections if c['network']==outgoing)
    rpc.RPC.call([*config['cli'],'-k'],'sendpay')
    save(root/'controller-received.json',{'received':True})
child.runpy.run_path=controller
sys.argv=['execution_child',direction]
child.main()
'''
        for direction in ('forward','reverse'):
            with self.subTest(direction=direction):
                root=self.root/direction;root.mkdir()
                configs=[dict(network=n,node_id='node-'+n,cli=['cli-'+n]) for n in ('regtest','xbt-regtest')]
                save(root/'remote.json',configs)
                result=subprocess.run([sys.executable,'-c',code,str(ROOT),str(root),direction],capture_output=True,text=True,
                    env={**os.environ,'BTC_XBT_DISPOSABLE_CONTAINER':'1','REMOTE_DROP_SEND_REPLY':'1','REMOTE_TEST_ROOT':str(root)})
                self.assertEqual(result.returncode,88,result.stderr)
                self.assertEqual(result.stdout,'');self.assertEqual(result.stderr,'')
                network='xbt-regtest' if direction=='forward' else 'regtest'
                self.assertEqual(private_load(root/'submission-reply-lost.json'),dict(network=network,method='sendpay',reply_discarded=True))
                self.assertEqual(private_load(root/'coordinator-accepted.json'),dict(network=network,accepted=True))
                self.assertEqual(private_load(root/'state.json'),{'phase':'outgoing_started'})
                self.assertFalse((root/'controller-received.json').exists())
                self.assertEqual([json.loads(v) for v in (root/'remote-audit.jsonl').read_text().splitlines()],
                                 [dict(network=network,method='sendpay')])

    def test_executor_enables_fault_only_for_explicit_submission_crash(self):
        env=dict(BTC_XBT_DISPOSABLE_CONTAINER='1',OWNER_FENCE_TEST='1',DROP_SUBMISSION_REPLY_TEST='1',REMOTE_DROP_SEND_REPLY='1')
        with patch.dict(os.environ,env),patch.object(Path,'read_text',return_value=executor.PIN),patch.object(executor.subprocess,'run') as run:
            executor.child(self.root,'forward',('--crash-after-sendpay',))
            self.assertEqual(run.call_args.kwargs['env']['REMOTE_DROP_SEND_REPLY'],'1')
            executor.child(self.root,'forward',())
            self.assertNotIn('REMOTE_DROP_SEND_REPLY',run.call_args.kwargs['env'])
            with patch.dict(os.environ,DROP_SUBMISSION_REPLY_TEST='0'):
                executor.child(self.root,'forward',('--crash-after-sendpay',))
                self.assertNotIn('REMOTE_DROP_SEND_REPLY',run.call_args.kwargs['env'])

    def test_executor_requires_disposable_and_fence_optins(self):
        for missing in ('BTC_XBT_DISPOSABLE_CONTAINER','OWNER_FENCE_TEST'):
            with self.subTest(missing=missing):
                env={'BTC_XBT_DISPOSABLE_CONTAINER':'1','OWNER_FENCE_TEST':'1','DROP_SUBMISSION_REPLY_TEST':'1'};env[missing]='0'
                with patch.dict(os.environ,env),patch.object(Path,'read_text',return_value=executor.PIN),patch.object(executor.subprocess,'run') as run:
                    with self.assertRaises(ValueError):executor.child(self.root,'forward',('--crash-after-sendpay',))
                    run.assert_not_called()

    def test_reads_and_fault_disabled_do_not_exit_or_write(self):
        with patch.dict(os.environ,REMOTE_DROP_SEND_REPLY='1'):
            execution_child.discard_submission_reply(self.root,'regtest','getinfo')
        with patch.dict(os.environ,REMOTE_DROP_SEND_REPLY='0'):
            execution_child.discard_submission_reply(self.root,'regtest','sendpay')
        self.assertEqual(list(self.root.iterdir()),[])

    def test_live_network_and_missing_optin_refused_before_marker(self):
        for network,enabled in (('bitcoin','1'),('regtest','0')):
            with patch.dict(os.environ,REMOTE_DROP_SEND_REPLY='1',BTC_XBT_DISPOSABLE_CONTAINER=enabled):
                with self.assertRaises(ValueError):execution_child.discard_submission_reply(self.root,network,'sendpay')
        self.assertEqual(list(self.root.iterdir()),[])


if __name__=='__main__':unittest.main()
