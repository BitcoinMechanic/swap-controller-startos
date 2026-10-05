import http.server
import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'assets'), str(ROOT/'tests/remote')]
from https_rpc import Remote, RpcFailure, parameters, restrictions, LIMIT

ID = '02'+'11'*32
HASH = '12'*32


class Response(io.BytesIO):
    pass


class TransportTests(unittest.TestCase):
    def remote(self):
        remote = Remote(dict(network='regtest', node_id=ID,
            url='https://localhost', rune='secret-rune', ca_pem=None))
        self.requests = []
        test = self
        class Opener:
            def open(self, req, timeout):
                test.requests.append(req)
                value = {'id': ID, 'network': 'regtest'} if req.full_url.endswith('/getinfo') else {'status': 'pending'}
                return Response(json.dumps(value).encode())
        remote.client.opener = Opener()
        return remote

    def test_named_submission_keeps_strings_and_route_types(self):
        remote = self.remote()
        remote.call('sendpay', 'route=[{"amount_msat":200000000}]',
                    'payment_hash='+HASH, 'payment_secret='+HASH, 'bolt11=invoice', named=True)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(json.loads(self.requests[-1].data)['route'][0]['amount_msat'], 200000000)
        self.assertEqual(json.loads(self.requests[-1].data)['payment_hash'], HASH)

    def test_gate_binding_is_json_array(self):
        params = parameters('xbt-regtest', 'reverse-release', (HASH, '["1x2x3", 4]', HASH))
        self.assertEqual(params['binding'], ['1x2x3', 4])

    def test_disallowed_methods_and_parameters_never_use_transport(self):
        remote = self.remote()
        for method, args, named in [('withdraw', (), False), ('getinfo', ('extra',), False),
                ('reverse-release', (HASH, '["1x2x3",4]', HASH), False),
                ('waitsendpay', (HASH, 100), False),
                ('sendpay', ('route=[]', 'unknown=x'), True),
                ('sendpay', ('route=[]', 'route=[]'), True)]:
            with self.assertRaises(ValueError): remote.call(method, *args, named=named)
        self.assertEqual(self.requests, [])

    def test_live_network_rejected(self):
        for network in ('bitcoin', 'xbt'):
            with self.assertRaises(ValueError):
                Remote(dict(network=network))

    def test_wrong_identity_stops_before_mutation(self):
        remote = self.remote(); remote.node_id = '03'+'22'*32
        with self.assertRaisesRegex(ValueError, 'identity'):
            remote.call('xbt-release', HASH)
        self.assertEqual(len(self.requests), 1)

    def test_wrong_network_stops_before_mutation(self):
        remote = self.remote()
        remote.client.opener.open = lambda req, timeout: Response(json.dumps({'id': ID, 'network': 'bitcoin'}).encode())
        with self.assertRaisesRegex(ValueError, 'identity'):
            remote.call('xbt-release', HASH)

    def test_lost_reply_is_private_and_never_retried(self):
        remote = self.remote(); original = remote.client.opener.open
        def fail(req, timeout):
            if req.full_url.endswith('/getinfo'): return original(req, timeout)
            self.requests.append(req)
            raise TimeoutError('secret rune preimage invoice')
        remote.client.opener.open = fail
        with self.assertRaises(RpcFailure) as error: remote.call('xbt-release', HASH)
        self.assertIsInstance(error.exception, subprocess.CalledProcessError)
        self.assertNotIn(HASH, str(error.exception))
        self.assertNotIn('secret', error.exception.output)
        self.assertEqual(len(self.requests), 2)

    def test_http_rejection_body_not_exposed_and_no_retry(self):
        remote = self.remote()
        def fail(req, timeout):
            self.requests.append(req)
            raise urllib.error.HTTPError('https://secret', 401, 'secret', {}, io.BytesIO(b'secret'))
        remote.client.opener.open = fail
        with self.assertRaises(RpcFailure) as error: remote.call('getinfo')
        self.assertNotIn('secret', str(error.exception)+error.exception.output)
        self.assertEqual(len(self.requests), 1)

    def test_oversized_or_malformed_responses_fail_closed(self):
        remote = self.remote()
        for data in (b'x'*(LIMIT+1), b'[]', b'{"error":"secret"}', b'not json'):
            remote.client.opener.open = lambda req, timeout: Response(data)
            with self.assertRaises(RpcFailure): remote.call('getinfo')

    def test_role_specific_rune_allowlists(self):
        for network in ('regtest', 'xbt-regtest'):
            allowed = restrictions(network)[0]
            self.assertIn('method=sendpay', allowed)
            for denied in ('pay', 'withdraw', 'createrune', 'blacklistrune', 'stop', 'plugin', 'close'):
                self.assertNotIn('method='+denied, allowed)
        self.assertNotIn('method=reverse-release', restrictions('regtest')[0])
        self.assertNotIn('method=xbt-release', restrictions('xbt-regtest')[0])

    def test_real_tls_serialization_and_redirect_refusal(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(directory/'key'), '-out', str(directory/'cert'), '-days', '1',
                '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                check=True, capture_output=True)
            requests = []; redirect = []
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_POST(self):
                    requests.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                    if redirect:
                        self.send_response(307); self.send_header('Location', '/stolen'); self.end_headers(); return
                    self.send_response(200); self.end_headers()
                    value = {'id': ID, 'network': 'regtest'} if self.path == '/v1/getinfo' else {'released': 1}
                    self.wfile.write(json.dumps(value).encode())
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(directory/'cert', directory/'key')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                config = dict(network='regtest', node_id=ID, url='https://127.0.0.1:'+str(server.server_port),
                              rune='test-rune', ca_pem=(directory/'cert').read_text())
                remote = Remote(config)
                self.assertEqual(remote.call('xbt-fail', HASH, '["1x2x3",4]'), {'released': 1})
                self.assertEqual(requests[-1][1]['binding'], ['1x2x3', 4])
                with self.assertRaises(RpcFailure): Remote({**config, 'ca_pem': None}).call('getinfo')
                before = len(requests); redirect.append(True)
                with self.assertRaises(RpcFailure): remote.call('getinfo')
                self.assertEqual(len(requests), before+1)
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
