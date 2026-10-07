"""Funded single forward pilot with real CLN authorization over isolated HTTPS."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
sys.path[:0]=['/controller-assets','/pair-fixtures','/remote-tests']
from controller import save,private_load
from image_pair import check_bundle
from image_remote import RemoteLab
from image_quote import bridge
from smoke_regtest import wait_until
from pilot_regtest_node import FixtureNode


class PilotLab(RemoteLab):
    def start(self,args,*rest,**kwargs):
        if str(args[0]).endswith('/bin/lightningd'):
            args=[*args,'--force-feerates=253'] # Disposable fixed-fee economics only.
        return super().start(args,*rest,**kwargs)


def run(lab):
    scenario=os.environ['PILOT_SCENARIO']
    assert scenario in ('normal','lost-reply','failure','pending-close')
    btc=lab.node('knots-btc',False);xbt=lab.node('knots-xbt',True)
    source=Path('/usr/local/libexec/cln-swap');gate=lab.root/'quote_plugin.py'
    gate.write_text('#!'+sys.executable+'\n'+(source/'quote_plugin.py').read_text());gate.chmod(0o700)
    def plugin(role,name):
        p=lab.root/(role+'-pilot')
        p.write_text('#!/bin/sh\nexec /usr/bin/python3 /remote-tests/pilot_regtest_node.py '+role+' '+str(lab.root/name)+' plugin\n');p.chmod(0o700)
        return p
    payer=lab.lightning('payer','regtest',btc)
    incoming=lab.lightning('swap-btc','regtest',btc,plugins=(gate,plugin('btc','swap-btc')))
    outgoing=lab.lightning('swap-xbt','xbt-regtest',xbt,plugins=(plugin('xbt','swap-xbt'),))
    hold=lab.root/'hold_htlc.py'
    hold.write_text('#!'+sys.executable+'\n'+(source/'hold_htlc.py').read_text());hold.chmod(0o700)
    recipient=lab.lightning('receiver','xbt-regtest',xbt,plugins=(hold,) if scenario in ('failure','pending-close') else ())
    def rpc(node,*args):return lab.rpc(node['cli'],*args)
    def channel(node):
        rows=rpc(node,'listpeerchannels')['channels'];assert len(rows)==1;return rows[0]
    def mine(backend,nodes,count):
        blocks=rpc(backend,'generatetoaddress',count,rpc(backend,'getnewaddress'));height=rpc(backend,'getblockcount')
        for node in nodes:wait_until(lambda:rpc(node,'getinfo')['blockheight']>=height,node['proc'],timeout=90)
        return blocks
    def deposit(backend,node,nodes):
        tx=rpc(backend,'sendtoaddress',rpc(node,'newaddr','bech32')['bech32'],'0.02')
        mine(backend,nodes,1)
        wait_until(lambda:any(o['txid']==tx and o['status']=='confirmed' for o in rpc(node,'listfunds')['outputs']),node['proc'])
    def open_channel(backend,sender,receiver):
        mine(backend,(sender,receiver),1);deposit(backend,sender,(sender,receiver))
        rpc(sender,'connect',receiver['id'],'127.0.0.1',receiver['port'])
        tx=rpc(sender,'fundchannel',receiver['id'],'1000000sat')['txid'];wait_until(lambda:tx in rpc(backend,'getrawmempool'))
        mine(backend,(sender,receiver),6)
        for n in (sender,receiver):wait_until(lambda:channel(n)['state']=='CHANNELD_NORMAL',n['proc'])
    open_channel(btc,payer,incoming);open_channel(xbt,outgoing,recipient)
    deposit(btc,incoming,(payer,incoming))
    # Require policy-compatible live-size HTLCs; never increase test amounts to hide dust refusal.
    from forward_pilot import untrimmed
    for n,amount in ((incoming,1000000),(outgoing,2000000)):untrimmed(channel(n),amount)
    invoice=rpc(recipient,'invoice','2000000msat','pilot-recipient','Isolated forward pilot','3600')
    configs={role:lab.connections[name] for role,name in (('btc','swap-btc'),('xbt','swap-xbt'))}
    inspect={}
    for role,n in (('btc',incoming),('xbt',outgoing)):
        inspect[role]=rpc(n,'createrune','null',json.dumps([['method='+m for m in ('getinfo','listpeerchannels','listfunds','decode','listsendpays')]]))['rune']
    save(Path('/exchange/control/pairing.json'),dict(schema=1,generation='a'*32,nodes={k:{f:configs[k][f] for f in ('url','node_id','rune','ca_pem')} for k in configs}))
    prepared=bridge('prepare',dict(invoice=invoice['bolt11'],incomingChannel=channel(incoming)['short_channel_id'],outgoingChannel=channel(outgoing)['short_channel_id'],btcRune=inspect['btc'],xbtRune=inspect['xbt']))
    assert rpc(outgoing,'listsendpays')['payments']==[] and rpc(incoming,'xbt-pilot-info')['registered_quotes']==0
    grants={}
    for role,n in (('btc',incoming),('xbt',outgoing)):
        grants[role]=FixtureNode(n['data'],role).authorize(prepared['contract'],True)
        rune=grants[role]['rune']
        # Server-side permission tests, not the client allowlist.
        for method,params in [('sendpay',{}),('close',{}),('createrune',{}),('swap-pilot-observe',{'pilot_id':'f'*64})]:
            result=subprocess.run([*n['cli'],'-k','checkrune','rune='+rune,'method='+method,'params='+json.dumps(params)],capture_output=True,text=True,timeout=15)
            assert result.returncode!=0,'pilot rune authorized unrelated method or contract'
        assert rpc(n,'-k','checkrune','rune='+rune,'method=swap-pilot-observe','params='+json.dumps({'pilot_id':prepared['pilot_id']}))['valid']
    print('PASS: both node grants bind one contract; raw send, close, mint and another contract denied',flush=True)
    approved=bridge('approve',dict(pilotId=prepared['pilot_id'],btcRune=grants['btc']['rune'],xbtRune=grants['xbt']['rune'],confirmed=True))
    assert approved['phase']=='waiting_for_btc'
    before={n['id']:channel(n)['to_us_msat'] for n in (payer,incoming,outgoing,recipient)}
    payer_process=lab.start([*payer['cli'],'pay',approved['invoice']],lab.root/'pilot-payer.log')
    wait_until(lambda:rpc(incoming,'xbt-quote-status',invoice['payment_hash'])['phase']=='held',incoming['proc'])
    wait_until(lambda:any(h.get('state')=='RCVD_ADD_ACK_REVOCATION' for h in channel(incoming)['htlcs']),incoming['proc'])
    result=bridge('worker')
    def attempts():return rpc(outgoing,'listsendpays',invoice['bolt11'])['payments']
    if scenario in ('failure','pending-close'):
        wait_until(lambda:len(attempts())==1 and attempts()[0]['status']=='pending',outgoing['proc'])
        if scenario=='failure':
            assert rpc(recipient,'xbt-fail',invoice['payment_hash'])['failed']==1
            wait_until(lambda:attempts()[0]['status']=='failed',outgoing['proc'])
        else:
            bound=next(h for h in channel(incoming)['htlcs'] if h['payment_hash']==invoice['payment_hash'])
            pin={k:channel(incoming)[k] for k in ('funding_txid','funding_outnum')}
            mine(btc,(payer,incoming),bound['expiry']-rpc(btc,'getblockcount')-73)
            bridge('worker');assert not (incoming['data']/'fixture-close.json').exists()
            mine(btc,(payer,incoming),1);bridge('worker');bridge('worker')
            from preimage_claim import run_claim
            def release():
                assert rpc(recipient,'xbt-continue',invoice['payment_hash'])['continued']==1
                wait_until(lambda:attempts()[0]['status']=='complete',outgoing['proc'])
                bridge('worker');assert bridge('worker')['phase']=='onchain_recovery'
                return attempts()[0]['payment_preimage']
            def confirmed(n,txid):return [o for o in rpc(n,'listfunds')['outputs'] if o['txid']==txid and o['status']=='confirmed']
            claim=run_claim(btc,payer,incoming,dict(txid=pin['funding_txid'],outnum=pin['funding_outnum']),
                dict(bolt11=approved['invoice'],payment_hash=invoice['payment_hash']),None,bound['expiry'],
                lambda n:mine(btc,(payer,incoming),n),rpc,confirmed,amount_sat=1000,standalone=False,
                release=release,close=private_load(incoming['data']/'fixture-close.json'))
            payer_process.wait(timeout=30);assert payer_process.returncode==0
            assert claim['payment_preimage']==attempts()[0]['payment_preimage']
            assert len(attempts())==1 and rpc(recipient,'listinvoices','pilot-recipient')['invoices'][0]['status']=='paid'
            for n,delta in ((outgoing,-2000000),(recipient,2000000)):
                wait_until(lambda:channel(n)['to_us_msat']==before[n['id']]+delta and not channel(n)['htlcs'],n['proc'])
            print('PASS: live-sized pilot deadline close, original preimage claim and mature CSV sweep verified on regtest',flush=True)
            return
    target='failed' if scenario=='failure' else 'settled'
    end=time.monotonic()+90
    while result['phase']!=target:
        assert time.monotonic()<end,'pilot settlement timeout'
        result=bridge('worker')
    payer_process.wait(timeout=30);assert (payer_process.returncode!=0)==(scenario=='failure')
    received=rpc(recipient,'listinvoices','pilot-recipient')['invoices'][0]
    if scenario=='failure':assert received['status']=='unpaid'
    else:assert received['status']=='paid' and received['amount_received_msat']==2000000
    for n,delta in ((payer,-1000000),(incoming,1000000),(outgoing,-2000000),(recipient,2000000)):
        if scenario=='failure':delta=0
        wait_until(lambda:channel(n)['to_us_msat']==before[n['id']]+delta and not channel(n)['htlcs'],n['proc'])
    original={r:private_load(lab.root/('swap-btc' if r=='btc' else 'swap-xbt')/'forward-pilot.json') for r in grants}
    assert bridge('worker')['phase']==target
    assert original=={r:private_load(lab.root/('swap-btc' if r=='btc' else 'swap-xbt')/'forward-pilot.json') for r in grants}
    attempts=rpc(outgoing,'listsendpays')['payments'];assert len(attempts)==1 and attempts[0]['status']==('failed' if scenario=='failure' else 'complete')
    print('PASS: packaged pilot '+scenario+' outcome; four balances and empty HTLC sets verified',flush=True)
    print('PASS: fresh worker retained one original attempt and node mutation records',flush=True)


def main():
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1' and os.environ.get('SEPARATE_CONTROLLER')=='1'
    os.environ['PILOT_SCENARIO']=sys.argv[1]
    os.umask(0o077);os.chown('/exchange/control',0,0)
    check_bundle('/usr/local/libexec/cln-swap')
    for name in ('pilot_node.py','pilot_contract.py'):
        assert (Path('/usr/local/libexec/btc-controller')/name).read_bytes()==(Path('/opt/xbt/libexec')/name).read_bytes()
    root=Path(tempfile.mkdtemp(prefix='forward-pilot-',dir='/results'));print('Test directory: '+str(root),flush=True)
    lab=PilotLab(root,'/test-bitcoind','/usr/bin/bitcoin-cli');lab.bound_forward_gate=True
    try:run(lab)
    finally:lab.close()
    print('Funded forward pilot OK (fixture-only network labels; isolated HTTPS; regtest only)',flush=True)
if __name__=='__main__':main()
