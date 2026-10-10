"""Deterministic public market responses, fixture-only; never copied into images."""
import os
import time
assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
LIMITS=dict(max_btc_msat=10000000,max_xbt_msat=500000000,total_btc_msat=20000000,total_xbt_msat=1000000000)
def read(kind):
    if kind=='ticker':return dict(success=True,pair='BTCB2_BTC',ticker=dict(computedAt=int(time.time()*1000),bestBid='0.0099',bestAsk='0.01'))
    assert kind=='orderbook'
    return dict(success=True,pair='BTCB2_BTC',asks=[dict(price='0.01',quantity='2')],bids=[dict(price='0.0099',quantity='2')])
