import copy
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'assets'))
from read_only_rpc import Client, ProbeError, METHODS, RESTRICTIONS
import chain_identity as ci

NODE = '02' + '11'*32


def vector(*bits):
    value = sum(1 << b for b in bits)
    return value.to_bytes((value.bit_length()+7)//8, 'big').hex()


def node_info(profile):
    fork = profile == ci.PRIVKEYIO_XBT
    return dict(network='bitcoin', id=NODE, blockheight=1000000,
                our_features=dict(init=vector(8,14,*([512,515] if fork else [])),
                                  node=vector(8,14,*([512,515] if fork else [])),
                                  invoice=vector(8,14,*([512] if fork else []))))


class ProfileProbeTests(unittest.TestCase):
    def client(self, info, channels=None):
        client = Client('https://localhost', 'synthetic-probe-only')
        self.calls = []
        calls = self.calls
        class Opener:
            def open(self, request, timeout):
                calls.append((request.full_url.rsplit('/',1)[-1],json.loads(request.data)))
                return io.BytesIO(json.dumps(info if calls[-1][0]=='getinfo' else
                                  dict(channels=[] if channels is None else channels)).encode())
        client.opener = Opener()
        return client

    def test_same_network_checks_protocol_before_channel_query(self):
        for wanted, actual in ((ci.BTC,ci.PRIVKEYIO_XBT),(ci.PRIVKEYIO_XBT,ci.BTC)):
            client = self.client(node_info(actual))
            with self.assertRaisesRegex(ProbeError,'^operator_identity_mismatch$'):
                client.inspect_profile(NODE,wanted,'mainnet')
            self.assertEqual(self.calls,[('getinfo',{})])

    def test_valid_probe_is_read_only_and_returns_explicit_binding(self):
        for profile in (ci.BTC,ci.PRIVKEYIO_XBT):
            raw = node_info(profile); before = copy.deepcopy(raw)
            client = self.client(raw,[dict(state='CHANNELD_NORMAL',htlcs=[])])
            result = client.inspect_profile(NODE,profile,'mainnet')
            self.assertEqual(result['chain_binding'],ci.binding(profile,'mainnet',NODE))
            self.assertTrue(result['read_only']); self.assertFalse(result['payment_started'])
            self.assertEqual(self.calls,[('getinfo',{}),('listpeerchannels',{})])
            self.assertEqual(raw,before)
            self.assertNotIn('synthetic',json.dumps(result))
            self.assertNotIn('https',json.dumps(result))

    def test_no_legacy_fallback_on_missing_features(self):
        client = self.client(dict(network='bitcoin',id=NODE,blockheight=1000000))
        with self.assertRaises(ProbeError): client.inspect_profile(NODE,ci.PRIVKEYIO_XBT,'mainnet')
        self.assertEqual(self.calls,[('getinfo',{})])

    def test_wrong_id_and_environment_refused(self):
        for node,environment in (('03'+'22'*32,'mainnet'),(NODE,'regtest')):
            with self.assertRaises(ProbeError):
                self.client(node_info(ci.PRIVKEYIO_XBT)).inspect_profile(node,ci.PRIVKEYIO_XBT,environment)

    def test_expected_identity_is_required_before_transport(self):
        for node,profile,environment in ((None,ci.PRIVKEYIO_XBT,'mainnet'),
                                         (NODE,'guess','mainnet'),(NODE,ci.PRIVKEYIO_XBT,'guess')):
            client=self.client(node_info(ci.PRIVKEYIO_XBT))
            with self.assertRaises(ProbeError): client.inspect_profile(node,profile,environment)
            self.assertEqual(self.calls,[])

    def test_channels_are_not_authority_or_a_signing_type_assertion(self):
        # Probe only counts; unified signing is checked by grant admission,
        # not assumed from NORMAL or an empty HTLC list.
        client = self.client(node_info(ci.PRIVKEYIO_XBT),[dict(state='CHANNELD_NORMAL',htlcs=[])])
        self.assertEqual(client.inspect_profile(NODE,ci.PRIVKEYIO_XBT,'mainnet')['normal_channels'],1)
        for rows in ([None],[{'htlcs':'invalid'}],{}):
            with self.assertRaisesRegex(ProbeError,'^invalid_channel_response$'):
                self.client(node_info(ci.PRIVKEYIO_XBT),rows).inspect_profile(NODE,ci.PRIVKEYIO_XBT,'mainnet')

    def test_mutations_remain_outside_probe_allowlist(self):
        self.assertEqual(METHODS,('getinfo','listpeerchannels'))
        self.assertEqual(RESTRICTIONS,[['method=getinfo','method=listpeerchannels'],['pnum=0']])
        client = self.client(node_info(ci.PRIVKEYIO_XBT))
        for method in ('pay','sendpay','signinvoice','createrune','swap-session-call','decode'):
            with self.assertRaisesRegex(ProbeError,'^method_not_allowed$'):client.call(method)
        self.assertEqual(self.calls,[])


if __name__ == '__main__':
    unittest.main()
