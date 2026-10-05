import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'assets'))
import executor as e
import lifecycle as l
from controller import save, private_load


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'execution';l.setup(self.root)
        self.env=patch.dict(os.environ, BTC_XBT_DISPOSABLE_CONTAINER='1');self.env.start();self.addCleanup(self.env.stop)

    def job(self):
        path=self.root/'jobs'/'swap';path.mkdir()
        connections=[dict(network=n, node_id='02'+str(i)*64,rune='test',ca_pem=None,url='https://localhost:'+str(9000+i),cli=['cli','--network='+n]) for i,n in enumerate(('regtest','xbt-regtest'),1)]
        state=dict(phase='prepared',payment_hash='12'*32,btc_cli=connections[0]['cli'],xbt_cli=connections[1]['cli'])
        token=e.prepare(path,'forward',state,connections)
        e.authorize(path,token,int(time.time())+100,True)
        return path

    def test_default_dormant_worker_never_calls_executor(self):
        self.job()
        with patch.dict(os.environ, BTC_XBT_DISPOSABLE_CONTAINER=''), patch.object(e,'step') as step:
            l.tick(self.root);step.assert_not_called()
        status=l.snapshot(self.root)
        self.assertEqual(status['worker_mode'],'disabled');self.assertTrue(status['worker_fresh'])

    def test_restored_prepared_authorization_cannot_start_or_be_renewed(self):
        path=self.job();before=(path/'state.json').read_bytes()
        l.restored(self.root)
        with self.assertRaisesRegex(ValueError,'restored'):e.step(path)
        with patch.object(e,'step') as step:l.tick(self.root);step.assert_not_called()
        self.assertEqual(before,(path/'state.json').read_bytes())
        self.assertTrue(l.snapshot(self.root)['restored_block'])
        self.assertTrue((path/'permit.json').exists())  # preserved for inspection, never reused

    def test_restore_of_old_empty_backup_still_blocks(self):
        l.restored(self.root);l.tick(self.root)
        self.assertEqual(l.snapshot(self.root)['worker_mode'],'restored')
        with self.assertRaises(ValueError):self.job()

    def test_backup_refuses_prepared_pending_and_unreadable(self):
        path=self.job()
        with self.assertRaises(ValueError):l.backup_begin(self.root)
        state=private_load(path/'state.json');state['phase']='outgoing_started';save(path/'state.json',state)
        with self.assertRaises(ValueError):l.backup_begin(self.root)
        (path/'state.json').write_text('bad')
        with self.assertRaises(ValueError):l.backup_begin(self.root)
        self.assertFalse((self.root/'backup-paused.json').exists())

    def test_empty_backup_pause_and_resume(self):
        l.backup_begin(self.root);l.tick(self.root)
        self.assertEqual(l.snapshot(self.root)['worker_mode'],'backup_paused')
        l.backup_end(self.root);l.tick(self.root)
        self.assertEqual(l.snapshot(self.root)['worker_mode'],'regtest')

    def test_job_lock_serializes_backup_and_restore(self):
        path=self.job()
        with e.lock(path):
            with self.assertRaises(BlockingIOError):l.restored(self.root)
            with self.assertRaises(BlockingIOError):l.backup_begin(self.root)
        self.assertFalse((self.root/'restored.json').exists())

    def test_worker_recovers_existing_job_on_each_fresh_tick(self):
        path=self.job()
        def step(job):
            with e.lock(job):
                e.execution_allowed(job)
                state=private_load(job/'state.json');state['phase']='outgoing_started';save(job/'state.json',state)
            return subprocess.CompletedProcess([],0,'{}','')
        with patch.object(e,'step',side_effect=step) as run:
            l.tick(self.root);l.tick(self.root)
            self.assertEqual(run.call_count,2)
        self.assertEqual(l.snapshot(self.root)['jobs'][0]['outcome'],'pending_recovery')

    def test_status_filters_secrets_and_ages_heartbeat(self):
        self.job()
        save(self.root/'heartbeat.json',dict(checked_at=100,mode='disabled'))
        report=l.snapshot(self.root,now=131)
        self.assertFalse(report['worker_fresh'])
        self.assertNotIn('rune',json.dumps(report));self.assertNotIn('payment_hash',json.dumps(report))
        self.assertFalse(l.snapshot(self.root,now=99)['worker_fresh'])

    def test_real_dormant_process_restart_preserves_restore_barrier(self):
        l.restored(self.root)
        script=Path(l.__file__)
        for _ in range(2):
            proc=subprocess.Popen([sys.executable,str(script),str(self.root),'run'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                limit=time.monotonic()+3
                while not (self.root/'heartbeat.json').exists() and time.monotonic()<limit:time.sleep(.02)
                self.assertIsNone(proc.poll());self.assertEqual(l.snapshot(self.root)['worker_mode'],'restored')
            finally:proc.terminate();proc.communicate(timeout=3)
            (self.root/'heartbeat.json').unlink()


if __name__=='__main__':unittest.main()
