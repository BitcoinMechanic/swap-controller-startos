import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
from forward_pilot import safe_error_reason

class DiagnosticTests(unittest.TestCase):
    def test_unknown_error_text_never_exported(self):
        for error in (ValueError('private-rune'), KeyError('private-rune'), RuntimeError('https://private-host')):
            self.assertEqual(safe_error_reason(error),'pilot_refused_or_uncertain')
    def test_known_refusal_and_missing_binding(self):
        self.assertEqual(safe_error_reason(ValueError('channel_not_unique')),'channel_not_unique')
        self.assertEqual(safe_error_reason(KeyError('funding_outnum')),'missing_field_funding_outnum')
    def test_lock_and_storage(self):
        self.assertEqual(safe_error_reason(BlockingIOError()),'worker_busy_retry_preparation')
        self.assertEqual(safe_error_reason(PermissionError('/private/path')),'pilot_storage_permission_denied')

if __name__=='__main__':unittest.main()
