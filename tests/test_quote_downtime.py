"""Test-only fault injection and host absence checks for packaged quote recovery."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app',str(Path(__file__).parent/'remote')]
import executor
import lifecycle
import quote_step
from controller import private_load


class QuoteDowntimeTests(unittest.TestCase):
    def test_release_exit_is_direction_bound_and_patch_removed(self):
        for direction,flag,code in (('forward','--crash-after-btc',87),('reverse','--crash-after-xbt-resolution',89)):
            with self.subTest(direction=direction),tempfile.TemporaryDirectory() as tmp:
                manager=Path(tmp);job=manager/'jobs/swap'
                with patch.object(executor,'records',return_value=({'direction':direction},{})), \
                     patch.object(executor,'step',return_value=subprocess.CompletedProcess([],code)) as step, \
                     patch.object(lifecycle,'tick',side_effect=lambda root:executor.step(job)):
                    quote_step.worker(manager,True)
                    step.assert_called_once_with(job,flags=(flag,))
                    self.assertIs(executor.step,step)
                self.assertEqual(private_load(manager/'resolution-interrupted.json'),dict(returncode=code))

    def test_fault_must_reach_the_expected_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager=Path(tmp)
            with patch.object(executor,'records',return_value=({'direction':'forward'},{})), \
                 patch.object(executor,'step',return_value=subprocess.CompletedProcess([],0)), \
                 patch.object(lifecycle,'tick',side_effect=lambda root:executor.step(manager/'jobs/swap')):
                with self.assertRaises(AssertionError):quote_step.worker(manager,True)
            self.assertFalse((manager/'resolution-interrupted.json').exists())

    def test_normal_worker_has_no_injected_flags(self):
        with patch.object(lifecycle,'tick',return_value={}) as tick:
            quote_step.worker(Path('/manager'))
            tick.assert_called_once_with(Path('/manager'))

    def test_definitive_failure_requires_original_attempt_without_preimage(self):
        # Compile the pure assertion helper without importing node fixture modules.
        source=Path(__file__).parent/'remote/image_quote.py'
        tree=ast.parse(source.read_text())
        helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='check_outcome')
        scope={'hashlib':hashlib}
        exec(compile(ast.Module(body=[helper],type_ignores=[]),str(source),'exec'),scope)
        check=scope['check_outcome']
        original=dict(id=1,groupid=2,partid=0,payment_hash='ab'*32,amount_sent_msat=2000,status='pending')
        failed=dict(original,status='failed')
        check(original,failed,True)
        for changes in ({'status':'pending'},{'status':'complete'},{'payment_preimage':'00'*32},
                        {'id':2},{'groupid':3},{'partid':1},{'payment_hash':'cd'*32},{'amount_sent_msat':2001}):
            with self.subTest(changes=changes),self.assertRaises(AssertionError):
                check(original,dict(failed,**changes),True)

    def test_host_absence_and_container_boundary(self):
        script=Path(__file__).resolve().parents[1]/'scripts/test-quote-flow.py'
        if not script.exists():self.skipTest('host launcher is intentionally absent from service image')
        spec=importlib.util.spec_from_file_location('quote_launcher',script)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with patch.object(module.base,'docker',return_value='') as docker:
            self.assertEqual(json.loads(module.absent('quote-controller-test').stdout),dict(controller_absent=True))
            docker.assert_called_once_with('ps','-aq','--filter','name=^/quote-controller-test$')
        with patch.object(module.base,'docker',return_value='abc123'):
            with self.assertRaises(RuntimeError):module.absent('quote-controller-test')
        with patch.object(module.base,'docker',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):module.absent('quote-controller-test')
        command=module.command(Path('/repo'),Path('/exchange'),'image','net','child','worker-interrupt-resolution')
        mounts=[command[i+1] for i,v in enumerate(command) if v=='--mount']
        self.assertEqual(mounts,['type=bind,src=/exchange/control,dst=/controller-state',
                                'type=bind,src=/repo/tests/remote,dst=/remote-tests,readonly'])
        self.assertIn('--read-only',command);self.assertIn('--cap-drop=ALL',command)
        self.assertEqual(command[-1],'worker-interrupt-resolution')
        with self.assertRaises(ValueError):module.command(Path('/r'),Path('/s'),'i','n','c','assert-controller-absent')


if __name__=='__main__':unittest.main()
