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



class QuoteLimits(unittest.TestCase):
    def quote(self,amount=1000000000,direction='forward',mode='ok'):
        def reader(kind):
            if kind=='ticker':
                return dict(success=True,pair='BTCB2_BTC',ticker=dict(computedAt=1000000 if mode!='stale' else 1,bestBid='0.00379',bestAsk='0.0038'))
            return dict(success=True,pair='BTCB2_BTC',asks=[dict(price='0.0038',quantity='0.00000001' if mode=='shallow' else '1')],bids=[dict(price='0.00379',quantity='1')])
        return service_market.make(direction,[dict(amount_msat=amount)],0,clock=lambda:1000,reader=reader)
    def test_one_million_xbt_round_trip_and_boundaries(self):
        import market_quote
        q=self.quote();self.assertEqual(q['xbt_msat'],1000000000);self.assertEqual(q['btc_msat'],3800000)
        self.assertEqual(market_quote.quote(q),q)
        with self.assertRaisesRegex(ValueError,'^market_amount_limit$'):self.quote(10000001000)
        with self.assertRaisesRegex(ValueError,'^market_amount_limit$'):self.quote(100001000,'reverse')
        with self.assertRaisesRegex(ValueError,'^market_amount_limit$'):self.quote(100000000,'reverse')
    def test_ten_million_xbt_round_trip(self):
        import market_quote
        q=self.quote(10000000000)
        self.assertEqual(q['xbt_msat'],10000000000)
        self.assertEqual(q['btc_msat'],38000000)
        self.assertEqual(market_quote.quote(q),q)
    def test_validation_and_depth_remain_distinct(self):
        with self.assertRaisesRegex(ValueError,'^market_data_invalid$'):self.quote(mode='stale')
        with self.assertRaisesRegex(ValueError,'^market_depth_unavailable$'):self.quote(mode='shallow')
    def test_reasons_cross_only_the_allowlisted_boundary(self):
        import service_cli
        self.assertTrue({'market_amount_limit','market_depth_unavailable'}<=service_cli.SAFE)

if __name__ == '__main__': unittest.main()
