import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'assets'),str(ROOT/'tests/remote')]
from controller import save,private_load
import partial_fence
import partial_fence_check
import test_owner_fence as fixtures


class PartialFenceTests(unittest.TestCase):
    rpc=fixtures.FenceTests.rpc
    reject=fixtures.FenceTests.reject

    def setUp(self):
        fixtures.FenceTests.setUp(self)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        save(self.root/'fence-nodes.json',self.records)
        self.offline=False;self.restore_count=0

    def factory(self,config):
        if self.offline and config['network']=='xbt-regtest':
            raise ValueError('HTTPS also needs the RPC socket')
        class Client:
            def call(self,method):return dict(id=config['node_id'],network=config['network'])
        return Client()

    def local(self,config,method,*args):
        if self.offline and config['network']=='xbt-regtest':raise ValueError('admin unavailable')
        return self.rpc(config,method,*args)

    def disconnect(self,path):
        self.offline=True
        return dict(path='private',hidden='private-hidden',inode=7)

    def begin(self):
        partial_fence.begin(self.root,self.local,self.reject,self.factory,lambda c:self.root/'socket',self.disconnect)

    def restore(self,record):
        self.assertEqual(record['inode'],7);self.offline=False;self.restore_count+=1

    def test_partial_fence_preserves_first_revocation_and_never_grants_replacement(self):
        self.begin()
        self.assertEqual(self.revoked,{('regtest',0)})
        self.assertTrue(self.offline)
        self.assertFalse((self.root/'owner-fence.json').exists())
        self.assertEqual(private_load(self.root/'partial-fence.json'),dict(first_revoked=True,second_admin_unavailable=True,second_old_credential_active_before_outage=True,replacement_authorized=False))

    def test_resume_requires_actual_blocked_replacement_evidence(self):
        self.begin()
        with self.assertRaises(FileNotFoundError):partial_fence.resume(self.root,self.local,self.restore,self.factory)
        self.assertTrue(self.offline);self.assertEqual(self.restore_count,0)

    def test_resume_restores_socket_and_checks_first_only_revoked(self):
        self.begin()
        save(self.root/'partial-recovery-blocked.json',dict(replacement_blocked=True,journal_and_audit_unchanged=True))
        partial_fence.resume(self.root,self.local,self.restore,self.factory)
        self.assertFalse(self.offline);self.assertEqual(self.restore_count,1)
        self.assertFalse((self.root/'owner-fence.json').exists())
        self.assertEqual(private_load(self.root/'partial-admin-restored.json'),{'restored':True})

    def test_changed_revocations_prevent_resume_success(self):
        self.begin();self.revoked.clear()
        save(self.root/'partial-recovery-blocked.json',dict(replacement_blocked=True,journal_and_audit_unchanged=True))
        with self.assertRaisesRegex(ValueError,'partial_revocation_state_changed'):partial_fence.resume(self.root,self.local,self.restore,self.factory)
        self.assertFalse((self.root/'partial-admin-restored.json').exists())

    def test_uninterrupted_second_mutation_cannot_pass_as_outage(self):
        with self.assertRaisesRegex(AssertionError,'unavailable_admin_socket_accepted_rpc'):
            partial_fence.begin(self.root,self.rpc,self.reject,self.factory,lambda c:self.root/'socket',self.disconnect)
        self.assertFalse((self.root/'partial-fence.json').exists())

    def test_nonfixture_directory_refused(self):
        with self.assertRaisesRegex(ValueError,'disposable_node_directory_required'):
            partial_fence.socket_path(dict(network='regtest',cli=['cli','--lightning-dir='+str(self.root)]))

    def test_real_unix_socket_unavailable_then_same_inode_restored(self):
        path=self.root/'lightning-rpc'
        try: listener=socket.socket(socket.AF_UNIX)
        except PermissionError: self.skipTest('execution environment denies Unix socket creation; run on packaging VM')
        with listener:
            listener.bind(str(path));listener.listen(1)
            record=partial_fence.disconnect(path)
            with socket.socket(socket.AF_UNIX) as client:
                with self.assertRaises(FileNotFoundError):client.connect(str(path))
            with patch.object(Path,'is_relative_to',return_value=True):partial_fence.reconnect(record)
            self.assertEqual(path.stat().st_ino,record['inode'])
            with socket.socket(socket.AF_UNIX) as client:client.connect(str(path))


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        env=patch.dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1',OWNER_FENCE_TEST='1')
        env.start();self.addCleanup(env.stop)
        save(self.root/'partial-fence.json',dict(first_revoked=True,second_admin_unavailable=True,second_old_credential_active_before_outage=True,replacement_authorized=False))
        save(self.root/'lost-journal.json',dict(direction='forward',filename='swap-state.json',digest='a'*64))

    def test_real_adapter_refuses_twice_before_any_journal_or_audit_change(self):
        partial_fence_check.check(self.root,'forward','swap-state.json')
        self.assertEqual(private_load(self.root/'partial-recovery-blocked.json'),dict(replacement_blocked=True,journal_and_audit_unchanged=True))
        self.assertFalse((self.root/'execution').exists())

    def test_wrong_missing_file_is_not_accepted_as_barrier(self):
        def wrong(*args):raise FileNotFoundError(2,'missing',str(self.root/'other.json'))
        with self.assertRaisesRegex(ValueError,'unexpected_missing_record'):
            partial_fence_check.check(self.root,'forward','swap-state.json',wrong)

    def test_mutation_or_success_is_not_accepted_as_blocked(self):
        def changed(*args):
            save(self.root/'state.json',{'changed':True})
            raise FileNotFoundError(2,'missing',str(self.root/'owner-fence.json'))
        with self.assertRaisesRegex(ValueError,'partial_recovery_changed_journal_or_audit'):
            partial_fence_check.check(self.root,'forward','swap-state.json',changed)
        with self.assertRaisesRegex(AssertionError,'replacement_not_blocked'):
            partial_fence_check.check(self.root,'forward','swap-state.json',lambda *a:0)
        self.assertFalse((self.root/'partial-recovery-blocked.json').exists())


if __name__=='__main__':unittest.main()
