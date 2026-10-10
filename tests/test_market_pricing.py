"""Exact market math and adversarial public-data boundaries; no external calls."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import market_terms as mt
import market_pricing as pricing

class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.t=dict(success=True,pair='BTCB2_BTC',ticker=dict(computedAt=1000000,bestBid='0.0099',bestAsk='0.01'))
        self.b=dict(success=True,pair='BTCB2_BTC',asks=[dict(price='0.01',quantity='2')],bids=[dict(price='0.0099',quantity='2')])
    def make(self,direction='forward',fee=1001):
        a=100000000 if direction=='forward' else 1500000
        return pricing.make(self.root,direction,[dict(amount_msat=a+fee),dict(amount_msat=a)],reader=lambda k:copy.deepcopy(self.t if k=='ticker' else self.b),clock=lambda:1000.001)
    def test_default_zero_and_exact_rounding_both_directions(self):
        self.assertEqual(pricing.settings(self.root),{'markup_bps':0})
        q=self.make();self.assertEqual(q['btc_msat'],1001000);self.assertEqual(q['xbt_msat'],100000000)
        q=self.make('reverse');self.assertEqual(q['xbt_msat'],151718000);self.assertEqual(q['btc_msat'],1500000)
        self.assertEqual(mt.number('0.0099')*151717<1502,True)
    def test_margin_is_configurable_and_only_new_quotes_change(self):
        first=self.make();before=copy.deepcopy(first)
        pricing.configure(self.root,dict(markupBps=100))
        q=self.make();self.assertEqual(q['markup_bps'],100);self.assertGreater(q['btc_msat'],first['btc_msat'])
        self.assertEqual(mt.quote(first),before)
        pricing.configure(self.root,dict(markupBps=0));self.assertEqual(self.make(),before)
    def test_timestamp_boundaries_future_stale_wrong_type(self):
        for stamp in (970001,1000001):
            self.t['ticker']['computedAt']=stamp;self.make()
        for stamp in (970000,1000002,True,'1000000'):
            self.t['ticker']['computedAt']=stamp
            with self.assertRaises(ValueError):self.make()
    def test_identity_and_numeric_types(self):
        for obj,key,value in [(self.t,'pair','BTC_BTCB2'),(self.b,'success',False)]:
            old=obj[key];obj[key]=value
            with self.assertRaises(ValueError):self.make()
            obj[key]=old
        for value in ('NaN','Infinity','-1','0','1e999','1e-999',True,None):
            with self.subTest(value=value):
                self.b['asks'][0]['price']=value
                with self.assertRaises(ValueError):self.make()
    def test_amm_depth_is_excluded(self):
        self.b['asks']=[dict(price='0.01',quantity='999',isAmm=True)]
        with self.assertRaises(ValueError):self.make()
    def test_insufficient_depth_and_reference_gap(self):
        self.b['asks'][0]['quantity']='0.00001'
        with self.assertRaisesRegex(ValueError,'market_depth_unavailable'):self.make()
        self.b['asks'][0].update(quantity='2',price='0.010201')
        with self.assertRaisesRegex(ValueError,'market_depth_unavailable'):self.make()
    def test_depth_is_sorted_and_slippage_bounded(self):
        self.b['asks']=[dict(price='0.0102',quantity='2'),dict(price='0.01',quantity='0.0005')]
        with self.assertRaisesRegex(ValueError,'market_depth_unavailable'):self.make()
        self.b['asks'][0]['price']='0.0101';self.make()
    def test_crossed_or_wide_market(self):
        for bid in ('0.02','0.001'):
            self.t['ticker']['bestBid']=bid
            with self.assertRaises(ValueError):self.make()
    def test_amount_fee_and_markup_caps(self):
        for q in [dict(self.make(),recipient_msat=True),dict(self.make(),routing_fee_msat=10001),dict(self.make(),markup_bps=501),dict(self.make(),markup_bps=True)]:
            with self.assertRaises(ValueError):mt.quote(q)
        for n in (-1,501,True,'100',1.5):
            with self.assertRaisesRegex(ValueError,'invalid_market_settings'):pricing.configure(self.root,{'markupBps':n})
    def test_expiry_checks_only_admission_not_saved_quote_recovery(self):
        q=self.make();c=dict(profile=mt.FORWARD,pricing=q)
        mt.live(c,1119)
        with self.assertRaisesRegex(ValueError,'market_quote_expired'):mt.live(c,1120)
        with patch.object(pricing.time,'time',return_value=999999):self.assertEqual(mt.quote(q),q)
    def test_http_failures_are_sanitized_and_no_redirects(self):
        with patch.object(pricing.urllib.request,'build_opener',side_effect=RuntimeError('PRIVATE RESPONSE')):
            with self.assertRaisesRegex(ValueError,'^market_unavailable$'):pricing.fetch('ticker')
        with self.assertRaisesRegex(ValueError,'market_unavailable'):pricing.NoRedirect().redirect_request(None,None,302,'',{},'https://example.com')
    def test_response_limit_duplicate_fields_and_nonfinite(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n):return self.body
        r=Response()
        for body in (b'x'*262145,b'{"a":1,"a":2}',b'{"a":NaN}'):
            r.body=body
            with patch.object(pricing.urllib.request,'build_opener') as opener:
                opener.return_value.open.return_value=r
                with self.assertRaisesRegex(ValueError,'^market_unavailable$'):pricing.fetch('ticker')
    def test_too_slow_snapshot_and_no_amount_sent_to_exchange(self):
        with patch.object(pricing.time,'monotonic',side_effect=[0,16]):
            with self.assertRaisesRegex(ValueError,'market_unavailable'):self.make()
        with patch.object(pricing.urllib.request,'build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError()
            with self.assertRaises(ValueError):pricing.fetch('orderbook')
            req=opener.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,'https://neoxa.exchange/api/exchange/orderbook/BTCB2_BTC')
            self.assertIsNone(req.data);self.assertEqual(req.get_method(),'GET')

if __name__=='__main__':unittest.main()
