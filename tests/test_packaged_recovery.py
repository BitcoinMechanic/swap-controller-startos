import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
import recovery
import test_lost_journal as original
from controller import save,private_load


class PackagedResolverTests(original.LostTests):
    def setUp(self):
        super().setUp()
        replacement=patch.object(original,'lost',recovery)
        replacement.start(); self.addCleanup(replacement.stop)

    def test_adapter_recovers_without_reading_fixture_mirror_or_recreating_executor(self):
        # Exercise command output, rather than the legacy adapter's imported resolver.
        self.setup_swap(status='complete')
        credentials=self.parent/'credentials.json';save(credentials,self.configs2)
        with patch.object(recovery,'recovery_factory',return_value=self.clients):
            report=recovery.command(self.root,self.token,credentials,confirmed=True)
        self.assertEqual(report,dict(regtest_only=True,phase='xbt_released',restored_block=True,outgoing_submission_enabled=False))
        self.assertNotIn(self.preimage,json.dumps(report))
        output=private_load(self.root/'recovery-result.json')
        self.assertEqual(output['mirror']['preimage'],self.preimage)
        self.assertEqual((self.root/'recovery-result.json').stat().st_mode & 0o777,0o600)
        self.assertEqual(private_load(self.root/'state.json')['phase'],'prepared')
        self.assertEqual(private_load(self.root.parent.parent/'restored.json'),{'blocked':True})

    def test_adapter_invokes_image_command_and_preserves_lost_reply_exit(self):
        import package_step
        self.setup_swap()
        manager=self.parent/'stale-prepared';self.root.parent.parent.rename(manager)
        self.root=manager/'jobs'/'swap'
        external=self.parent/'reverse-state.json';external.write_text('output only')
        save(self.parent/'lost-journal.json',dict(digest=self.token,direction='reverse',filename=external.name))
        save(self.parent/'remote-recovery.json',self.configs2)
        entry=json.dumps(dict(network='xbt-regtest',method='reverse-release'))+'\n'
        def child(command,**kwargs):
            self.assertEqual(command[1],'/app/recovery.py')
            self.assertIn('--confirm-resolution-only',command)
            (manager/'recovery-audit.jsonl').write_text(entry)
            save(manager/'resolution-reply-lost.json',dict(network='xbt-regtest',method='reverse-release'))
            return subprocess.CompletedProcess(command,89,'','')
        with patch.dict(os.environ,PACKAGED_RECOVERY='1'), patch.object(package_step.subprocess,'run',side_effect=child):
            self.assertEqual(package_step.recover_lost(external,'reverse'),89)
            self.assertEqual(package_step.recover_lost(external,'reverse'),89)
        self.assertEqual((self.parent/'remote-audit.jsonl').read_text(),entry)
        self.assertEqual(external.read_text(),'output only')
        self.assertFalse((self.parent/'execution').exists())

    def test_confirmation_and_disposable_optin_required_before_loading(self):
        with patch.object(recovery,'private_load') as load:
            with self.assertRaises(ValueError): recovery.command(self.parent,'a'*64,self.parent/'missing')
            with patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='0'):
                with self.assertRaises(ValueError): recovery.command(self.parent,'a'*64,self.parent/'missing',True)
            load.assert_not_called()

    def test_live_network_refused_before_rpc(self):
        self.setup_swap()
        intent=private_load(self.root/'intent.json')
        intent['connections'][0]['network']='bitcoin';save(self.root/'intent.json',intent)
        with patch.object(recovery,'Remote') as remote:
            with self.assertRaises(ValueError): recovery.resolve(self.root,self.token,self.configs2,self.parent/'audit')
            remote.assert_not_called()

    def test_cli_error_output_is_filtered(self):
        with patch.object(recovery,'command',side_effect=RuntimeError('SECRET-URL-RUNE')), contextlib.redirect_stdout(io.StringIO()) as output:
            code=recovery.main(['--root',str(self.parent),'--expected-digest','a'*64,'--credentials','private','--confirm-resolution-only'])
        self.assertEqual(code,1)
        self.assertEqual(json.loads(output.getvalue()),dict(event='regtest_recovery_blocked',details='withheld',automatic_retry=False))

    def test_cli_real_process_requires_optin(self):
        result=subprocess.run([sys.executable,str(ROOT/'assets/recovery.py'),'--root',str(self.parent),
            '--expected-digest','a'*64,'--credentials','missing','--confirm-resolution-only'],
            env={**os.environ,'BTC_XBT_DISPOSABLE_CONTAINER':'0'},capture_output=True,text=True)
        self.assertEqual(result.returncode,1);self.assertEqual(result.stderr,'')
        self.assertEqual(json.loads(result.stdout)['event'],'regtest_recovery_blocked')

    def test_packaged_resolution_and_inspection_match_tested_algorithms(self):
        legacy=(ROOT/'tests/remote/lost_journal.py').read_text()
        packaged=(ROOT/'assets/recovery.py').read_text()
        self.assertEqual(legacy[legacy.index('WRITES ='):].strip(),packaged[packaged.index('WRITES ='):packaged.index('\ndef command(')].strip())
        legacy=(ROOT/'tests/remote/stale_restore.py').read_text()
        packaged=(ROOT/'assets/recovery_inspection.py').read_text()
        a=legacy[legacy.index('READS ='):legacy.index('\ndef capture(')].replace('Evidence stays inside the test harness.','Evidence stays private to recovery.')
        self.assertEqual(a.strip(),packaged[packaged.index('READS ='):].strip())


if __name__=='__main__':unittest.main()
