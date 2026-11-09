import tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
import held_gate as hg
import service,service_cli

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'service';service.initialize(self.root)
    def test_real_worker_lock_release_allows_one_dispatch(self):
        owner=hg.Journal(self.root);self.addCleanup(owner.close)
        entered=threading.Event();calls=[];results=[]
        class Handler:
            def __init__(self,j,**kw):calls.append(j);self.record=service.empty('regtest')
            def status(self):return {'ok':True}
        def invoke():
            entered.set();results.append(service_cli.execute(self.root,{'action':'status'}))
        with patch.object(service_cli.service,'Service',Handler):
            t=threading.Thread(target=invoke);t.start();entered.wait();time.sleep(.12)
            self.assertEqual(calls,[]);owner.close();t.join(2)
            self.assertFalse(t.is_alive());self.assertEqual(results,[{'ok':True}]);self.assertEqual(len(calls),1)
    def test_timeout_preserves_journal_and_never_dispatches(self):
        owner=hg.Journal(self.root);self.addCleanup(owner.close);before=owner.read()
        acquire=service_cli.action_journal
        with patch.object(service_cli,'action_journal',side_effect=lambda d:acquire(d,.1)),patch.object(service_cli.service,'Service') as constructor:
            with self.assertRaisesRegex(ValueError,'^service_lock_timeout$'):service_cli.execute(self.root,{'action':'status'})
            constructor.assert_not_called()
        self.assertEqual(before,owner.read())
    def test_busy_after_dispatch_is_never_replayed_and_lock_is_released(self):
        calls=[]
        class Handler:
            def __init__(self,j,**kw):self.record=service.empty('regtest')
            def configure(self,**kw):calls.append(kw);raise BlockingIOError('uncertain downstream reply')
        with patch.object(service_cli.service,'Service',Handler):
            with self.assertRaises(BlockingIOError):service_cli.execute(self.root,{'action':'configure','paused':True,'markup_bps':0})
        self.assertEqual(len(calls),1)
        j=hg.Journal(self.root);j.close()
    def test_non_lock_error_does_not_retry(self):
        with patch.object(service_cli.hg,'Journal',side_effect=ValueError('journal_invalid')) as constructor:
            with self.assertRaisesRegex(ValueError,'journal_invalid'):service_cli.action_journal(self.root)
            self.assertEqual(constructor.call_count,1)
if __name__=='__main__':unittest.main()
