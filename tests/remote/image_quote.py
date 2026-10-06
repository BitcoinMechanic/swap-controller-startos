"""Fund nodes; let the packaged controller create/review/publish/bind the swap."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
sys.path.insert(0,'/controller-assets')
from controller import save,private_load
sys.path.insert(0,'/pair-fixtures')
from image_pair import PIN,check_bundle
from smoke_regtest import wait_until
import image_remote

BASE_RESTRICTIONS=image_remote.restrictions
# Only this disposable fixture creates a quote-publication credential. The
# installed node packages and their read-only credentials are unchanged.
def quote_restrictions(network):
    values=BASE_RESTRICTIONS(network)
    return [values[0]+(['method=xbt-register','method=signinvoice'] if network=='regtest' else ['method=reverse-register','method=signinvoice'])]
image_remote.restrictions=quote_restrictions


def bridge(stage,request=None):
    assert stage in ('prepare','approve','status','review','worker','worker-interrupt-resolution','assert-controller-absent')
    root=Path('/exchange/control')
    if request is not None:save(root/'quote-input.json',request)
    mailbox=Path('/exchange/jobs')/(uuid.uuid4().hex+'.request')
    save(mailbox,dict(stage=stage));mailbox.chmod(0o644)
    response=mailbox.with_suffix('.response')
    end=time.monotonic()+100
    while not response.exists():
        if time.monotonic()>end:raise RuntimeError('quote_container_timeout')
        time.sleep(.05)
    result=private_load(response)
    assert result['returncode']==0, 'packaged quote step failed; inspect private step logs'
    if stage=='assert-controller-absent':
        assert json.loads(result['stdout'])==dict(controller_absent=True)
        return
    return private_load(root/'quote-output.json')


def check_outcome(original,current,failure):
    assert current['status']==('failed' if failure else 'complete')
    assert all(current.get(k)==original.get(k) for k in ('id','groupid','partid','payment_hash','amount_sent_msat'))
    if failure:assert not current.get('payment_preimage')
    else:assert hashlib.sha256(bytes.fromhex(current['payment_preimage'])).hexdigest()==current['payment_hash']


def run(lab,reverse=False):
    downtime=os.environ.get('QUOTE_SETTLED_DOWNTIME')=='1'
    interrupt=os.environ.get('QUOTE_INTERRUPT_RESOLUTION')=='1'
    failure=os.environ.get('QUOTE_FAILED_DOWNTIME')=='1'
    assert not failure or (downtime and not interrupt)
    assert not interrupt or downtime
    btc=lab.node('knots-btc',False);xbt=lab.node('knots-xbt',True)
    plugin=lab.root/('reverse_gate.py' if reverse else 'quote_plugin.py')
    source=Path('/usr/local/libexec/cln-swap')
    plugin.write_text('#!'+sys.executable+'\n'+(source/plugin.name).read_text());plugin.chmod(0o700)
    if reverse:(lab.root/'quote_plugin.py').write_text((source/'quote_plugin.py').read_text())
    payer=lab.lightning('payer','xbt-regtest' if reverse else 'regtest',xbt if reverse else btc)
    op_btc=lab.lightning('swap-btc','regtest',btc,plugins=() if reverse else (plugin,))
    op_xbt=lab.lightning('swap-xbt','xbt-regtest',xbt,plugins=(plugin,) if reverse else ())
    hold=lab.root/'hold_htlc.py'
    if downtime:
        hold.write_text('#!'+sys.executable+'\n'+(source/'hold_htlc.py').read_text());hold.chmod(0o700)
    receiver=lab.lightning('receiver','regtest' if reverse else 'xbt-regtest',btc if reverse else xbt,plugins=(hold,) if downtime else ())
    incoming,outgoing=(op_xbt,op_btc) if reverse else (op_btc,op_xbt)
    price,amount=(200000000,100000000) if reverse else (100000000,200000000)
    invoice_key='xbt_invoice' if reverse else 'btc_invoice'
    waiting,terminal=('waiting_for_xbt','xbt_released') if reverse else ('waiting_for_btc','btc_released')
    if failure:terminal='xbt_failed' if reverse else 'btc_failed'
    status_method='reverse-status' if reverse else 'xbt-quote-status'
    def rpc(node,*args):return lab.rpc(node['cli'],*args)
    def channel(node):
        channels=rpc(node,'listpeerchannels')['channels'];assert len(channels)==1;return channels[0]
    def mine(backend,nodes,count):
        rpc(backend,'generatetoaddress',count,rpc(backend,'getnewaddress'))
        height=rpc(backend,'getblockcount')
        for node in nodes:wait_until(lambda:rpc(node,'getinfo')['blockheight']>=height,node['proc'],timeout=90)
    def open_channel(backend,sender,recipient):
        mine(backend,(sender,recipient),1)
        txid=rpc(backend,'sendtoaddress',rpc(sender,'newaddr','bech32')['bech32'],'0.02')
        mine(backend,(sender,recipient),1)
        wait_until(lambda:any(o['txid']==txid and o['status']=='confirmed' for o in rpc(sender,'listfunds')['outputs']),sender['proc'])
        rpc(sender,'connect',recipient['id'],'127.0.0.1',recipient['port'])
        funding=rpc(sender,'fundchannel',recipient['id'],'1000000sat')
        wait_until(lambda:funding['txid'] in rpc(backend,'getrawmempool'))
        mine(backend,(sender,recipient),6)
        for node in (sender,recipient):wait_until(lambda:channel(node)['state']=='CHANNELD_NORMAL',node['proc'])
    open_channel(xbt if reverse else btc,payer,incoming);open_channel(btc if reverse else xbt,outgoing,receiver)
    initial={n['id']:channel(n)['to_us_msat'] for n in (payer,op_btc,op_xbt,receiver)}
    print('PASS: funded '+('XBT payer -> coordinator and BTC coordinator -> recipient channels' if reverse else 'BTC payer -> coordinator and XBT coordinator -> recipient channels'),flush=True)
    invoice=rpc(receiver,'invoice',str(amount)+'msat','packaged-quote','Packaged quote flow')
    request=dict(connections=list(lab.connections.values()),**(dict(btc_invoice=invoice['bolt11'],xbt_sats=200000) if reverse else dict(xbt_invoice=invoice['bolt11'],btc_sats=100000)))
    assert bridge('status')['quotes']==[]
    review=bridge('prepare',request)
    assert review['phase']=='review_required' and review['xbt_price_sats' if reverse else 'btc_price_sats']==price//1000 and review['btc_amount_msat' if reverse else 'xbt_amount_msat']==amount
    assert review['quote_policy_version']=='regtest-quote-admission-v1'
    assert len(review['quote_policy_digest'])==64
    assert invoice_key not in review
    assert review==bridge('prepare',request)
    saved=bridge('review');assert saved['review_digest']==review['review_digest']
    assert saved['recipient']==receiver['id'] and saved['xbt_price_sats' if reverse else 'btc_price_sats']==price//1000
    summaries=bridge('status')['quotes'];assert len(summaries)==1 and summaries[0]['state']=='recorded'
    assert summaries[0]['review_digest']==review['review_digest']
    if reverse:
        assert not (plugin.with_suffix('.quotes.json')).exists() or json.loads((plugin.with_suffix('.quotes.json')).read_text())=={}
    else:assert rpc(op_btc,'xbt-pilot-info')['registered_quotes']==0
    assert rpc(outgoing,'listsendpays')['payments']==[]
    assert bridge('worker')['phase']=='review_required'
    print('PASS: packaged action bound the quote to admission policy; repeat preparation preserved terms; no gate registration or payment before approval',flush=True)
    approved=bridge('approve',dict(digest=review['review_digest'],confirmed=True))
    again=bridge('approve',dict(digest=review['review_digest'],confirmed=True))
    assert approved==again and approved['phase']==waiting
    signed=rpc(payer,'decode',approved[invoice_key])
    assert signed['valid'] and signed['currency']==('xbtrt' if reverse else 'bcrt') and signed['payee']==incoming['id']
    assert signed['payment_hash']==invoice['payment_hash'] and signed['amount_msat']==price
    if reverse:assert rpc(incoming,'reverse-status',invoice['payment_hash'])['phase']=='quoted'
    else:assert rpc(op_btc,'xbt-pilot-info')['registered_quotes']==1
    assert bridge('worker')['phase']==waiting
    assert rpc(outgoing,'listsendpays')['payments']==[]
    print('PASS: explicit action approval published a verified incoming invoice; repeated approval returned the same invoice; worker waited for payment',flush=True)
    paylog=lab.root/'payer-pay.log'
    payer_process=lab.start([*payer['cli'],'pay',approved[invoice_key]],paylog)
    def held():
        return rpc(incoming,status_method,invoice['payment_hash'])['phase']=='held'
    wait_until(held,incoming['proc'])
    wait_until(lambda:any(h['payment_hash']==invoice['payment_hash'] and h['state']=='RCVD_ADD_ACK_REVOCATION'
                         for h in channel(incoming).get('htlcs',[])),incoming['proc'])
    status=bridge('worker')
    if downtime:
        assert status['phase']=='outgoing_started'
        payment_hash=invoice['payment_hash']
        def attempts():return [p for p in rpc(outgoing,'listsendpays')['payments'] if p['payment_hash']==payment_hash]
        wait_until(lambda:any(h['payment_hash']==payment_hash and h['state']=='SENT_ADD_ACK_REVOCATION'
                             for h in channel(outgoing).get('htlcs',[])),outgoing['proc'])
        original=attempts();assert len(original)==1 and original[0]['status']=='pending'
        original=original[0]
        pin={k:channel(incoming)[k] for k in ('channel_id','funding_txid','funding_outnum','short_channel_id','peer_id')}
        htlcs=[h for h in channel(incoming)['htlcs'] if h['payment_hash']==payment_hash and h['direction']=='in']
        assert len(htlcs)==1;bound=htlcs[0]
        control=Path('/exchange/control');manager=control/'execution';job=manager/'jobs/swap'
        assert 'preimage' not in private_load(job/'state.json')
        bridge('assert-controller-absent')
        saved={p:p.read_bytes() for p in control.rglob('*') if p.is_file()}
        quote_before=plugin.with_suffix('.quotes.json').read_bytes()
        incoming_chain,outgoing_chain=(xbt,btc) if reverse else (btc,xbt)
        outgoing_height=rpc(outgoing_chain,'getblockcount')
        count=bound['expiry']-rpc(incoming_chain,'getblockcount')-27
        assert count>0
        mine(incoming_chain,(payer,incoming),count)
        assert bound['expiry']-rpc(incoming_chain,'getblockcount')==27
        assert channel(incoming)['state']=='CHANNELD_NORMAL'
        assert rpc(receiver,'xbt-fail' if failure else 'xbt-continue',payment_hash)['failed' if failure else 'continued']==1
        wait_until(lambda:len(attempts())==1 and attempts()[0]['status']==('failed' if failure else 'complete'),outgoing['proc'])
        complete=attempts()[0]
        check_outcome(original,complete,failure)
        assert payer_process.poll() is None and held()
        current=channel(incoming)
        assert current['state']=='CHANNELD_NORMAL' and all(current[k]==v for k,v in pin.items())
        same=[h for h in current['htlcs'] if h['id']==bound['id'] and h['direction']=='in']
        assert len(same)==1 and all(same[0][k]==bound[k] for k in ('payment_hash','expiry','amount_msat','state'))
        assert rpc(outgoing_chain,'getblockcount')==outgoing_height
        assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
        assert saved=={p:p.read_bytes() for p in control.rglob('*') if p.is_file()}
        bridge('assert-controller-absent')
        print('PASS: original outgoing attempt '+('failed' if failure else 'settled')+' with controller absent at 27 incoming blocks remaining; incoming HTLC stayed held; controller files and gate journal unchanged',flush=True)
        if interrupt:
            interrupted=bridge('worker-interrupt-resolution')
            assert interrupted['phase']==('btc_paid' if reverse else 'xbt_paid')
            assert private_load(manager/'resolution-interrupted.json')['returncode']==(89 if reverse else 87)
            assert rpc(incoming,status_method,payment_hash)['phase']=='resolved'
        status=bridge('worker')
        assert status['phase']==terminal
        assert channel(incoming)['state']=='CHANNELD_NORMAL'
        assert all(channel(incoming)[k]==v for k,v in pin.items())
        assert attempts()[0]['id']==original['id'] and len(attempts())==1
        print('PASS: fresh packaged worker '+('failed bound incoming gate' if failure else 'recovered original preimage and resolved incoming gate')+' without channel close'+('; interrupted release checkpoint reconciled' if interrupt else ''),flush=True)
    end=time.monotonic()+90
    while status['phase']!=terminal:
        if time.monotonic()>end:raise RuntimeError('packaged_quote_did_not_settle')
        status=bridge('worker')
    payer_process.wait(timeout=30)
    received=rpc(receiver,'listinvoices','packaged-quote')['invoices'][0]
    if failure:
        assert payer_process.returncode!=0 and received['status']=='unpaid'
        assert rpc(incoming,status_method,invoice['payment_hash'])['phase']=='failed'
        assert 'preimage' not in private_load(Path('/exchange/control/execution/jobs/swap/state.json'))
    else:
        assert payer_process.returncode==0
        paid=json.loads(paylog.read_text())
        assert paid['status']=='complete' and received['status']=='paid' and received['amount_received_msat']==amount
        assert hashlib.sha256(bytes.fromhex(paid['payment_preimage'])).hexdigest()==invoice['payment_hash']
    for node,delta in ((payer,-price),(incoming,price),(outgoing,-amount),(receiver,amount)):
        if failure:delta=0
        wait_until(lambda:channel(node)['state']=='CHANNELD_NORMAL' and not channel(node).get('htlcs') and channel(node)['to_us_msat']==initial[node['id']]+delta,node['proc'])
    journal=Path('/exchange/control/execution/jobs/swap')
    before=(journal/'remote-audit.jsonl').read_bytes()
    assert bridge('worker')['phase']==terminal
    assert (journal/'remote-audit.jsonl').read_bytes()==before
    outgoing=[p for p in rpc(outgoing,'listsendpays')['payments'] if p['payment_hash']==invoice['payment_hash']]
    assert len(outgoing)==1 and outgoing[0]['status']==('failed' if failure else 'complete')
    if failure:assert not outgoing[0].get('payment_preimage')
    else:assert outgoing[0]['payment_preimage']==paid['payment_preimage']
    audit=[json.loads(line) for line in before.splitlines()]
    resolution=('reverse-' if reverse else 'xbt-')+('fail' if failure else 'release')
    forbidden={'close','xbt-fail','reverse-fail','xbt-release','reverse-release'}-{resolution}
    assert not {r['method'] for r in audit}&forbidden
    assert sum(r['method']=='sendpay' for r in audit)==1 and sum(r['method']==resolution for r in audit)==1
    print('PASS: one original outgoing attempt and one bound incoming '+('failure; all four balances restored' if failure else 'release with the same preimage'),flush=True)
    print('PASS: all four balances verified; no pending HTLCs; a fresh worker repeated terminal status without another mutation',flush=True)


def main():
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert os.environ.get('SEPARATE_CONTROLLER')=='1'
    os.umask(0o077)
    check_bundle('/usr/local/libexec/cln-swap')
    source=json.loads(Path('/opt/xbt/share/xbt-cln/source-lock.json').read_text());assert source['cln_commit']==PIN
    os.chown('/exchange/control',0,0);Path('/exchange/control').chmod(0o700)
    root=Path(tempfile.mkdtemp(prefix='quote-flow-',dir='/results'))
    print('Test directory: '+str(root),flush=True)
    lab=image_remote.RemoteLab(root,'/test-bitcoind','/usr/bin/bitcoin-cli')
    direction=sys.argv[1] if len(sys.argv)>1 else 'forward'
    assert direction in ('forward','reverse')
    try:run(lab,direction=='reverse')
    finally:lab.close()
    print('Packaged StartOS '+direction+' quote -> approval -> invoice -> worker settlement OK ('+('failed during downtime; ' if os.environ.get('QUOTE_FAILED_DOWNTIME')=='1' else 'settled during downtime; ' if os.environ.get('QUOTE_SETTLED_DOWNTIME')=='1' else '')+'isolated HTTPS; regtest only)',flush=True)


if __name__=='__main__':main()
