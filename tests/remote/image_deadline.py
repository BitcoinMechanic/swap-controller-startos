"""Funded forward close over packaged HTTPS; claim verification stays in fixture.

The pinned setup/submission and settlement CLI are fixture infrastructure.
Only deadline decisions/close/reconciliation execute in the isolated image.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
sys.path.insert(0,'/controller-assets')
from controller import private_load,save
from execution_rpc import Remote
sys.path.insert(0,'/pair-fixtures')
from image_pair import PIN,check_bundle
from smoke_regtest import wait_until
from preimage_claim import run_claim
import image_remote

ROOT=Path('/exchange/control')


def bridge(drop=False):
    request=Path('/exchange/jobs')/(uuid.uuid4().hex+'.request')
    save(request,dict(stage='drop-close-reply' if drop else 'step'));request.chmod(0o644)
    response=request.with_suffix('.response');end=time.monotonic()+100
    while not response.exists():
        if time.monotonic()>end:raise RuntimeError('deadline_container_timeout')
        time.sleep(.05)
    result=private_load(response)
    assert result['returncode']==(89 if drop else 0),'deadline step failed; inspect private logs'
    return None if drop else private_load(ROOT/'deadline-output.json')


def scoped_connections(lab,swap_btc,swap_xbt):
    connections=[]
    for node,network in ((swap_btc,'regtest'),(swap_xbt,'xbt-regtest')):
        methods=['getinfo','listpeerchannels','listsendpays']
        if network=='regtest':methods+=['xbt-quote-status','xbt-spend-info','close']
        token=lab.rpc([*node['cli'],'-k'],'createrune',
                      'restrictions='+json.dumps([['method='+m for m in methods]]))
        config={k:v for k,v in lab.connections[node['data'].name].items() if k!='cli'}
        config['rune']=token['rune'];connections.append(config)
        remote=Remote(config)
        forbidden=['sendpay','pay','withdraw','createrune']
        if network=='regtest':forbidden+=['xbt-release','xbt-fail']
        for method in forbidden:
            req=urllib.request.Request(config['url']+'/v1/'+method,data=b'{}',
                headers={'Content-Type':'application/json','Rune':config['rune']})
            try:
                with remote.client.opener.open(req,timeout=10):
                    raise AssertionError('deadline rune permitted forbidden method')
            except urllib.error.HTTPError as exc:
                assert exc.code in (401,403);exc.close()
    return connections


def run_deadline(lab,payer,swap_btc,swap_xbt,receiver,btc,xbt,invoice,
                 btc_invoice,binding,plugin,initial,pay_process,pay_log,watch=False):
    assert not watch
    drop=os.environ['DEADLINE_FIXTURE_MODE']=='lost-reply'
    payment_hash=invoice['payment_hash']
    def rpc(node,*args):return lab.rpc(node['cli'],*args)
    def channel(node):
        values=rpc(node,'listpeerchannels')['channels'];assert len(values)==1;return values[0]
    def mine(count):
        blocks=rpc(btc,'generatetoaddress',count,rpc(btc,'getnewaddress'))
        height=rpc(btc,'getblockcount')
        for node in (payer,swap_btc):
            wait_until(lambda:rpc(node,'getinfo')['blockheight']>=height,node['proc'],timeout=90)
        return blocks
    def confirmed(node,txid):
        return [o for o in rpc(node,'listfunds')['outputs'] if o['txid']==txid and o['status']=='confirmed']
    deposit=rpc(btc,'sendtoaddress',rpc(swap_btc,'newaddr','bech32')['bech32'],'0.01')
    mine(1);wait_until(lambda:confirmed(swap_btc,deposit),swap_btc['proc'])
    c=channel(swap_btc)
    incoming=next(h for h in c['htlcs'] if h['payment_hash']==payment_hash)
    funding=dict(txid=c['funding_txid'],outnum=c['funding_outnum'])
    quote_before=plugin.with_suffix('.quotes.json').read_bytes()
    xbt_height=rpc(xbt,'getblockcount')
    # Fixture submission only. The deadline container never gets these CLI
    # handles or submission/release runes.
    route=[dict(id=receiver['id'],channel=channel(swap_xbt)['short_channel_id'],amount_msat=200000000,delay=40)]
    lab.rpc([*swap_xbt['cli'],'-k'],'sendpay','route='+json.dumps(route),
            'payment_hash='+payment_hash,'payment_secret='+invoice['payment_secret'])
    for node,state in ((swap_xbt,'SENT_ADD_ACK_REVOCATION'),(receiver,'RCVD_ADD_ACK_REVOCATION')):
        wait_until(lambda:any(h['payment_hash']==payment_hash and h['state']==state
                   for h in channel(node).get('htlcs',[])),node['proc'])
    attempts=lab.rpc([*swap_xbt['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
    assert len(attempts)==1 and attempts[0]['status']=='pending'
    attempt=attempts[0]
    spec=dict(direction='forward',node_ids=dict(btc=swap_btc['id'],xbt=swap_xbt['id']),
        channel={k:c[k] for k in ('channel_id','funding_txid','funding_outnum','peer_id','short_channel_id')},
        htlc_id=incoming['id'],payment_hash=payment_hash,expiry=incoming['expiry'],
        incoming_amount_msat=incoming['amount_msat'],outgoing_amount_msat=attempt['amount_sent_msat'],
        groupid=attempt['groupid'],partid=attempt.get('partid',0))
    assert binding==[spec['channel']['short_channel_id'],spec['htlc_id']]
    assert spec['outgoing_amount_msat']==200000000
    save(ROOT/'deadline-input.json',dict(spec=spec,connections=scoped_connections(lab,swap_btc,swap_xbt)))
    (ROOT/'remote.json').unlink()  # Do not expose the fixture's submission runes.
    print('PASS: dedicated deadline runes reject spending and gate resolution; original outgoing attempt pending',flush=True)
    mine(incoming['expiry']-rpc(btc,'getblockcount')-31)
    result=bridge();assert not result['close_intent_recorded'] and not result['close_reply_recorded']
    assert channel(swap_btc)['state']=='CHANNELD_NORMAL'
    assert not (ROOT/'fixture-close-reply.json').exists()
    assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
    print('PASS: packaged HTTPS deadline guard leaves bound BTC channel open at 31 blocks',flush=True)
    mine(1);assert incoming['expiry']-rpc(btc,'getblockcount')==30
    assert rpc(xbt,'getblockcount')==xbt_height
    bridge(drop)
    wait_until(lambda:channel(swap_btc)['state']=='AWAITING_UNILATERAL',swap_btc['proc'])
    journal=ROOT/'deadline-job/deadline.json'
    record=private_load(journal);assert record['spec']==spec
    assert record['state']['btc_close_intent']['channel_id']==c['channel_id']
    assert ('btc_close_result' in record['state']) is (not drop)
    before=journal.read_bytes()
    def pending():
        result=bridge();assert result['close_intent_recorded'] and not result['onchain_claim_verified']
        assert journal.read_bytes()==before
        for node in (payer,swap_xbt):
            payments=lab.rpc([*node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
            assert len(payments)==1 and payments[0]['status']=='pending' and not payments[0].get('payment_preimage')
        audit=[json.loads(line) for line in (ROOT/'deadline-audit.jsonl').read_text().splitlines()]
        assert sum(row['method']=='close' for row in audit)==1
        assert not {row['method'] for row in audit}&{'sendpay','xbt-release','xbt-fail','pay','withdraw'}
        assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
    pending();pending()
    print('PASS: one original-channel close at 30 blocks; fresh containers reconcile '+('lost reply' if drop else 'saved reply')+' without a second close',flush=True)
    close=private_load(ROOT/'fixture-close-reply.json');assert close['type']=='unilateral'
    def release():
        pending()
        # Fixture-only settlement after the on-chain verifier confirms the
        # original unresolved commitment. Packaged claims remain future work.
        assert rpc(receiver,'xbt-continue',payment_hash)['continued']==1
        completed=rpc(swap_xbt,'waitsendpay',payment_hash,10,attempt.get('partid',0),attempt['groupid'])
        assert completed['status']=='complete' and completed['id']==attempt['id']
        assert rpc(swap_btc,'xbt-release',completed['payment_preimage'])['released']==1
        return completed['payment_preimage']
    claim=run_claim(btc,payer,swap_btc,funding,dict(bolt11=btc_invoice,payment_hash=payment_hash),None,
                    incoming['expiry'],mine,rpc,confirmed,standalone=False,release=release,close=close)
    pay_process.wait(timeout=30);assert pay_process.returncode==0
    paid=json.loads(pay_log.read_text())
    assert paid['status']=='complete' and paid['payment_preimage']==claim['payment_preimage']
    for node,delta in ((swap_xbt,-200000000),(receiver,200000000)):
        wait_until(lambda:channel(node)['state']=='CHANNELD_NORMAL' and not channel(node).get('htlcs')
                   and channel(node)['to_us_msat']==initial[node['id']]+delta,node['proc'])
    outgoing=lab.rpc([*swap_xbt['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
    assert len(outgoing)==1 and outgoing[0]['id']==attempt['id'] and outgoing[0]['status']=='complete'
    received=rpc(receiver,'listinvoices','swap-receive')['invoices'][0]
    assert received['status']=='paid' and received['amount_received_msat']==200000000
    expected=json.loads(quote_before)[payment_hash];expected.update(phase='resolved',preimage=claim['payment_preimage'])
    assert json.loads(plugin.with_suffix('.quotes.json').read_text())[payment_hash]==expected
    assert not rpc(swap_btc,'xbt-held')['held'] and rpc(xbt,'getblockcount')==xbt_height
    assert journal.read_bytes()==before
    print('PASS: fixture verified confirmed BTC HTLC-success and CSV sweep; original XBT attempt settled off-chain; XBT height stayed fixed',flush=True)


def main():
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert os.environ.get('SEPARATE_CONTROLLER')=='1'
    mode=sys.argv[1];assert mode in ('normal','lost-reply');os.environ['DEADLINE_FIXTURE_MODE']=mode
    os.umask(0o077);check_bundle('/usr/local/libexec/cln-swap')
    source=json.loads(Path('/opt/xbt/share/xbt-cln/source-lock.json').read_text());assert source['cln_commit']==PIN
    os.chown(ROOT,0,0);ROOT.chmod(0o700)
    root=Path(tempfile.mkdtemp(prefix='funded-deadline-',dir='/results'))
    print('Test directory: '+str(root),flush=True)
    # Reuse pinned funding, invoice and holding fixture unchanged.
    import btc_deadline,swap_regtest
    btc_deadline.run_deadline=run_deadline
    lab=image_remote.RemoteLab(root,'/test-bitcoind','/usr/bin/bitcoin-cli')
    try:swap_regtest.run(lab,btc_deadline=True)
    finally:lab.close()
    print('Funded packaged BTC deadline OK ('+mode+'; fixture-assisted claim; regtest only)',flush=True)


if __name__=='__main__':main()
