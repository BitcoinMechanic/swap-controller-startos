import json
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import customer_https as https
import service_cli

class Tests(unittest.TestCase):
    def test_amount_limit_reports_asset_and_actual_limit(self):
        g=dict(chain=dict(profile='xbt-privkeyio-v1'),kind='incoming',limits=dict(max_amount_msat=500252000))
        error=service_cli.service.cc.AmountLimitError(g,506701000)
        with tempfile.TemporaryDirectory() as root:
            report=service_cli.failure_report(error,root)
        self.assertEqual(report['reason'],'grant_amount_limit')
        self.assertEqual(report['diagnostic']['node'],'xbt-privkeyio-v1')
        self.assertEqual(report['diagnostic']['amount_msat'],506701000)
        self.assertEqual(report['diagnostic']['maximum_msat'],500252000)

    def client(self):
        c = object.__new__(https.Client)
        c.config = {'url':'https://private.example', 'rune':'SECRET_RUNE'}
        c.grant = {'chain':{'profile':'btc-cln-v1'}, 'kind':'outgoing'}
        c.gid = 'a'*64; c.context = None
        return c

    def test_transport_causes_no_retry_no_secrets(self):
        for error, code in [(TimeoutError('SECRET'), 'node_timeout'),
                            (ssl.SSLError('SECRET'), 'node_tls_error'),
                            (socket.gaierror('SECRET'), 'node_dns_error'),
                            (ConnectionRefusedError('SECRET'), 'node_connection_refused')]:
            connection = Mock(); connection.request.side_effect = error
            with patch.object(https.http.client, 'HTTPSConnection', return_value=connection):
                with self.assertRaises(https.HttpsError) as caught:
                    self.client()._request('enroll', {'invoice':'SECRET_INVOICE'})
            self.assertEqual(str(caught.exception), code)
            self.assertEqual(caught.exception.diagnostic['operation'], 'enroll')
            self.assertEqual(connection.request.call_count, 1)
            self.assertNotIn('SECRET', json.dumps(caught.exception.diagnostic))
            connection.close.assert_called_once()

    def test_response_classification_and_rpc_code(self):
        for status, body, code in [(503,b'PRIVATE','node_http_error'),
                                  (201,b'invalid PRIVATE','node_reply_json_invalid'),
                                  (201,b'{"error":{"code":-32603,"message":"SECRET"}}','node_rpc_rejected'),
                                  (500,b'{"code":-32603,"message":"SECRET"}','node_http_error'),
                                  (201,b'[]','node_reply_shape_invalid'),
                                  (201,b'{"admission_error":"recipient_route_unknown"}','recipient_route_unknown')]:
            response=Mock(status=status)
            response.getheader.return_value='application/json'; response.read.return_value=body
            connection=Mock(); connection.getresponse.return_value=response
            with patch.object(https.http.client,'HTTPSConnection',return_value=connection):
                with self.assertRaises(https.HttpsError) as caught:self.client()._request('plan',{})
            self.assertEqual(str(caught.exception),code)
            self.assertEqual(caught.exception.diagnostic['http_status'],status)
            if code=='node_rpc_rejected' or status==500:self.assertEqual(caught.exception.diagnostic['rpc_code'],-32603)
            self.assertNotIn('SECRET',json.dumps(caught.exception.diagnostic))

    def test_route_admission_details_survive_transport_without_invoice_data(self):
        detail={'route_hint_count':1,'route_hint_entry_unknown':1,'max_fee_msat':10000,'max_delay_blocks':576,'max_hops':4}
        response=Mock(status=201);response.getheader.return_value='application/json'
        response.read.return_value=json.dumps({'admission_error':'route_hint_entry_unknown','admission_details':detail}).encode()
        connection=Mock();connection.getresponse.return_value=response
        with patch.object(https.http.client,'HTTPSConnection',return_value=connection):
            with self.assertRaises(https.HttpsError) as caught:self.client()._request('plan',{'invoice':'SECRET_INVOICE'})
        self.assertEqual(str(caught.exception),'route_hint_entry_unknown')
        for k,v in detail.items():self.assertEqual(caught.exception.diagnostic[k],v)
        self.assertNotIn('SECRET',json.dumps(caught.exception.diagnostic))

    def test_inspect_minimum_details_are_validated(self):
        for details,expected in [({'amount_msat':39000,'minimum_msat':546001},'incoming_amount_below_safe_minimum'),
                                 ({'amount_msat':'SECRET'},'node_reply_shape_invalid'),
                                 ({'rune':'SECRET'},'node_reply_shape_invalid')]:
            response=Mock(status=201); response.getheader.return_value='application/json'
            response.read.return_value=json.dumps({'admission_error':'incoming_amount_below_safe_minimum','admission_details':details}).encode()
            connection=Mock();connection.getresponse.return_value=response
            with patch.object(https.http.client,'HTTPSConnection',return_value=connection):
                with self.assertRaises(https.HttpsError) as caught:self.client()._request('inspect',{'amount_msat':39000})
            self.assertEqual(str(caught.exception),expected)
            if expected=='incoming_amount_below_safe_minimum':self.assertEqual(caught.exception.diagnostic['minimum_msat'],546001)
            self.assertNotIn('SECRET',json.dumps(caught.exception.diagnostic))

    def test_reports_bounded_private_and_preserve_state(self):
        with tempfile.TemporaryDirectory() as root:
            state=Path(root)/'state.json';state.write_text('UNCHANGED')
            for _ in range(67):
                try:raise ValueError('SECRET_INVOICE')
                except ValueError as error:report=service_cli.failure_report(error,root)
            self.assertEqual(report['reason'],'service_request_refused')
            self.assertTrue(report['diagnostic']['saved'])
            files=list((Path(root)/'action-diagnostics').glob('*.json'))
            self.assertEqual(len(files),64)
            for f in files:
                self.assertEqual(f.stat().st_mode & 0o777,0o600)
                self.assertNotIn('SECRET',f.read_text())
            self.assertEqual(state.read_text(),'UNCHANGED')

    def test_report_storage_failure_does_not_replace_original_cause(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(Path,'mkdir',side_effect=OSError('PRIVATE')):
                report=service_cli.failure_report(https.HttpsError('node_timeout',operation='info'),root)
            self.assertEqual(report['reason'],'node_timeout')
            self.assertFalse(report['diagnostic']['saved'])

if __name__=='__main__':unittest.main()
