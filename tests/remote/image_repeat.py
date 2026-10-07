"""Two funded sequential swaps with one setup and fresh controller containers.
Fixture labels are regtest-only; service implementation and runes are packaged.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from controller import save,private_load
from image_quote import bridge
from smoke_regtest import wait_until
from pilot_regtest_node import FixtureSession


def run(lab):
    scenario=os.environ['PILOT_SCENARIO'];assert scenario in ('repeat-normal','repeat-lost-reply','repeat-failure')
    btc=lab.node('knots-btc',False);xbt=lab.node('knots-xbt',True)
    source=Path('/usr/local/libexec/cln-swap');gate=lab.root/'quote_plugin.py'
    gate.write_text('#!'+sys.executable+'\n'+(source/'quote_plugin.py').read_text());gate.chmod(0o700)
    def plugin(role,name):
        p=lab.root/(role+'-pilot');p.write_text('#!/bin/sh\nexec /usr/bin/python3 /remote-tests/pilot_regtest_node.py '+role+' '+str(lab.root/name)+' plugin\n');p.chmod(0o700);return p
    payer=lab.lightning('payer','regtest',btc)
    incoming=lab.lightning('swap-btc','regtest',btc,plugins=(gate,plugin('btc','swap-btc')))
    outgoing=lab.lightning('swap-xbt','xbt-regtest',xbt,plugins=(plugin('xbt','swap-xbt'),))
    hold=lab.root/'hold_htlc.py';hold.write_text('#!'+sys.executable+'\n'+(source/'hold_htlc.py').read_text());hold.chmod(0o700)
    recipient=lab.lightning('receiver','xbt-regtest',xbt,plugins=(hold,) if scenario=='repeat-failure' else ())
    def rpc(n,*args):return lab.rpc(n['cli'],*args)
    def channel(n):
        rows=rpc(n,'listpeerchannels')['channels'];assert len(rows)==1;return rows[0]
    def mine(backend,nodes,count):
        rpc(backend,'generatetoaddress',count,rpc(backend,'getnewaddress'));height=rpc(backend,'getblockcount')
        for n in nodes:wait_until(lambda:rpc(n,'getinfo')['blockheight']>=height,n['proc'],timeout=90)
    def deposit(backend,n,nodes):
        tx=rpc(backend,'sendtoaddress',rpc(n,'newaddr','bech32')['bech32'],'0.02');mine(backend,nodes,1)
        wait_until(lambda:any(o['txid']==tx and o['status']=='confirmed' for o in rpc(n,'listfunds')['outputs']),n['proc'])
    def open_channel(backend,a,b):
        mine(backend,(a,b),1);deposit(backend,a,(a,b));rpc(a,'connect',b['id'],'127.0.0.1',b['port'])
        tx=rpc(a,'fundchannel',b['id'],'1000000sat')['txid'];wait_until(lambda:tx in rpc(backend,'getrawmempool'))
        mine(backend,(a,b),6)
        for n in (a,b):wait_until(lambda:channel(n)['state']=='CHANNELD_NORMAL',n['proc'])
    open_channel(btc,payer,incoming);open_channel(xbt,outgoing,recipient);deposit(btc,incoming,(payer,incoming))
    configs={r:lab.connections[name] for r,name in (('btc','swap-btc'),('xbt','swap-xbt'))}
    save(Path('/exchange/control/pairing.json'),dict(schema=1,generation='a'*32,nodes={k:{f:v[f] for f in ('url','node_id','rune','ca_pem')} for k,v in configs.items()}))
    inspection={};grants={}
    for role,n in (('btc',incoming),('xbt',outgoing)):
        inspection[role+'Rune']=rpc(n,'createrune','null',json.dumps([['method='+m for m in ('getinfo','listpeerchannels','listfunds','decode','listsendpays')]]))['rune']
        grant=FixtureSession(n['data'],role).enable(channel(n)['short_channel_id'],2,True)
        grants[role+'Credential']=grant['credential'];token=json.loads(grant['credential'])
        good=dict(session_id=token['session_id'],operation='info',contract='',pilot_id='',preimage='')
        assert rpc(n,'-k','checkrune','rune='+token['rune'],'method=swap-session-call','params='+json.dumps(good))['valid']
        for method,params in [('sendpay',{}),('close',{}),('createrune',{}),('swap-session-call',dict(good,session_id='f'*64)),('swap-session-call',dict(good,extra='x'))]:
            result=subprocess.run([*n['cli'],'-k','checkrune','rune='+token['rune'],'method='+method,'params='+json.dumps(params)],capture_output=True,text=True,timeout=15)
            assert result.returncode!=0,'grant allowed wrong method, session or parameter shape'
    inspection['confirmed']=True;grants['confirmed']=True
    assert bridge('repeat-setup',dict(inspection=inspection,grants=grants))['remaining']==2
    print('PASS: one setup paired channel-pinned grants; CLN denies raw spending, another grant and extra parameters',flush=True)
    preserved=None;ids=[]
    for index in range(2):
        label='repeat-'+str(index);invoice=rpc(recipient,'invoice','2000000msat',label,'Repeat swap regtest','3600')
        before={n['id']:channel(n)['to_us_msat'] for n in (payer,incoming,outgoing,recipient)}
        prepared=bridge('repeat-prepare',dict(invoice=invoice['bolt11']));ids.append(prepared['pilot_id'])
        assert prepared['approval_required'] is True
        assert rpc(outgoing,'listsendpays',invoice['bolt11'])['payments']==[]
        approved=bridge('repeat-approve',dict(pilotId=ids[-1],confirmed=True));assert approved['phase']=='waiting_for_btc'
        pay=lab.start([*payer['cli'],'pay',approved['invoice']],lab.root/('repeat-pay-'+str(index)+'.log'))
        wait_until(lambda:rpc(incoming,'xbt-quote-status',invoice['payment_hash'])['phase']=='held',incoming['proc'])
        wait_until(lambda:any(h.get('state')=='RCVD_ADD_ACK_REVOCATION' for h in channel(incoming)['htlcs']),incoming['proc'])
        bridge('repeat-worker')
        if scenario=='repeat-failure':
            wait_until(lambda:len(rpc(outgoing,'listsendpays',invoice['bolt11'])['payments'])==1,outgoing['proc'])
            rpc(recipient,'xbt-fail' if index==1 else 'xbt-continue',invoice['payment_hash'])
        expected='failed' if scenario=='repeat-failure' and index==1 else 'settled'
        end=time.monotonic()+90
        while True:
            status=bridge('repeat-worker');row=next(r for r in status['swaps'] if r['pilot_id']==ids[-1])
            if row['phase']==expected:break
            assert time.monotonic()<end,'repeat settlement timeout'
        pay.wait(timeout=30);assert (pay.returncode!=0)==(expected=='failed')
        for n,delta in ((payer,-1000000),(incoming,1000000),(outgoing,-2000000),(recipient,2000000)):
            if expected=='failed':delta=0
            wait_until(lambda:channel(n)['to_us_msat']==before[n['id']]+delta and channel(n)['htlcs']==[],n['proc'])
        attempts=rpc(outgoing,'listsendpays',invoice['bolt11'])['payments'];assert len(attempts)==1
        old_paths=[incoming['data']/'forward-swaps'/(ids[0]+'.json'),outgoing['data']/'forward-swaps'/(ids[0]+'.json'),Path('/exchange/control/execution/forward-swaps')/ids[0]/'record.json']
        if index==0:preserved=[p.read_bytes() for p in old_paths]
        else:assert preserved==[p.read_bytes() for p in old_paths]
        print('PASS: swap '+str(index+1)+' '+expected+'; four balances, cleared HTLCs and one outgoing attempt verified',flush=True)
    assert ids[0]!=ids[1]
    for role,n in (('btc',incoming),('xbt',outgoing)):
        token=json.loads(grants[role+'Credential']);session=FixtureSession(n['data'],role)
        assert len(session.read(token['session_id'])['enrolled'])==2
        info=session.call(token['session_id'],'info','','','');assert info['remaining']==0
    assert len(bridge('repeat-worker')['swaps'])==2
    assert preserved==[p.read_bytes() for p in old_paths]
    print('PASS: original journals retained across second swap and fresh workers; two-slot budget exhausted without reset',flush=True)
