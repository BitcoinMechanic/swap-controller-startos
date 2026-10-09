"""Seven-node funded routed swaps with a two-hop public BTC prefix. Both customers are non-neighbors.

Fixture network labels only; production route planning, authority, gate and
worker are unchanged. Fresh HTTPS controller container for each step.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from controller import save,private_load
from image_quote import bridge
from smoke_regtest import wait_until
from reverse_regtest_node import FixtureSession


def run(lab):
    scenario=os.environ['PILOT_SCENARIO']
    assert scenario in ('reverse-routed-normal','reverse-routed-lost-reply','reverse-routed-failure','reverse-routed-restart')
    restart_pending=scenario.endswith('restart')
    xbt=lab.node('knots-xbt',True);btc=lab.node('knots-btc',False)
    source=Path('/usr/local/libexec/cln-swap');gate=lab.root/'reverse-gate'
    gate.write_text('#!/bin/sh\nexec /usr/bin/python3 /remote-tests/reverse_regtest_gate.py '+str(lab.root/'reverse_gate.quotes.json')+'\n');gate.chmod(0o700)
    def plugin(role,name):
        p=lab.root/(role+'-pilot');p.write_text('#!/bin/sh\nexec /usr/bin/python3 /remote-tests/reverse_regtest_node.py '+role+' '+str(lab.root/name)+' plugin\n');p.chmod(0o700);return p
    payer=lab.lightning('payer','xbt-regtest',xbt)
    xbt_router=lab.lightning('xbt-router','xbt-regtest',xbt)
    incoming=lab.lightning('swap-xbt','xbt-regtest',xbt,plugins=(gate,plugin('xbt','swap-xbt')))
    outgoing=lab.lightning('swap-btc','regtest',btc,plugins=(plugin('btc','swap-btc'),))
    btc_prefix=lab.lightning('btc-prefix','regtest',btc)
    btc_router=lab.lightning('btc-router','regtest',btc)
    hold=lab.root/'hold_htlc.py';hold.write_text('#!'+sys.executable+'\n'+(source/'hold_htlc.py').read_text());hold.chmod(0o700)
    recipient=lab.lightning('receiver','regtest',btc,plugins=(hold,) if scenario.endswith('failure') or restart_pending else ())
    xbt_nodes=(payer,xbt_router,incoming);btc_nodes=(outgoing,btc_prefix,btc_router,recipient)
    def rpc(n,*args):return lab.rpc(n['cli'],*args)
    def rows(n):return rpc(n,'listpeerchannels')['channels']
    def channel(n,peer):
        found=[c for c in rows(n) if c['peer_id']==peer['id'] and c['state']=='CHANNELD_NORMAL'];assert len(found)==1;return found[0]
    def mine(backend,nodes,count):
        rpc(backend,'generatetoaddress',count,rpc(backend,'getnewaddress'));height=rpc(backend,'getblockcount')
        for n in nodes:wait_until(lambda:rpc(n,'getinfo')['blockheight']>=height,n['proc'],timeout=90)
    def deposit(backend,n,nodes):
        tx=rpc(backend,'sendtoaddress',rpc(n,'newaddr','bech32')['bech32'],'0.02');mine(backend,nodes,1)
        wait_until(lambda:any(o['txid']==tx and o['status']=='confirmed' for o in rpc(n,'listfunds')['outputs']),n['proc'])
    def open_channel(backend,a,b,nodes,private=False):
        deposit(backend,a,nodes);rpc(a,'connect',b['id'],'127.0.0.1',b['port'])
        tx=rpc(a,'-k','fundchannel','id='+b['id'],'amount=1000000sat','announce='+('false' if private else 'true'))['txid']
        wait_until(lambda:tx in rpc(backend,'getrawmempool'));mine(backend,nodes,6)
        for n,peer in ((a,b),(b,a)):
            wait_until(lambda:any(c['funding_txid']==tx and c['state']=='CHANNELD_NORMAL' for c in rows(n)),n['proc'])
    # Public prefixes and private final channels exercise invoice hints on both sides.
    open_channel(xbt,payer,xbt_router,xbt_nodes)
    open_channel(xbt,xbt_router,incoming,xbt_nodes,private=True)
    open_channel(btc,outgoing,btc_prefix,btc_nodes)
    open_channel(btc,btc_prefix,btc_router,btc_nodes)
    open_channel(btc,btc_router,recipient,btc_nodes,private=True)
    deposit(xbt,incoming,xbt_nodes)
    for n,peer in ((incoming,xbt_router),(outgoing,btc_prefix)):
        ch=channel(n,peer)
        assert 'option_anchors' in ch['features'],'fixture must use zero-fee HTLC anchors'
        assert ch['feerate']['perkw']==1255,'fixture must exercise the reported commitment fee'
    assert channel(incoming,xbt_router)['receivable_msat']>=3000000
    print('PASS: actual anchor channels at 1255 perkw admit reverse amounts',flush=True)
    for n,count in ((payer,2),(outgoing,4)):
        wait_until(lambda:len(rpc(n,'listchannels')['channels'])>=count,n['proc'],timeout=90)
    assert payer['id'] not in [c['peer_id'] for c in rows(incoming)]
    assert recipient['id'] not in [c['peer_id'] for c in rows(outgoing)]
    configs={r:lab.connections[name] for r,name in (('xbt','swap-xbt'),('btc','swap-btc'))}
    save(Path('/exchange/control/pairing.json'),dict(schema=1,generation='a'*32,nodes={k:{f:v[f] for f in ('url','node_id','rune','ca_pem')} for k,v in configs.items()}))
    inspection={};grants={}
    for role,n in (('xbt',incoming),('btc',outgoing)):
        inspection[role+'Rune']=rpc(n,'createrune','null',json.dumps([['method='+m for m in ('getinfo','listpeerchannels','listfunds','decode','listsendpays')]]))['rune']
        grant=FixtureSession(n['data'],role).enable('',2,True,routed_grant=True,max_delay=288)
        grants[role+'Credential']=grant['credential'];token=json.loads(grant['credential'])
        good=dict(session_id=token['session_id'],operation='info',contract='',pilot_id='',preimage='')
        assert rpc(n,'-k','checkrune','rune='+token['rune'],'method=swap-reverse-call','params='+json.dumps(good))['valid']
        for method,params in [('swap-session-call',good),('sendpay',{}),('pay',{}),('close',{}),('createrune',{}),('swap-reverse-call',dict(good,session_id='f'*64)),('swap-reverse-call',dict(good,extra='x'))]:
            result=subprocess.run([*n['cli'],'-k','checkrune','rune='+token['rune'],'method='+method,'params='+json.dumps(params)],capture_output=True,text=True,timeout=15)
            assert result.returncode!=0,'grant allowed foreign authority'
    inspection['confirmed']=True;grants['confirmed']=True
    assert bridge('reverse-setup',dict(inspection=inspection,grants=grants))['remaining']==2
    print('PASS: seven-node topology; two BTC forwarding hops at 80 blocks and 1,001 msat each; routed grants deny raw mutations',flush=True)
    def balance(n):return sum(c['to_us_msat'] for c in rows(n))
    preserved=None;ids=[]
    for index in range(2):
        label='reverse-routed-'+str(index);invoice=rpc(recipient,'invoice','1500000msat',label,'Routed swap regtest','3600')
        hints=rpc(recipient,'decode',invoice['bolt11']).get('routes',[])
        assert any(len(h)==1 and h[0]['cltv_expiry_delta']==80 for h in hints),'recipient must supply the real 80-block private hint'
        before={n['id']:balance(n) for n in (*xbt_nodes,*btc_nodes)}
        prepared=bridge('reverse-prepare',dict(invoice=invoice['bolt11']));swap_id=prepared['pilot_id'];ids.append(swap_id)
        assert prepared['approval_required'] and prepared['routed'] and 0<=prepared['routing_fee_msat']<=10000
        assert prepared['max_delay_blocks']==288 and prepared['route_delay_blocks']==200
        assert prepared['routing_fee_msat']==2002
        record_path=Path('/exchange/control/execution/reverse-swaps')/swap_id/'record.json'
        c=private_load(record_path)['contract'];assert len(c['route'])==3 and c['route'][-1]['id']==recipient['id']
        assert rpc(outgoing,'listsendpays',invoice['bolt11'])['payments']==[]
        approved=bridge('reverse-approve',dict(pilotId=swap_id,confirmed=True));assert approved['phase']=='waiting_for_xbt'
        incoming_channel=channel(incoming,xbt_router)
        assert 'option_scid_alias' in incoming_channel.get('features',[]),'fixture must exercise negotiated SCID aliases'
        expected_alias=incoming_channel['alias']['remote']
        assert expected_alias!=incoming_channel['short_channel_id']
        hinted=rpc(incoming,'decode',approved['invoice']).get('routes',[])
        assert any(len(h)==1 and h[0]['pubkey']==xbt_router['id'] and h[0]['short_channel_id']==expected_alias for h in hinted), 'incoming invoice must advertise remote alias, not funding SCID'
        payer_log=lab.root/('routed-pay-'+str(index)+'.log')
        pay=lab.start([*payer['cli'],'pay',approved['invoice']],payer_log)
        def incoming_held():
            if rpc(incoming,'reverse-status',invoice['payment_hash'])['phase']=='held':return True
            if pay.poll() is not None:
                raise RuntimeError('routed XBT payer exited before gate held: '+payer_log.read_text()[-8192:])
            return False
        try:wait_until(incoming_held,incoming['proc'])
        except RuntimeError:
            print('Routed XBT payer output: '+payer_log.read_text()[-8192:],flush=True)
            raise
        wait_until(lambda:any(h.get('state')=='RCVD_ADD_ACK_REVOCATION' for h in channel(incoming,xbt_router)['htlcs']),incoming['proc'])
        held=[h for h in channel(incoming,xbt_router)['htlcs'] if h['payment_hash']==invoice['payment_hash']]
        assert len(held)==1 and held[0].get('local_trimmed',False) is False,'incoming anchor HTLC must actually be untrimmed'
        bridge('reverse-worker')
        if scenario.endswith('failure') or restart_pending:
            wait_until(lambda:len(rpc(outgoing,'listsendpays',invoice['bolt11'])['payments'])==1,outgoing['proc'])
            # Wait for the receiver's test-only hold hook, then settle/fail once.
            wait_until(lambda:any(h.get('payment_hash')==invoice['payment_hash'] for h in channel(recipient,btc_router)['htlcs']),recipient['proc'])
            if restart_pending:
                def htlc_identity(n,peer,direction):
                    ch=channel(n,peer)
                    matches=[h for h in ch['htlcs'] if h['payment_hash']==invoice['payment_hash'] and h['direction']==direction]
                    if len(matches)!=1:return None
                    h=matches[0]
                    return tuple(ch[k] for k in ('channel_id','short_channel_id','funding_txid','funding_outnum'))+tuple(h[k] for k in ('id','direction','payment_hash','amount_msat','expiry','state'))
                for n,peer,direction,state in ((incoming,xbt_router,'in','RCVD_ADD_ACK_REVOCATION'),(outgoing,btc_prefix,'out','SENT_ADD_ACK_REVOCATION')):
                    wait_until(lambda:any(h['payment_hash']==invoice['payment_hash'] and h['direction']==direction and h['state']==state for h in channel(n,peer)['htlcs']),n['proc'])
                identities={incoming['id']:htlc_identity(incoming,xbt_router,'in'),outgoing['id']:htlc_identity(outgoing,btc_prefix,'out')}
                assert all(value is not None for value in identities.values()),'original pending HTLC missing or ambiguous'
                attempt_before=rpc(outgoing,'listsendpays',invoice['bolt11'])['payments'][0]
                assert attempt_before['status']=='pending' and not attempt_before.get('payment_preimage')
                bridge('reverse-worker')
                assert private_load(record_path).get('outgoing_htlc'),'original BTC HTLC must be durably recorded'
                record_paths=[record_path,incoming['data']/'reverse-swaps'/(swap_id+'.json'),outgoing['data']/'reverse-swaps'/(swap_id+'.json')]
                records_before=[p.read_bytes() for p in record_paths]
                gate_before=rpc(incoming,'reverse-status',invoice['payment_hash'])
                endpoints=json.loads(json.dumps(lab.connections))
                for n in (incoming,outgoing):
                    lab.stop(n['proc'])
                    n['log'].rename(n['log'].with_name('before-routed-restart-'+str(index)+'.log'))
                for n,name,network,backend,plugins in ((incoming,'swap-xbt','xbt-regtest',xbt,(gate,plugin('xbt','swap-xbt'))),(outgoing,'swap-btc','regtest',btc,(plugin('btc','swap-btc'),))):
                    resumed=lab.lightning(name,network,backend,plugins=plugins)
                    assert resumed['id']==n['id'] and resumed['cli']==n['cli'],'coordinator identity changed'
                    n.update(resumed)
                assert lab.connections==endpoints,'saved HTTPS endpoints or credentials changed'
                rpc(xbt_router,'connect',incoming['id'],'127.0.0.1',incoming['port'])
                rpc(outgoing,'connect',btc_prefix['id'],'127.0.0.1',btc_prefix['port'])
                wait_until(lambda:any(h['payment_hash']==invoice['payment_hash'] for h in rpc(incoming,'xbt-held')['held']),incoming['proc'])
                for n,peer,direction in ((incoming,xbt_router,'in'),(outgoing,btc_prefix,'out')):
                    wait_until(lambda:channel(n,peer)['peer_connected'] and htlc_identity(n,peer,direction)==identities[n['id']],n['proc'])
                assert rpc(incoming,'reverse-status',invoice['payment_hash'])==gate_before,'gate binding changed'
                for _ in range(2):
                    status=bridge('reverse-worker')
                    current=next(r for r in status['swaps'] if r['pilot_id']==swap_id)
                    assert current['phase']=='send_intent' and current['needs_attention'] is False
                    attempts=rpc(outgoing,'listsendpays',invoice['bolt11'])['payments']
                    assert len(attempts)==1 and attempts[0]['status']=='pending'
                    for key in ('id','groupid','payment_hash','amount_msat','amount_sent_msat'):
                        assert attempts[0][key]==attempt_before[key],'original payment attempt changed'
                    assert attempts[0].get('partid',0)==attempt_before.get('partid',0)
                    assert not attempts[0].get('payment_preimage')
                assert [p.read_bytes() for p in record_paths[1:]]==records_before[1:],'node authority records changed'
                resumed_record=private_load(record_path)
                original_record=json.loads(records_before[0])
                for key in ('contract','binding','expiry','incoming_pin','outgoing_htlc'):
                    assert resumed_record[key]==original_record[key],'controller payment binding changed'
                print('PASS: both coordinators restarted while routed payment pending; original HTLCs, HTTPS authority and outgoing attempt survived two fresh workers',flush=True)
            rpc(recipient,'xbt-fail' if scenario.endswith('failure') and index==1 else 'xbt-continue',invoice['payment_hash'])
        expected='failed' if scenario.endswith('failure') and index==1 else 'settled'
        end=time.monotonic()+90
        while True:
            status=bridge('reverse-worker');row=next(r for r in status['swaps'] if r['pilot_id']==swap_id)
            if row['phase']==expected:break
            assert time.monotonic()<end,'routed settlement timeout'
        pay.wait(timeout=30);assert (pay.returncode!=0)==(expected=='failed')
        attempts=rpc(outgoing,'listsendpays',invoice['bolt11'])['payments'];assert len(attempts)==1
        assert attempts[0]['amount_sent_msat']==c['route'][0]['amount_msat']
        btc_fee=c['route'][0]['amount_msat']-1500000
        payer_payments=rpc(payer,'-k','listsendpays','payment_hash='+invoice['payment_hash'])['payments']
        completed=[p for p in payer_payments if p['status']=='complete']
        if expected=='settled':assert len(completed)==1
        xbt_fee=completed[0]['amount_sent_msat']-3000000 if expected=='settled' else 0
        received=rpc(recipient,'listinvoices',label)['invoices'][0]
        assert received['status']==('paid' if expected=='settled' else 'unpaid')
        if expected=='settled':assert received['amount_received_msat']==1500000
        prefix_fee=c['route'][0]['amount_msat']-c['route'][1]['amount_msat']
        hint_fee=c['route'][1]['amount_msat']-1500000
        assert prefix_fee==hint_fee==1001
        deltas=(-3000000-xbt_fee,xbt_fee,3000000,-1500000-btc_fee,prefix_fee,hint_fee,1500000)
        for n,delta in zip((*xbt_nodes,*btc_nodes),deltas):
            if expected=='failed':delta=0
            wait_until(lambda:balance(n)==before[n['id']]+delta and all(c['htlcs']==[] for c in rows(n)),n['proc'])
        node_record=private_load(incoming['data']/'reverse-swaps'/(swap_id+'.json'))
        assert node_record['incoming_pin']['channel_id']==channel(incoming,xbt_router)['channel_id']
        assert node_record['incoming_binding']==private_load(record_path)['binding']
        old_paths=[incoming['data']/'reverse-swaps'/(ids[0]+'.json'),outgoing['data']/'reverse-swaps'/(ids[0]+'.json'),Path('/exchange/control/execution/reverse-swaps')/ids[0]/'record.json']
        if index==0:preserved=[p.read_bytes() for p in old_paths]
        else:assert preserved==[p.read_bytes() for p in old_paths]
        print('PASS: routed swap '+str(index+1)+' '+expected+'; seven balances, public/private routing fees, actual incoming pin and one outgoing attempt verified',flush=True)
    for role,n in (('xbt',incoming),('btc',outgoing)):
        token=json.loads(grants[role+'Credential']);assert FixtureSession(n['data'],role).call(token['session_id'],'info','','','')['remaining']==0
    assert len(bridge('reverse-worker')['swaps'])==2
    assert preserved==[p.read_bytes() for p in old_paths]
    print('PASS: two-slot budget exhausted; fresh workers preserve original records; no duplicate mutation after lost replies',flush=True)
    if scenario=='reverse-routed-normal':
        # Reproduce the live report: successful history followed by a user close.
        old=channel(incoming,xbt_router)
        histories={p:p.read_bytes() for n in (incoming,outgoing) for p in (n['data']/'reverse-swaps').glob('*.json')}
        closed=rpc(incoming,'-k','close','id='+old['channel_id'],'unilateraltimeout=0')
        assert closed['type']=='mutual','fixture must close cooperatively'
        # Keep the same non-neighbor topology, with a different funding pin.
        open_channel(xbt,xbt_router,incoming,xbt_nodes,private=True)
        replacement=channel(incoming,xbt_router)
        assert replacement['channel_id']!=old['channel_id']
        assert any(c['channel_id']==old['channel_id'] and c['state']!='CHANNELD_NORMAL' for c in rows(incoming))
        for archived in (False,True):
            if archived:
                mine(xbt,xbt_nodes,110)
                wait_until(lambda:any(c['channel_id']==old['channel_id'] for c in rpc(incoming,'listclosedchannels')['closedchannels']),incoming['proc'],timeout=90)
                assert all(c['channel_id']!=old['channel_id'] for c in rows(incoming))
            new=FixtureSession(incoming['data'],'xbt').enable('',2,True,new_grant=True,routed_grant=True,max_delay=288)
            token=json.loads(new['credential'])
            session=FixtureSession(incoming['data'],'xbt').call(token['session_id'],'info','','','')
            assert [c['channel_id'] for c in session['channels']]==[replacement['channel_id']]
            assert histories=={p:p.read_bytes() for p in histories},'renewal changed original swap evidence'
        old_token=json.loads(grants['xbtCredential'])
        assert FixtureSession(incoming['data'],'xbt').call(old_token['session_id'],'info','','','')['current'] is False
        print('PASS: cooperative close and archived channel permit fresh routed grants; old funding pins and swap journals preserved',flush=True)
