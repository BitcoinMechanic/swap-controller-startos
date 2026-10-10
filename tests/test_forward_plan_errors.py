"""Forward planning through plugin, HTTP client, preparation and CLI boundaries.

Node RPC is simulated. These tests do not claim funded or live success.
"""
import copy
import io
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
from unittest.mock import patch

import test_routed_swaps as cases
from test_routed_swaps import swaps, swap_session, RPCError
import controller
import pilot_node
import read_only_rpc
from pilot_contract import PLAN_ERRORS

OPERATIONS = ('info', 'plan', 'enroll', 'observe', 'publish', 'send', 'close', 'release', 'fail', 'retire')
PRIVATE = 'PRIVATE_INVOICE_URL_RUNE_CERT_PREIMAGE'
OPAQUE = 'session_rpc_refused_or_uncertain'


def plugin_reply(root, params, method='swap-session-call', role='xbt'):
    output = io.StringIO()
    message = dict(jsonrpc='2.0', id=1, method=method, params=params)
    with patch('sys.stdin', io.StringIO(json.dumps(message)+'\n')), patch('sys.stdout', output):
        pilot_node.plugin(root, role)
    return json.loads(output.getvalue())


def remote_with(open_response):
    remote = object.__new__(swaps.SessionRemote)
    remote.session_id = 'a'*64
    remote.client = SimpleNamespace(url='https://coordinator.invalid', rune=PRIVATE,
                                    opener=SimpleNamespace(open=open_response))
    return remote


class BoundaryTests(unittest.TestCase):
    def test_plugin_exposes_only_exact_value_errors_during_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            params = dict(session_id='a'*64, contract=PRIVATE, pilot_id='', preimage='')
            for operation in OPERATIONS:
                for error in [*(ValueError(code) for code in PLAN_ERRORS), ValueError(PRIVATE),
                              ValueError('bounded_route_unavailable '+PRIVATE),
                              RuntimeError('bounded_route_unavailable')]:
                    with self.subTest(operation=operation, error=type(error).__name__, reason=str(error)):
                        with patch.object(swap_session.Session, 'call', side_effect=error):
                            reply = plugin_reply(Path(tmp), dict(params, operation=operation))
                        exposed = operation == 'plan' and isinstance(error, ValueError) and str(error) in PLAN_ERRORS
                        self.assertEqual('result' in reply, exposed)
                        if exposed:
                            self.assertEqual(reply['result'], {'route_error': str(error)})
                        else:
                            self.assertEqual(reply['error']['code'], -32602)
                        self.assertNotIn(PRIVATE, json.dumps(reply))

    def test_plugin_invalid_request_and_legacy_methods_stay_opaque(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = dict(session_id='a'*64, operation='plan', contract=PRIVATE, pilot_id='', preimage='')
            for params, method in [(dict(good, extra=PRIVATE), 'swap-session-call'),
                                   ([PRIVATE], 'swap-session-call'), (good, 'swap-pilot-step')]:
                with patch.object(swap_session.Session, 'call') as call:
                    reply = plugin_reply(Path(tmp), params, method)
                    call.assert_not_called()
                self.assertIn('error', reply)
                self.assertNotIn(PRIVATE, json.dumps(reply))

    def test_client_accepts_only_exact_plan_envelopes(self):
        for operation in OPERATIONS:
            for value in [*({'route_error': code} for code in PLAN_ERRORS),
                          {'route_error': PRIVATE}, {'route_error': ['bounded_route_unavailable']},
                          {'route_error': None}, {'route_error': 'bounded_route_unavailable', 'private': PRIVATE},
                          {'route_error': 'bounded_route_unavailable', 'error': PRIVATE}]:
                with self.subTest(operation=operation, value=value):
                    valid = operation == 'plan' and set(value) == {'route_error'} and type(value['route_error']) is str and value['route_error'] in PLAN_ERRORS
                    remote = remote_with(lambda *a, **k: io.BytesIO(json.dumps(value).encode()))
                    with self.assertRaisesRegex(ValueError, '^'+(value['route_error'] if valid else OPAQUE)+'$'):
                        remote.request(operation)

    def test_old_nodes_and_transport_or_malformed_replies_stay_opaque(self):
        for raw in [b'{', b'[]', b'null', b'x'*1048577,
                    json.dumps({'error': {'code': -32602, 'message': PRIVATE}}).encode()]:
            with self.subTest(raw=raw[:30]):
                with self.assertRaisesRegex(ValueError, '^'+OPAQUE+'$'):
                    remote_with(lambda *a, **k: io.BytesIO(raw)).request('plan')
        for error in [TimeoutError(PRIVATE), urllib.error.HTTPError('https://private.invalid', 403, PRIVATE, {}, None)]:
            def failed(*args, **kwargs):
                raise error
            with self.assertRaisesRegex(ValueError, '^'+OPAQUE+'$'):
                remote_with(failed).request('plan')

    def test_success_responses_and_request_authority_unchanged(self):
        for operation in OPERATIONS:
            result = {'remaining': 2} if operation == 'info' else {'ok': True}
            def opened(request, timeout):
                self.assertEqual(request.full_url, 'https://coordinator.invalid/v1/swap-session-call')
                self.assertEqual(timeout, 20)
                self.assertEqual(request.get_header('Rune'), PRIVATE)
                self.assertEqual(json.loads(request.data), dict(session_id='a'*64, operation=operation,
                    contract='', pilot_id='', preimage=''))
                return io.BytesIO(json.dumps(result).encode())
            self.assertEqual(remote_with(opened).request(operation), result)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        cases.RoutedTests.setUp(self)
        for role, node in self.config['nodes'].items():
            node.update(url='https://'+role+'.invalid', ca_pem='PRIVATE_CERT')
        cases.RoutedTests.setup(self)
        self.invoice = 'lnxbt-'+PRIVATE
        self.rpc['xbt'].invoices[self.invoice] = dict(valid=True, type='bolt11 invoice', currency='xbt',
            amount_msat=2000000, payment_hash='a'*64, payment_secret='b'*64, payee=cases.RECIPIENT,
            created_at=int(cases.time.time()), expiry=7200, min_final_cltv_expiry=40, routes=[])
        # Constructor replacement supplies only simulated RPC; Session.call,
        # the planner, plugin serialization and controller client remain real.
        stub = patch.object(swap_session, 'Session', side_effect=lambda root, role: self.nodes[role])
        stub.start(); self.addCleanup(stub.stop)

    def open_for(self, role):
        def opened(request, timeout):
            reply = plugin_reply(self.nodes[role].root, json.loads(request.data), role=role)
            return io.BytesIO(json.dumps(reply.get('result', reply)).encode())
        return opened

    def transport_remote(self, node, credential):
        remote = remote_with(self.open_for(node['role']))
        remote.session_id = json.loads(credential)['session_id']
        return remote

    def replace_rpc(self, replacement):
        self.nodes['xbt'].rpc = self.nodes['xbt'].node.rpc = replacement

    def no_route(self, code=205):
        original = self.rpc['xbt']
        def rpc(method, **params):
            if method == 'getroutes':
                original.calls.append((method, copy.deepcopy(params)))
                raise RPCError(code)
            return original(method, **params)
        self.replace_rpc(rpc)

    def snapshot(self):
        return {str(p): p.read_bytes() for p in Path(self.tmp.name).rglob('*.json')}

    def assert_refused(self, reason, cli=False):
        before = self.snapshot()
        if cli:
            output = io.StringIO()
            def client(url, *args, **kwargs):
                role = 'btc' if url == self.config['nodes']['btc']['url'] else 'xbt'
                return SimpleNamespace(url=url, rune=PRIVATE, opener=SimpleNamespace(open=self.open_for(role)))
            with patch.object(controller, 'load_config', lambda root: self.config), \
                    patch.object(read_only_rpc, 'Client', side_effect=client), \
                    patch('sys.argv', ['forward_swaps.py', str(self.root), 'prepare']), \
                    patch('sys.stdin', io.StringIO(json.dumps({'invoice': self.invoice}))), patch('sys.stdout', output):
                with self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(swaps.__file__, run_name='__main__')
            self.assertEqual(stopped.exception.code, 1)
            self.assertEqual(json.loads(output.getvalue()), {'reason': reason})
            self.assertNotIn(PRIVATE, output.getvalue())
        else:
            with self.assertRaisesRegex(ValueError, '^'+reason+'$'):
                swaps.prepare(self.root, {'invoice': self.invoice}, factory=self.transport_remote, inspector=self.inspector)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(swaps.paths(self.root))
        for rpc in self.rpc.values():
            self.assertFalse(any(method in ('sendpay', 'pay', 'xbt-register', 'close', 'xbt-release-bound', 'xbt-fail') for method, _ in rpc.calls))

    def test_missing_route_reaches_cli_without_spending_or_reserving(self):
        for code in (205, 206):
            self.no_route(code)
            self.assert_refused('bounded_route_unavailable', cli=True)
        queries = [p for m, p in self.rpc['xbt'].calls if m == 'getroutes']
        self.assertEqual(len(queries), 2)
        for q in queries:
            self.assertEqual((q['maxfee_msat'], q['maxdelay'], q['maxparts']), (10000, 80, 1))

    def test_invalid_recipient_amount_cltv_and_prefix_reach_cli(self):
        for fields in ({'amount_msat': 2000001}, {'min_final_cltv_expiry': 41}):
            original = copy.deepcopy(self.rpc['xbt'].invoices[self.invoice])
            self.rpc['xbt'].invoices[self.invoice].update(fields)
            self.assert_refused('invalid_recipient_invoice', cli=True)
            self.rpc['xbt'].invoices[self.invoice] = original
        self.invoice = 'lnbc-'+PRIVATE
        self.assert_refused('invalid_recipient_invoice', cli=True)

    def test_expired_grant_between_info_and_plan_reaches_cli(self):
        original = self.nodes['xbt'].call
        def call(session_id, operation, contract, pilot_id, preimage):
            if operation == 'plan':
                # Race after info, without editing the persisted grant in this test.
                with patch.object(self.nodes['xbt'], 'clock', lambda: 2**40):
                    return original(session_id, operation, contract, pilot_id, preimage)
            return original(session_id, operation, contract, pilot_id, preimage)
        with patch.object(self.nodes['xbt'], 'call', side_effect=call):
            self.assert_refused('route_planning_refused', cli=True)

    def test_outside_grant_reaches_cli(self):
        original = self.rpc['xbt']
        def rpc(method, **params):
            result = original(method, **params)
            if method == 'getroutes':
                result['routes'][0]['path'][0]['short_channel_id_dir'] = '99x1x0/0'
            return result
        self.replace_rpc(rpc)
        self.assert_refused('route_outside_grant', cli=True)

    def test_unknown_rpc_failure_keeps_uncertainty(self):
        self.no_route(401)
        self.assert_refused(OPAQUE, cli=True)

    def test_success_still_requires_confirmation_and_has_no_enrollment(self):
        before = {str(p): p.read_bytes() for role in self.nodes for p in self.nodes[role].root.rglob('*.json')}
        result = swaps.prepare(self.root, {'invoice': self.invoice}, factory=self.transport_remote, inspector=self.inspector)
        self.assertEqual(result['phase'], 'review')
        self.assertTrue(result['approval_required'])
        self.assertEqual(result['routing_fee_msat'], 1000)
        self.assertEqual({str(p): p.read_bytes() for role in self.nodes for p in self.nodes[role].root.rglob('*.json')}, before)
        for rpc in self.rpc.values():
            self.assertFalse(any(method in ('sendpay', 'pay', 'xbt-register') for method, _ in rpc.calls))


if __name__ == '__main__':
    unittest.main()
