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
