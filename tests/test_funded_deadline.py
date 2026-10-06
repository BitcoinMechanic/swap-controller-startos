"""Check the new fixture's lost-reply boundary without nodes or networking."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app',str(Path(__file__).parent/'remote')]
import deadline_step as runner
from execution_rpc import Remote

class Exited(Exception):pass

class FundedHarnessTests(unittest.TestCase):
    def test_reply_saved_only_as_fixture_evidence_before_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);journal=root/'deadline.json';journal.write_text('{"intent":true}')
            remote=runner.Audited.__new__(runner.Audited);remote.network='regtest';remote.drop=True
            reply=dict(type='unilateral',txids=['a'*64])
            with patch.object(runner,'ROOT',root),patch.object(Remote,'_request',return_value=reply), \
                 patch.object(runner.os,'_exit',side_effect=Exited) as stop:
                with self.assertRaises(Exited):remote._request('close',{})
                stop.assert_called_once_with(89)
            self.assertEqual(journal.read_text(),'{"intent":true}')
            self.assertEqual(json.loads((root/'fixture-close-reply.json').read_text()),reply)
            self.assertEqual(json.loads((root/'deadline-audit.jsonl').read_text()),dict(network='regtest',method='close'))

    def test_normal_reply_returns_to_adapter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);remote=runner.Audited.__new__(runner.Audited)
            remote.network='regtest';remote.drop=False;reply=dict(type='unilateral')
            with patch.object(runner,'ROOT',root),patch.object(Remote,'_request',return_value=reply):
                self.assertEqual(remote._request('close',{}),reply)

    def test_failed_request_has_no_success_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);remote=runner.Audited.__new__(runner.Audited);remote.network='regtest';remote.drop=False
            with patch.object(runner,'ROOT',root),patch.object(Remote,'_request',side_effect=ValueError):
                with self.assertRaises(ValueError):remote._request('close',{})
            self.assertEqual(list(root.iterdir()),[])

    def test_only_incoming_role_receives_close_target(self):
        for direction,incoming in (('forward','btc'),('reverse','xbt')):
            with self.subTest(direction=direction),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                request=dict(spec=dict(direction=direction,channel=dict(channel_id='a'*64)),
                             connections=[dict(network='regtest'),dict(network='xbt-regtest')])
                runner.save(root/'deadline-input.json',request)
                created=[]
                def client(config,target,drop):
                    item=dict(config=config,target=target,drop=drop);created.append(item);return item
                with patch.object(runner,'ROOT',root),patch.object(runner,'isolation'), \
                     patch.dict(runner.os.environ,{'BTC_XBT_DISPOSABLE_CONTAINER':'1'}), \
                     patch.object(runner.sys,'argv',['deadline_step.py','step']), \
                     patch.object(runner,'Audited',side_effect=client), \
                     patch.object(runner,'step',return_value={}) as step,patch('builtins.print'):
                    runner.main()
                clients=step.call_args.args[2]
                self.assertEqual(clients[incoming]['target'],'a'*64)
                self.assertIsNone(clients['xbt' if incoming=='btc' else 'btc']['target'])
                self.assertEqual(len(created),2)

    def test_host_command_mounts_only_control_and_fixture_helpers(self):
        script=Path(__file__).resolve().parents[1]/'scripts/test-funded-deadline.py'
        if not script.exists():self.skipTest('host launcher is intentionally absent from service image')
        spec=importlib.util.spec_from_file_location('funded_launcher',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        command=module.command(Path('/repo'),Path('/exchange'),'image','internal-net','child','step')
        mounts=[command[i+1] for i,v in enumerate(command) if v=='--mount']
        self.assertEqual(mounts,['type=bind,src=/exchange/control,dst=/controller-state',
                                'type=bind,src=/repo/tests/remote,dst=/remote-tests,readonly'])
        self.assertIn('--read-only',command);self.assertIn('--cap-drop=ALL',command)
        with self.assertRaises(ValueError):module.command(Path('/repo'),Path('/exchange'),'i','n','c','close')

if __name__=='__main__':unittest.main()
