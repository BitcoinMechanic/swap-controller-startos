"""Read-only Neoxa snapshot adapter. Saved quotes never refresh during recovery."""
import json
import time
from decimal import Decimal
import urllib.request
import held_gate as hg
import market_quote as market

BASE='https://neoxa.exchange/api/exchange/'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**kw): raise ValueError('market_unavailable')


def fetch(kind):
    hg.need(kind in ('ticker','orderbook'),'market_unavailable')
    try:
        req=urllib.request.Request(BASE+kind+'/BTCB2_BTC',headers={'Accept':'application/json','Cache-Control':'no-cache'})
        with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(req,timeout=10) as r:
            hg.need(r.status==200,'market_unavailable'); raw=r.read(262145)
        hg.need(len(raw)<=262144,'market_unavailable')
        return json.loads(raw,parse_float=Decimal,object_pairs_hook=hg.unique_object,
                          parse_constant=lambda _:hg.need(False,'market_unavailable'))
    except Exception: raise ValueError('market_unavailable') from None


def make(direction,route,markup_bps,*,clock=time.time,reader=fetch):
    start=time.monotonic(); ticker,book=reader('ticker'),reader('orderbook')
    try:
        hg.need(time.monotonic()-start<=15,'market_unavailable')
        for data in (ticker,book):
            hg.need(type(data) is dict and data.get('success') is True and data.get('pair')=='BTCB2_BTC','market_data_invalid')
        rows=book['asks' if direction=='forward' else 'bids']; levels=[]
        hg.need(type(rows) is list and len(rows)<=50,'market_data_invalid')
        for row in rows:
            hg.need(type(row) is dict and type(row.get('isAmm',False)) is bool,'market_data_invalid')
            if row.get('isAmm') is True: continue
            hg.need(type(row['price']) in (int,str,Decimal) and type(row['quantity']) in (int,str,Decimal),'market_data_invalid')
            levels.append(dict(price=str(row['price']),quantity=str(row['quantity'])))
        now=int(clock()*1000); t=ticker['ticker']
        return market.quote(dict(schema=1,source='neoxa.exchange',pair='BTCB2_BTC',direction=direction,
            markup_bps=markup_bps,fetched_at_ms=now,ticker_at_ms=t['computedAt'],expires_at=now//1000+120,
            bid=str(t['bestBid']),ask=str(t['bestAsk']),levels=levels,recipient_msat=route[-1]['amount_msat'],
            routing_fee_msat=route[0]['amount_msat']-route[-1]['amount_msat'],btc_msat=0,xbt_msat=0),compute=True)
    except Exception: raise ValueError('market_data_invalid') from None
