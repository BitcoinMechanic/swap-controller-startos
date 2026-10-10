"""Public market data and local markup setting. No payment or exchange trading API."""
import json
import time
from decimal import Decimal
from pathlib import Path
import sys
import urllib.request
import market_terms as mt
from controller import private_load, save
import lifecycle

FILE='market-pricing.json'
BASE='https://neoxa.exchange/api/exchange/'

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('market_unavailable')

def fetch(kind):
    mt.require(kind in ('ticker','orderbook'),'market_unavailable')
    req=urllib.request.Request(BASE+kind+'/BTCB2_BTC',headers={'Accept':'application/json','Cache-Control':'no-cache','User-Agent':'StartOS-SwapController/1.0'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=10) as response:
            mt.require(response.status==200,'market_unavailable')
            raw=response.read(262145)
        mt.require(len(raw)<=262144,'market_unavailable')
        def unique(pairs):
            d={}
            for k,v in pairs:
                mt.require(k not in d);d[k]=v
            return d
        return json.loads(raw,parse_float=Decimal,object_pairs_hook=unique,parse_constant=lambda x: mt.require(False))
    except Exception:raise ValueError('market_unavailable') from None

def settings(root):
    p=Path(root)/FILE
    value=private_load(p) if p.exists() else dict(markup_bps=0)
    mt.require(type(value) is dict and set(value)=={'markup_bps'} and mt.integer(value['markup_bps'],0,500),'invalid_market_settings')
    return value

def configure(root,request):
    mt.require(type(request) is dict and set(request)=={'markupBps'} and mt.integer(request['markupBps'],0,500),'invalid_market_settings')
    with lifecycle.locked(root/'execution'):
        from forward_pilot import allowed
        allowed(root)
        save(root/FILE,dict(markup_bps=request['markupBps']))
    return dict(markupBps=request['markupBps'],new_quotes_only=True)

def make(root,direction,route,*,reader=fetch,clock=time.time):
    policy=settings(root);start=time.monotonic()
    ticker,book=reader('ticker'),reader('orderbook')
    try:
        mt.require(time.monotonic()-start<=15,'market_unavailable')
        for data in (ticker,book):mt.require(type(data) is dict and data.get('success') is True and data.get('pair')=='BTCB2_BTC')
        tick=ticker['ticker'];levels=[]
        rows=book['asks' if direction=='forward' else 'bids']
        mt.require(type(rows) is list and len(rows)<=50)
        for row in rows:
            mt.require(type(row) is dict and type(row.get('isAmm',False)) is bool)
            if row.get('isAmm') is True:continue
            mt.require(type(row['price']) in (int,Decimal,str) and type(row['quantity']) in (int,Decimal,str))
            levels.append(dict(price=str(row['price']),quantity=str(row['quantity'])))
        now_ms=int(clock()*1000)
        q=dict(schema=1,source='neoxa.exchange',pair='BTCB2_BTC',direction=direction,**policy,
            fetched_at_ms=now_ms,ticker_at_ms=tick['computedAt'],expires_at=now_ms//1000+120,
            bid=str(tick['bestBid']),ask=str(tick['bestAsk']),levels=levels,
            recipient_msat=route[-1]['amount_msat'],routing_fee_msat=route[0]['amount_msat']-route[-1]['amount_msat'],btc_msat=0,xbt_msat=0)
        q=mt.quote(q,compute=True)
        mt.require(settings(root)==policy,'invalid_market_settings')
        return q
    except ValueError as e:
        if str(e) in mt.ERRORS:raise
        raise ValueError('market_data_invalid') from None
    except Exception:raise ValueError('market_data_invalid') from None

if __name__=='__main__':
    try:
        root=Path(sys.argv[1]);mode=sys.argv[2]
        if mode=='get':result=dict(markupBps=settings(root)['markup_bps'])
        else:
            mt.require(mode=='configure','invalid_market_settings')
            raw=sys.stdin.read(4097);mt.require(len(raw)<=4096,'invalid_market_settings')
            result=configure(root,json.loads(raw))
        print(json.dumps(result))
    except Exception:print('{"reason":"invalid_market_settings"}');raise SystemExit(1) from None
