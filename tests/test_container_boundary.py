import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'assets'), str(ROOT/'tests/remote')]
from controller import save, private_load
import container_bridge

spec = importlib.util.spec_from_file_location('separated_driver', ROOT/'scripts/test-separated-controller.py')
driver = importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)


class BoundaryTests(unittest.TestCase):
    def job(self):
        return dict(direction='forward', flags=['--crash-after-sendpay'], filename='swap-state.json', phase='prepared')

    def test_packaged_recovery_flag_and_image_boundary(self):
        command=driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),
            packaged=True,lifecycle=True,lost=True,packaged_recovery=True)
        self.assertIn('PACKAGED_RECOVERY=1',command)
        self.assertFalse(any('/controller-assets' in str(v) for v in command))
        self.assertFalse(any('dst=/pinned' in str(v) for v in command))

    def test_owner_fence_requires_packaged_recovery_and_keeps_node_access_out(self):
        with self.assertRaisesRegex(ValueError,'owner_fence_requires_packaged_recovery'):
            driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),owner_fence=True)
        command=driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),
            packaged=True,lifecycle=True,lost=True,packaged_recovery=True,owner_fence=True)
        self.assertIn('OWNER_FENCE_TEST=1',command)
        for forbidden in ('dst=/controller-assets','dst=/results','docker.sock','lightning-rpc'):
            self.assertFalse(any(forbidden in str(v) for v in command))

    def test_submission_reply_fault_requires_fencing(self):
        with self.assertRaisesRegex(ValueError,'submission_reply_test_requires_owner_fence'):
            driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),drop_submission_reply=True)
        command=driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),
            packaged=True,lifecycle=True,lost=True,packaged_recovery=True,owner_fence=True,drop_submission_reply=True)
        self.assertIn('DROP_SUBMISSION_REPLY_TEST=1',command)

    def test_partial_fence_checker_runs_fresh_without_node_mounts(self):
        with self.assertRaisesRegex(ValueError,'partial_fence_requires_owner_fence'):
            driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),partial_fence=True)
        command=driver.controller_command(ROOT,Path('/shared'),'image','network','test',self.job(),
            packaged=True,lifecycle=True,lost=True,packaged_recovery=True,owner_fence=True,partial_fence=True)
        self.assertEqual(command[-3:],['/remote-tests/partial_fence_check.py','forward','swap-state.json'])
        self.assertFalse(any('dst=/results' in str(v) or 'docker.sock' in str(v) for v in command))

    def test_fixed_job_schema_rejects_path_and_command_injection(self):
        for field, value in [('filename', '../../wallet'), ('direction', 'shell'),
                             ('flags', ['--entrypoint=sh']), ('phase', 'unknown'),
                             ('flags', '--crash-after-sendpay'), ('flags', [None])]:
            job = self.job(); job[field] = value
            with self.assertRaises(ValueError): driver.validate_job(job)
        job = self.job(); job['command'] = 'sh'
        with self.assertRaises(ValueError): driver.validate_job(job)

    def test_both_directions_and_crash_flags_accepted(self):
        for direction, filename in [('forward', 'swap-state.json'), ('reverse', 'reverse-state.json')]:
            job = self.job(); job.update(direction=direction, filename=filename)
            for flags in ([], ['--crash-after-sendpay'], ['--crash-after-btc'], ['--crash-after-xbt-resolution']):
                job['flags'] = flags
                self.assertEqual(driver.validate_job(job), job)

    def test_controller_mounts_only_control_and_read_only_source(self):
        command = driver.controller_command(Path('/repo'), Path('/shared'), 'sha256:test', 'test-net', 'child', self.job())
        mounts = [command[i+1] for i, value in enumerate(command) if value == '--mount']
        self.assertEqual(len(mounts), 4)
        self.assertEqual(sum(not value.endswith(',readonly') for value in mounts), 1)
        self.assertIn('src=/shared/control,dst=/controller-state', mounts[0])
        for prohibited in ('docker.sock', 'lightning-rpc', '/opt/xbt', '/results', '/nodes', '--privileged'):
            self.assertNotIn(prohibited, ' '.join(command))
        self.assertIn('--read-only', command); self.assertIn('--cap-drop=ALL', command)
        self.assertIn('--security-opt=no-new-privileges', command)
        self.assertEqual(command[command.index('--network')+1], 'test-net')

    def test_packaged_executor_has_no_source_or_pinned_module_mount(self):
        command = driver.controller_command(ROOT, Path('/tmp/exchange'), 'image', 'network', 'worker', self.job(), packaged=True)
        mounts = [command[i+1] for i, v in enumerate(command) if v == '--mount']
        self.assertEqual(len(mounts), 2)
        self.assertTrue(any('dst=/controller-state' in m for m in mounts))
        self.assertFalse(any('dst=/pinned' in m or 'dst=/controller-assets' in m for m in mounts))
        self.assertIn('/remote-tests/package_step.py', command)

    def test_stale_restore_optin_keeps_container_boundaries(self):
        command = driver.controller_command(ROOT, Path('/shared'), 'image', 'none', 'child', self.job(),
                                            partition=True, packaged=True, lifecycle=True, stale=True)
        self.assertIn('STALE_RESTORE_TEST=1', command)
        self.assertIn('/remote-tests/partition_check.py', command)
        mounts = [command[i+1] for i,v in enumerate(command) if v == '--mount']
        self.assertEqual(len(mounts),2)
        self.assertFalse(any('/nodes' in m or 'docker.sock' in m for m in mounts))

    def test_lost_journal_uses_same_isolated_packaged_container(self):
        command = driver.controller_command(ROOT, Path('/shared'), 'image', 'none', 'child', self.job(),
                                            partition=True, packaged=True, lifecycle=True, lost=True)
        self.assertIn('LOST_JOURNAL_TEST=1', command)
        self.assertIn('STALE_RESTORE_TEST=0', command)
        self.assertIn('/remote-tests/partition_check.py', command)
        self.assertIn('--cap-drop=ALL', command)
        mounts = [command[i+1] for i,v in enumerate(command) if v == '--mount']
        self.assertEqual(len(mounts),2)

    def test_resolution_reply_checker_is_network_free_and_has_no_crash_flags(self):
        command = driver.controller_command(ROOT,Path('/shared'),'image','none','child',self.job(),
                                            packaged=True,lifecycle=True,lost=True,reply_check='before')
        self.assertIn('/remote-tests/resolution_reply_check.py',command)
        self.assertEqual(command[-1],'before')
        self.assertEqual(command[command.index('--network')+1],'none')
        self.assertNotIn('--crash-after-sendpay',command)
        self.assertIn('DROP_RESOLUTION_REPLY=0',command)
        fault = driver.controller_command(ROOT,Path('/shared'),'image','test-net','child',self.job(),
                                          packaged=True,lifecycle=True,lost=True,drop_resolution_reply=True)
        self.assertIn('DROP_RESOLUTION_REPLY=1',fault)
        with self.assertRaises(ValueError):
            driver.controller_command(ROOT,Path('/shared'),'image','none','child',self.job(),reply_check='invalid')

    def test_partition_uses_network_none_and_no_crash_flag(self):
        command = driver.controller_command(Path('/repo'), Path('/shared'), 'image', 'none', 'child', self.job(), True)
        self.assertEqual(command[command.index('--network')+1], 'none')
        self.assertIn('/remote-tests/partition_check.py', command)
        self.assertNotIn('--crash-after-sendpay', command)

    def bridge(self, concurrent=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root/'control').mkdir(); (root/'jobs').mkdir()
            source = root/'original.json'
            save(source, {'phase': 'prepared', 'quote': 'original'})
            errors = []
            def host():
                try:
                    end = time.monotonic()+5
                    while not list((root/'jobs').glob('*.request')):
                        if time.monotonic() > end: raise TimeoutError()
                        time.sleep(0.01)
                    request = next((root/'jobs').glob('*.request'))
                    job = driver.validate_job(json.loads(request.read_text()))
                    self.assertEqual(job['flags'], ['--crash-after-sendpay'])
                    # Simulate a child persisting submission intent then exiting
                    # without returning a remote submission acknowledgement.
                    save(root/'control/swap-state.json', {'phase': 'outgoing_started', 'quote': 'original'})
                    if concurrent: save(source, {'phase': 'external-change'})
                    save(request.with_suffix('.response'), dict(returncode=88, stdout='', stderr=''))
                except Exception as exc: errors.append(exc)
            thread = threading.Thread(target=host, daemon=True); thread.start()
            with patch.object(container_bridge, 'EXCHANGE', root), patch.dict(os.environ, {'SEPARATE_CONTROLLER': '1'}), patch.object(sys, 'argv', ['bridge', 'forward', '--state', str(source), '--crash-after-sendpay']):
                if concurrent:
                    with self.assertRaisesRegex(RuntimeError, 'concurrently'): container_bridge.main()
                else:
                    self.assertEqual(container_bridge.main(), 88)
            thread.join(5)
            self.assertFalse(thread.is_alive()); self.assertEqual(errors, [])
            return private_load(source)

    def test_child_crash_checkpoint_copied_back_without_reverting_intent(self):
        self.assertEqual(self.bridge()['phase'], 'outgoing_started')

    def test_concurrent_original_change_preserved_and_refused(self):
        self.assertEqual(self.bridge(True)['phase'], 'external-change')

    def test_bridge_requires_disposable_fixture_optin(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            container_bridge.main()


if __name__ == '__main__': unittest.main()
