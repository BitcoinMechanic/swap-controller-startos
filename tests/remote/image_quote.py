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
    return [values[0]+(['method=xbt-register','method=signinvoice'] if network=='regtest' else [])]
image_remote.restrictions=quote_restrictions


def bridge(stage,request=None):
    assert stage in ('prepare','approve','status','worker')
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
    return private_load(root/'quote-output.json')


def run(lab):
    btc=lab.node('knots-btc',False);xbt=lab.node('knots-xbt',True)
    plugin=lab.root/'quote_plugin.py'
    plugin.write_text('#!'+sys.executable+'\n'+Path('/usr/local/libexec/cln-swap/quote_plugin.py').read_text());plugin.chmod(0o700)
    payer=lab.lightning('payer','regtest',btc)
    op_btc=lab.lightning('swap-btc','regtest',btc,plugins=(plugin,))
    op_xbt=lab.lightning('swap-xbt','xbt-regtest',xbt)
    receiver=lab.lightning('receiver','xbt-regtest',xbt)
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
    open_channel(btc,payer,op_btc);open_channel(xbt,op_xbt,receiver)
    initial={n['id']:channel(n)['to_us_msat'] for n in (payer,op_btc,op_xbt,receiver)}
    print('PASS: funded BTC payer -> coordinator and XBT coordinator -> recipient channels',flush=True)
    invoice=rpc(receiver,'invoice','200000000msat','packaged-quote','Packaged quote flow')
    request=dict(connections=list(lab.connections.values()),xbt_invoice=invoice['bolt11'],btc_sats=100000)
    review=bridge('prepare',request)
    assert review['phase']=='review_required' and review['btc_price_sats']==100000 and review['xbt_amount_msat']==200000000
    assert 'btc_invoice' not in review
    assert review==bridge('prepare',request)
    assert rpc(op_btc,'xbt-pilot-info')['registered_quotes']==0
    assert rpc(op_xbt,'listsendpays')['payments']==[]
    assert bridge('worker')['phase']=='review_required'
    print('PASS: packaged quote reviewed; repeat preparation preserved terms; no gate registration or payment before approval',flush=True)
    approved=bridge('approve',dict(digest=review['review_digest'],confirmed=True))
    again=bridge('approve',dict(digest=review['review_digest'],confirmed=True))
    assert approved==again and approved['phase']=='waiting_for_btc'
    signed=rpc(payer,'decode',approved['btc_invoice'])
    assert signed['valid'] and signed['currency']=='bcrt' and signed['payee']==op_btc['id']
    assert signed['payment_hash']==invoice['payment_hash'] and signed['amount_msat']==100000000
    assert rpc(op_btc,'xbt-pilot-info')['registered_quotes']==1
    assert bridge('worker')['phase']=='waiting_for_btc'
    assert rpc(op_xbt,'listsendpays')['payments']==[]
    print('PASS: explicit digest approval published a verified BTC invoice; repeated approval returned the same invoice; worker waited for BTC',flush=True)
    paylog=lab.root/'payer-pay.log'
    payer_process=lab.start([*payer['cli'],'pay',approved['btc_invoice']],paylog)
    def held():
        return rpc(op_btc,'xbt-quote-status',invoice['payment_hash'])['phase']=='held'
    wait_until(held,op_btc['proc'])
    wait_until(lambda:any(h['payment_hash']==invoice['payment_hash'] and h['state']=='RCVD_ADD_ACK_REVOCATION'
                         for h in channel(op_btc).get('htlcs',[])),op_btc['proc'])
    status=bridge('worker')
    end=time.monotonic()+90
    while status['phase']!='btc_released':
        if time.monotonic()>end:raise RuntimeError('packaged_quote_did_not_settle')
        status=bridge('worker')
    payer_process.wait(timeout=30);assert payer_process.returncode==0
    paid=json.loads(paylog.read_text());received=rpc(receiver,'listinvoices','packaged-quote')['invoices'][0]
    assert paid['status']=='complete' and received['status']=='paid' and received['amount_received_msat']==200000000
    assert hashlib.sha256(bytes.fromhex(paid['payment_preimage'])).hexdigest()==invoice['payment_hash']
    for node,delta in ((payer,-100000000),(op_btc,100000000),(op_xbt,-200000000),(receiver,200000000)):
        wait_until(lambda:not channel(node).get('htlcs') and channel(node)['to_us_msat']==initial[node['id']]+delta,node['proc'])
    journal=Path('/exchange/control/execution/jobs/swap')
    before=(journal/'remote-audit.jsonl').read_bytes()
    assert bridge('worker')['phase']=='btc_released'
    assert (journal/'remote-audit.jsonl').read_bytes()==before
    outgoing=[p for p in rpc(op_xbt,'listsendpays')['payments'] if p['payment_hash']==invoice['payment_hash']]
    assert len(outgoing)==1 and outgoing[0]['status']=='complete'
    assert outgoing[0]['payment_preimage']==paid['payment_preimage']
    audit=[json.loads(line) for line in before.splitlines()]
    assert sum(r['method']=='sendpay' for r in audit)==1 and sum(r['method']=='xbt-release' for r in audit)==1
    print('PASS: packaged worker bound the committed BTC HTLC, paid XBT once, and released BTC with the same preimage',flush=True)
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
    try:run(lab)
    finally:lab.close()
    print('Packaged quote -> approval -> BTC invoice -> worker settlement OK (isolated HTTPS; regtest only)',flush=True)


if __name__=='__main__':main()
