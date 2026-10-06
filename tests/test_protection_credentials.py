"""Reject unsafe credential requests; funded fixtures exercise CLN authorization."""
from pathlib import Path
import sys
import unittest
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
from controller import Refused
from protection_credentials import restrictions


class CredentialTests(unittest.TestCase):
    def test_live_networks_rejected(self):
        for network in ('bitcoin','xbt',None,''):
            with self.subTest(network=network),self.assertRaises(Refused):
                restrictions(network,'close','11'*32)

    def test_target_required_and_cannot_inject_restrictions(self):
        for target in (None,True,1,'','11'*31,'AA'*32,'11'*32+'|method=withdraw'):
            with self.subTest(target=target),self.assertRaises(Refused):
                restrictions('regtest','close',target)

    def test_no_unbound_forward_release_or_spending(self):
        for purpose in ('xbt-release','sendpay','pay','withdraw','close|method=pay'):
            with self.subTest(purpose=purpose),self.assertRaises(Refused):
                restrictions('regtest',purpose,'11'*32)
        with self.assertRaises(Refused):restrictions('regtest','reverse-release','11'*32)

    def test_separate_roles_and_fresh_restrictions(self):
        for network in ('regtest','xbt-regtest'):
            rules=restrictions(network,'close','11'*32)
            methods=rules[0]
            self.assertIn('method=close',methods)
            self.assertFalse(any('release' in m or 'sendpay' == m.removeprefix('method=') for m in methods))
            rules.clear()
            self.assertTrue(restrictions(network,'close','11'*32))
        methods=restrictions('xbt-regtest','reverse-release','22'*32)[0]
        self.assertIn('method=reverse-release',methods)
        self.assertNotIn('method=close',methods)

if __name__=='__main__':unittest.main()
