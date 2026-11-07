"""HTTP regression for the exchange rejecting urllib's default client name."""
import http.server
import threading
import unittest
from unittest.mock import patch
import service_market


class Tests(unittest.TestCase):
    def test_exchange_client_identity_and_fail_closed_responses(self):
        seen = []
        class Handler(http.server.BaseHTTPRequestHandler):
            mode = 'ok'
            def log_message(self, *args): pass
            def do_GET(self):
                seen.append((self.path, dict(self.headers)))
                if self.headers.get('User-Agent') != 'SwapController/0.1.0':
                    self.send_error(403); return
                if self.mode == 'redirect':
                    self.send_response(302); self.send_header('Location', '/unexpected'); self.end_headers(); return
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"success":true}' if self.mode == 'ok' else b'not json')
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(service_market, 'BASE', 'http://127.0.0.1:%s/' % server.server_port):
                for kind in ('ticker', 'orderbook'):
                    self.assertEqual(service_market.fetch(kind), {'success': True})
                for mode in ('redirect', 'invalid'):
                    Handler.mode = mode
                    with self.assertRaisesRegex(ValueError, '^market_unavailable$'):
                        service_market.fetch('ticker')
            self.assertEqual(len(seen), 4)  # No redirected request.
            self.assertEqual([p for p, _ in seen[:2]], ['/ticker/BTCB2_BTC', '/orderbook/BTCB2_BTC'])
            for _, headers in seen:
                self.assertNotIn('Authorization', headers)
                self.assertEqual(headers['Accept'], 'application/json')
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': unittest.main()
