"""Funded packaged HTTPS deadline close and gate recovery; fixture verifies claims.

The pinned setup/submission and settlement CLI are fixture infrastructure.
Deadline decisions, close and preimage/gate recovery run in the isolated image.
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


def bridge(drop=False,recover=False,verify=False,fault=None,absent=False):
    request=Path('/exchange/jobs')/(uuid.uuid4().hex+'.request')
    stage=('drop-release-reply' if drop else 'recover') if recover else ('drop-close-reply' if drop else 'step')
    if verify:
        assert not drop and not recover and fault in (None,'outage','crash');stage='verify'+('-'+fault if fault else '')
    if absent:
        assert not (drop or recover or verify or fault);stage='assert-controller-absent'
    save(request,dict(stage=stage));request.chmod(0o644)
    response=request.with_suffix('.response');end=time.monotonic()+100
    while not response.exists():
        if time.monotonic()>end:raise RuntimeError('deadline_container_timeout')
        time.sleep(.05)
    result=private_load(response)
    assert result['returncode']==(1 if fault=='outage' else 89 if drop or fault=='crash' else 0),'deadline step failed; inspect private logs'
    if absent:
        assert json.loads(result['stdout'])==dict(controller_absent=True)
        return
    return None if drop or fault else private_load(ROOT/'deadline-output.json')


def scoped_connections(lab,incoming_node,outgoing_node,reverse=False,recovery=False,inspection=False,channel_id=None,payment_hash=None):
    connections=[]
    incoming_network='xbt-regtest' if reverse else 'regtest'
    outgoing_network='regtest' if reverse else 'xbt-regtest'
    for node,network in ((incoming_node,incoming_network),(outgoing_node,outgoing_network)):
        methods=['getinfo','listpeerchannels','listsendpays']
        if node is incoming_node:
            methods+=(['reverse-status'] if reverse else ['xbt-quote-status','xbt-spend-info'])
            methods+=[('reverse-release' if reverse else 'xbt-release') if recovery else 'close']
        if inspection:methods=['getinfo']+(['listtransactions','listfunds'] if node is incoming_node else ['listsendpays'])
        rules=[['method='+m for m in methods]]
        if node is incoming_node and not inspection:
            from protection_credentials import restrictions
            if not recovery:rules=restrictions(network,'close',channel_id)
            elif reverse:rules=restrictions(network,'reverse-release',payment_hash)
        token=lab.rpc([*node['cli'],'-k'],'createrune',
                      'restrictions='+json.dumps(rules))
        config={k:v for k,v in lab.connections[node['data'].name].items() if k!='cli'}
        config['rune']=token['rune'];connections.append(config)
        remote=Remote(config)
        if node is incoming_node and not inspection and (not recovery or reverse):
            # Ask CLN's authorization checker, never invoke close/release with
            # deliberately wrong parameters. Positive permission is checked too.
            method='reverse-release' if recovery else 'close'
            good=(dict(payment_hash=payment_hash,binding=['1x1x1',0],preimage='00'*32)
                  if recovery else dict(id=channel_id,unilateraltimeout=1))
            def permitted(params):
                try:
                    reply=lab.rpc([*node['cli'],'-k'],'checkrune','rune='+token['rune'],
                                  'method='+method,'params='+json.dumps(params))
                    return reply.get('valid') is True
                except __import__('subprocess').CalledProcessError:return False
            assert permitted(good),'bound rune refused intended parameter shape'
            wrong=dict(good);key='payment_hash' if recovery else 'id'
            wrong[key]=('00' if good[key][:2]!='00' else '11')+good[key][2:]
            assert not permitted(wrong),'bound rune accepted another target'
            assert not permitted({}),'bound rune accepted missing parameters'
            assert not permitted(dict(good,unexpected=True)),'bound rune accepted extra parameters'
            assert not permitted(list(good.values())),'bound rune accepted positional parameters'
            if not recovery:
                assert not permitted(dict(good,unilateraltimeout=0)),'bound rune accepted changed timeout'
            print('PASS: CLN authorization enforces exact '+method+' target and parameter shape',flush=True)
        forbidden=['sendpay','pay','withdraw','createrune']
        if node is incoming_node:
            forbidden+=([('reverse-fail' if reverse else 'xbt-fail'),'close'] if recovery else
                         (['reverse-release','reverse-fail'] if reverse else ['xbt-release','xbt-fail']))
        else:forbidden+=['close']
        if inspection:forbidden+=['close','xbt-release','xbt-fail','reverse-release','reverse-fail']
        for method in set(forbidden):
            req=urllib.request.Request(config['url']+'/v1/'+method,data=b'{}',
                headers={'Content-Type':'application/json','Rune':config['rune']})
            try:
                with remote.client.opener.open(req,timeout=10):
                    raise AssertionError('deadline rune permitted forbidden method')
            except urllib.error.HTTPError as exc:
                assert exc.code in (401,403);exc.close()
    return connections


def run_bound_deadline(lab,payer,incoming_node,outgoing_node,receiver,incoming_chain,outgoing_chain,invoice,
                 incoming_invoice,binding,plugin,initial,pay_process,pay_log,reverse=False):
    drop=os.environ['DEADLINE_FIXTURE_MODE']=='lost-reply'
    downtime=os.environ.get('DEADLINE_CONTROLLER_DOWNTIME')=='1'
    payment_hash=invoice['payment_hash']
    incoming_name,outgoing_name=('XBT','BTC') if reverse else ('BTC','XBT')
    incoming_role='xbt' if reverse else 'btc'
    outgoing_amount=100000000 if reverse else 200000000
    incoming_amount=200000000 if reverse else 100000000
    def rpc(node,*args):return lab.rpc(node['cli'],*args)
    def channel(node):
        values=rpc(node,'listpeerchannels')['channels'];assert len(values)==1;return values[0]
    def mine(count):
        blocks=rpc(incoming_chain,'generatetoaddress',count,rpc(incoming_chain,'getnewaddress'))
        height=rpc(incoming_chain,'getblockcount')
        for node in (payer,incoming_node):
            wait_until(lambda:rpc(node,'getinfo')['blockheight']>=height,node['proc'],timeout=90)
        return blocks
    def confirmed(node,txid):
        return [o for o in rpc(node,'listfunds')['outputs'] if o['txid']==txid and o['status']=='confirmed']
    deposit=rpc(incoming_chain,'sendtoaddress',rpc(incoming_node,'newaddr','bech32')['bech32'],'0.01')
    mine(1);wait_until(lambda:confirmed(incoming_node,deposit),incoming_node['proc'])
    c=channel(incoming_node)
    incoming=next(h for h in c['htlcs'] if h['payment_hash']==payment_hash)
    funding=dict(txid=c['funding_txid'],outnum=c['funding_outnum'])
    quote_before=plugin.with_suffix('.quotes.json').read_bytes()
    outgoing_height=rpc(outgoing_chain,'getblockcount')
    # Fixture submission only. The deadline container never gets these CLI
    # handles or submission/release runes.
    route=[dict(id=receiver['id'],channel=channel(outgoing_node)['short_channel_id'],amount_msat=outgoing_amount,delay=40)]
    lab.rpc([*outgoing_node['cli'],'-k'],'sendpay','route='+json.dumps(route),
            'payment_hash='+payment_hash,'payment_secret='+invoice['payment_secret'])
    for node,state in ((outgoing_node,'SENT_ADD_ACK_REVOCATION'),(receiver,'RCVD_ADD_ACK_REVOCATION')):
        wait_until(lambda:any(h['payment_hash']==payment_hash and h['state']==state
                   for h in channel(node).get('htlcs',[])),node['proc'])
    attempts=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
    assert len(attempts)==1 and attempts[0]['status']=='pending'
    attempt=attempts[0]
    spec=dict(direction='reverse' if reverse else 'forward',
        node_ids=dict(btc=outgoing_node['id'],xbt=incoming_node['id']) if reverse else dict(btc=incoming_node['id'],xbt=outgoing_node['id']),
        channel={k:c[k] for k in ('channel_id','funding_txid','funding_outnum','peer_id','short_channel_id')},
        htlc_id=incoming['id'],payment_hash=payment_hash,expiry=incoming['expiry'],
        incoming_amount_msat=incoming['amount_msat'],outgoing_amount_msat=attempt['amount_sent_msat'],
        groupid=attempt['groupid'],partid=attempt.get('partid',0))
    assert binding==[spec['channel']['short_channel_id'],spec['htlc_id']]
    assert spec['outgoing_amount_msat']==outgoing_amount and spec['incoming_amount_msat']==incoming_amount
    save(ROOT/'deadline-input.json',dict(spec=spec,connections=scoped_connections(lab,incoming_node,outgoing_node,reverse,channel_id=spec['channel']['channel_id'])))
    (ROOT/'remote.json').unlink()  # Do not expose the fixture's submission runes.
    print('PASS: dedicated deadline runes reject spending and gate resolution; original outgoing attempt pending',flush=True)
    mine(incoming['expiry']-rpc(incoming_chain,'getblockcount')-31)
    result=bridge();assert not result['close_intent_recorded'] and not result['close_reply_recorded']
    assert channel(incoming_node)['state']=='CHANNELD_NORMAL'
    assert not (ROOT/'fixture-close-reply.json').exists()
    assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
    print('PASS: packaged HTTPS deadline guard leaves bound '+incoming_name+' channel open at 31 blocks',flush=True)
    close_margin=27 if downtime else 30
    if downtime:
        # The 31-block one-shot process has exited. The host acknowledges there
        # is no named controller container; no step requests occur while mining.
        bridge(absent=True)
        offline_files={p:p.read_bytes() for p in ROOT.rglob('*') if p.is_file()}
        for blocks,remaining in ((1,30),(3,27)):
            mine(blocks)
            assert incoming['expiry']-rpc(incoming_chain,'getblockcount')==remaining
            assert rpc(outgoing_chain,'getblockcount')==outgoing_height
            current=channel(incoming_node)
            assert current['state']=='CHANNELD_NORMAL'
            assert all(current[k]==value for k,value in spec['channel'].items())
            held=[h for h in current.get('htlcs',[]) if h['id']==spec['htlc_id'] and h['direction']=='in']
            assert len(held)==1 and all(held[0][k]==value for k,value in dict(
                payment_hash=payment_hash,expiry=spec['expiry'],amount_msat=incoming_amount,
                state='RCVD_ADD_ACK_REVOCATION').items())
            payments=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
            assert len(payments)==1 and all(payments[0].get(k)==attempt.get(k) for k in
                ('id','groupid','partid','payment_hash','amount_sent_msat'))
            assert payments[0]['status']=='pending' and not payments[0].get('payment_preimage')
            assert pay_process.poll() is None
            assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
            assert {p:p.read_bytes() for p in ROOT.rglob('*') if p.is_file()}==offline_files
        bridge(absent=True)
        assert not (ROOT/'fixture-close-reply.json').exists()
        print('PASS: controller absent while incoming margin fell from 31 through 30 to 27 blocks; original HTLC and attempt pending; control files and gate journal unchanged',flush=True)
    else:mine(1)
    assert incoming['expiry']-rpc(incoming_chain,'getblockcount')==close_margin
    assert rpc(outgoing_chain,'getblockcount')==outgoing_height
    bridge(drop)
    wait_until(lambda:channel(incoming_node)['state']=='AWAITING_UNILATERAL',incoming_node['proc'])
    journal=ROOT/'deadline-job/deadline.json'
    record=private_load(journal);assert record['spec']==spec
    intent=record['state'][incoming_role+'_close_intent']
    assert (intent['channel'] if reverse else {'channel_id':intent['channel_id']})== (spec['channel'] if reverse else {'channel_id':c['channel_id']})
    assert (incoming_role+'_close_result' in record['state']) is (not drop)
    before=journal.read_bytes()
    def pending():
        result=bridge();assert result['close_intent_recorded'] and not result['onchain_claim_verified']
        assert journal.read_bytes()==before
        for node in (payer,outgoing_node):
            payments=lab.rpc([*node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
            assert len(payments)==1 and payments[0]['status']=='pending' and not payments[0].get('payment_preimage')
        audit=[json.loads(line) for line in (ROOT/'deadline-audit.jsonl').read_text().splitlines()]
        closes=[row for row in audit if row['method']=='close']
        assert len(closes)==1 and closes[0]['network']==('xbt-regtest' if reverse else 'regtest')
        assert not {row['method'] for row in audit}&{'sendpay','xbt-release','xbt-fail','reverse-release','reverse-fail','pay','withdraw'}
        assert plugin.with_suffix('.quotes.json').read_bytes()==quote_before
    pending();pending()
    print('PASS: one original-channel close at '+str(close_margin)+' blocks'+(' after controller downtime' if downtime else '')+'; fresh containers reconcile '+('lost reply' if drop else 'saved reply')+' without a second close',flush=True)
    close=private_load(ROOT/'fixture-close-reply.json');assert close['type']=='unilateral'
    def release():
        pending()
        # Only recipient cooperation and independent on-chain verification stay
        # in the fixture. The packaged resolver must obtain/release the secret.
        save(ROOT/'claim-input.json',dict(spec=spec,
            connections=scoped_connections(lab,incoming_node,outgoing_node,reverse,recovery=True,payment_hash=payment_hash)))
        (ROOT/'deadline-input.json').unlink()  # Recovery containers receive no close rune.
        assert bridge(recover=True)['phase']=='outgoing_pending'
        assert not (ROOT/'deadline-job/claim-receipt.json').exists()
        assert rpc(receiver,'xbt-continue',payment_hash)['continued']==1
        def outgoing_complete():
            payments=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
            return len(payments)==1 and payments[0]['status']=='complete'
        wait_until(outgoing_complete,outgoing_node['proc'])
        bridge(drop,recover=True)
        receipt=ROOT/'deadline-job/claim-receipt.json'
        if drop:assert private_load(receipt)['stage']=='release_intent'
        assert bridge(recover=True)['phase']=='gate_resolved'
        terminal=receipt.read_bytes()
        assert bridge(recover=True)['phase']=='gate_resolved' and receipt.read_bytes()==terminal
        audit=[json.loads(line) for line in (ROOT/'claim-audit.jsonl').read_text().splitlines()]
        releases=[row for row in audit if row['method'] in ('xbt-release','reverse-release')]
        assert releases==[dict(network='xbt-regtest' if reverse else 'regtest',method='reverse-release' if reverse else 'xbt-release')]
        assert not {row['method'] for row in audit}&{'sendpay','close','xbt-fail','reverse-fail','pay','withdraw'}
        assert journal.read_bytes()==before
        completed=rpc(outgoing_node,'waitsendpay',payment_hash,10,attempt.get('partid',0),attempt['groupid'])
        assert completed['status']=='complete' and completed['id']==attempt['id']
        print('PASS: packaged post-close recovery learned the original preimage and resolved the bound gate once; '+
              ('discarded release reply reconciled' if drop else 'terminal recovery repeated safely'),flush=True)
        save(ROOT/'verification-input.json',dict(spec=spec,connections=scoped_connections(
            lab,incoming_node,outgoing_node,reverse,inspection=True)))
        (ROOT/'claim-input.json').unlink()
        assert not bridge(verify=True)['verified']
        print('PASS: read-only packaged verifier reports claim pending before confirmation; write methods denied',flush=True)
        return completed['payment_preimage']
    claim=run_claim(incoming_chain,payer,incoming_node,funding,dict(bolt11=incoming_invoice,payment_hash=payment_hash),None,
                    incoming['expiry'],mine,rpc,confirmed,amount_sat=incoming_amount//1000,standalone=False,release=release,close=close)
    pay_process.wait(timeout=30);assert pay_process.returncode==0
    paid=json.loads(pay_log.read_text())
    assert paid['status']=='complete' and paid['payment_preimage']==claim['payment_preimage']
    for node,delta in ((outgoing_node,-outgoing_amount),(receiver,outgoing_amount)):
        wait_until(lambda:channel(node)['state']=='CHANNELD_NORMAL' and not channel(node).get('htlcs')
                   and channel(node)['to_us_msat']==initial[node['id']]+delta,node['proc'])
    outgoing=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
    assert len(outgoing)==1 and outgoing[0]['id']==attempt['id'] and outgoing[0]['status']=='complete'
    received=rpc(receiver,'listinvoices','reverse-receive' if reverse else 'swap-receive')['invoices'][0]
    assert received['status']=='paid' and received['amount_received_msat']==outgoing_amount
    expected=json.loads(quote_before)[payment_hash];expected.update(phase='resolved',preimage=claim['payment_preimage'])
    assert json.loads(plugin.with_suffix('.quotes.json').read_text())[payment_hash]==expected
    assert not rpc(incoming_node,'xbt-held')['held'] and rpc(outgoing_chain,'getblockcount')==outgoing_height
    assert journal.read_bytes()==before
    terminal=(ROOT/'deadline-job/claim-receipt.json').read_bytes()
    result=bridge(verify=True);assert result['verified']
    assert result['proof']['success']==claim['htlc_success_txid']
    assert result['proof']['sweep']==claim['receiver_sweep_txid']
    observation=(ROOT/'deadline-job/chain-verification.json').read_bytes()
    assert bridge(verify=True)==result and (ROOT/'deadline-job/chain-verification.json').read_bytes()==observation
    assert journal.read_bytes()==before
    assert (ROOT/'deadline-job/claim-receipt.json').read_bytes()==terminal
    print('PASS: fresh read-only verifier linked funding, preimage claim and mature wallet sweep; fixture transaction IDs agree',flush=True)
    releases=[json.loads(line) for line in (ROOT/'claim-audit.jsonl').read_text().splitlines()
              if json.loads(line)['method'] in ('xbt-release','reverse-release')]
    assert len(releases)==1
    # Fixture-only chain administration: the verifier has read-only runes and
    # never receives backend credentials. Disconnect only the sweep block and
    # descendants, keeping the commitment and HTLC-success confirmed.
    protected=[journal,ROOT/'deadline-job/claim-receipt.json',
               ROOT/'deadline-audit.jsonl',ROOT/'claim-audit.jsonl',plugin.with_suffix('.quotes.json')]
    snapshots={path:path.read_bytes() for path in protected}
    for fault,phase in (('outage','verification_unavailable'),('crash','verification_in_progress')):
        bridge(verify=True,fault=fault)
        assert not (ROOT/'deadline-output.json').exists()
        stale=private_load(ROOT/'deadline-job/chain-verification.json')
        assert stale['phase']==phase and stale['verified'] is False and 'proof' not in stale
        assert all(path.read_bytes()==data for path,data in snapshots.items())
        assert bridge(verify=True)==result
        assert all(path.read_bytes()==data for path,data in snapshots.items())
        assert rpc(outgoing_chain,'getblockcount')==outgoing_height
    print('PASS: networkless and abruptly exited verifiers cleared prior success; fresh inspection recovered without close or release',flush=True)
    sweep_height=result['proof']['heights'][2]
    assert sweep_height>result['proof']['heights'][1]
    old_height=rpc(incoming_chain,'getblockcount')
    removed=rpc(incoming_chain,'getblockhash',sweep_height)
    rpc(incoming_chain,'invalidateblock',removed)
    assert rpc(incoming_chain,'getblockcount')==sweep_height-1
    # CLN detects a changed predecessor while fetching tip+1; a shorter
    # backend tip alone leaves it waiting for that next block. Extend an empty
    # replacement branch past the old tip without confirming the sweep.
    replacement_height=old_height+1
    for _ in range(replacement_height-(sweep_height-1)):
        empty=rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),'[]')
        block=rpc(incoming_chain,'getblock',empty['hash'])
        assert len(block['tx'])==1  # coinbase only, regardless of the mempool
    assert rpc(incoming_chain,'getblockcount')==replacement_height
    assert rpc(incoming_chain,'getblockhash',sweep_height)!=removed
    def disconnected():
        if any(rpc(node,'getinfo')['blockheight']!=replacement_height for node in (payer,incoming_node)):return False
        txs=rpc(incoming_node,'listtransactions')['transactions']
        return not any(t['hash']==claim['receiver_sweep_txid'] and t.get('blockheight',0)>0 for t in txs)
    wait_until(disconnected,incoming_node['proc'],timeout=90)
    for _ in range(2):
        pending_result=bridge(verify=True)
        assert pending_result['phase']=='awaiting_csv_sweep' and not pending_result['verified']
        assert 'proof' not in pending_result
        assert private_load(ROOT/'deadline-job/chain-verification.json')==pending_result
    assert all(path.read_bytes()==data for path,data in snapshots.items())
    assert rpc(outgoing_chain,'getblockcount')==outgoing_height
    print('PASS: disconnected sweep confirmation; two fresh read-only verifiers revoked success without close or release',flush=True)
    # Mine a replacement block, not reconsiderblock: require the same wallet
    # sweep to enter a different active block and be observed again by CLN.
    wait_until(lambda:claim['receiver_sweep_txid'] in rpc(incoming_chain,'getrawmempool'),incoming_node['proc'],timeout=90)
    mine(1)
    assert rpc(incoming_chain,'getblockhash',sweep_height)!=removed
    wait_until(lambda:confirmed(incoming_node,claim['receiver_sweep_txid']),incoming_node['proc'],timeout=90)
    recovered=bridge(verify=True)
    assert recovered['verified']
    expected_proof=dict(result['proof'],heights=[*result['proof']['heights'][:2],replacement_height+1])
    assert recovered['proof']==expected_proof
    assert bridge(verify=True)==recovered
    assert all(path.read_bytes()==data for path,data in snapshots.items())
    assert rpc(outgoing_chain,'getblockcount')==outgoing_height
    current=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
    assert len(current)==1 and current[0]['id']==attempt['id'] and current[0]['status']=='complete'
    print('PASS: same sweep confirmed in replacement block; fresh verification recovered; original attempt and mutation journals unchanged',flush=True)
    def rollback_claim(previous,whole=False):
        # Raw transactions stay inside this disposable backend fixture; the
        # packaged verifier must rediscover their links from its read-only RPCs.
        txs={t['hash']:t for t in rpc(incoming_node,'listtransactions')['transactions']}
        success_id=claim['htlc_success_txid'];sweep_id=claim['receiver_sweep_txid']
        success_raw=txs[success_id]['rawtx'];sweep_raw=txs[sweep_id]['rawtx']
        commitment_id=previous['proof']['commitment']
        commitment_raw=txs[commitment_id]['rawtx']
        commitment_height,success_height=previous['proof']['heights'][:2]
        rewind_height=commitment_height if whole else success_height
        old_height=rpc(incoming_chain,'getblockcount')
        assert previous['proof']['heights'][0]<success_height
        # Keep the replacement success within the original quoted claim window.
        assert old_height+2+int(whole)<spec['expiry'],'insufficient_fixture_claim_margin'
        removed_success=rpc(incoming_chain,'getblockhash',rewind_height)
        rpc(incoming_chain,'invalidateblock',removed_success)
        assert rpc(incoming_chain,'getblockcount')==rewind_height-1
        branch_height=old_height+1
        for _ in range(branch_height-(rewind_height-1)):
            empty=rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),'[]')
            assert len(rpc(incoming_chain,'getblock',empty['hash'])['tx'])==1
        def claim_disconnected():
            if any(rpc(node,'getinfo')['blockheight']!=branch_height for node in (payer,incoming_node)):return False
            txs=rpc(incoming_node,'listtransactions')['transactions']
            targets=(commitment_id,success_id,sweep_id) if whole else (success_id,sweep_id)
            return not any(t['hash'] in targets and t.get('blockheight',0)>0 for t in txs)
        wait_until(claim_disconnected,incoming_node['proc'],timeout=90)
        for _ in range(2):
            pending_result=bridge(verify=True)
            assert pending_result['phase']==('awaiting_commitment' if whole else 'awaiting_htlc_success') and not pending_result['verified']
            assert 'proof' not in pending_result and private_load(ROOT/'deadline-job/chain-verification.json')==pending_result
        assert all(path.read_bytes()==data for path,data in snapshots.items())
        if whole:
            print('PASS: commitment and descendants disconnected; two fresh verifiers report awaiting commitment without retained proof',flush=True)
            block=rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),json.dumps([commitment_raw]))
            assert rpc(incoming_chain,'getblock',block['hash'])['tx'][1:]==[commitment_id]
            commitment_height=branch_height+1
            def commitment_reconfirmed():
                if rpc(incoming_node,'getinfo')['blockheight']!=commitment_height:return False
                return any(t['hash']==commitment_id and t.get('blockheight')==commitment_height
                           for t in rpc(incoming_node,'listtransactions')['transactions'])
            wait_until(commitment_reconfirmed,incoming_node['proc'],timeout=90)
            pending_result=bridge(verify=True)
            assert pending_result['phase']=='awaiting_htlc_success' and not pending_result['verified']
        else:
            print('PASS: success and sweep disconnected; fresh verifiers report awaiting HTLC-success; commitment retained',flush=True)
        # Select only the original success, never its CSV-delayed child.
        block=rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),json.dumps([success_raw]))
        assert rpc(incoming_chain,'getblock',block['hash'])['tx'][1:]==[success_id]
        new_success_height=branch_height+1+int(whole)
        def success_reconfirmed():
            if rpc(incoming_node,'getinfo')['blockheight']!=new_success_height:return False
            return any(t['hash']==success_id and t.get('blockheight')==new_success_height
                       for t in rpc(incoming_node,'listtransactions')['transactions'])
        wait_until(success_reconfirmed,incoming_node['proc'],timeout=90)
        assert bridge(verify=True)['phase']=='awaiting_csv_sweep'
        delay=previous['proof']['csv_delay'];assert delay>=2
        # At this tip, the next block is still one block too early for the sweep.
        for _ in range(delay-2):
            rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),'[]')
        immature_height=new_success_height+delay-2
        wait_until(lambda:rpc(incoming_node,'getinfo')['blockheight']==immature_height,incoming_node['proc'],timeout=90)
        acceptance=rpc(incoming_chain,'testmempoolaccept',json.dumps([sweep_raw]))
        assert len(acceptance)==1 and acceptance[0]['allowed'] is False
        assert acceptance[0].get('reject-reason')=='non-BIP68-final',acceptance[0].get('reject-reason')
        assert not bridge(verify=True)['verified']
        rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),'[]')
        block=rpc(incoming_chain,'generateblock',rpc(incoming_chain,'getnewaddress'),json.dumps([sweep_raw]))
        assert rpc(incoming_chain,'getblock',block['hash'])['tx'][1:]==[sweep_id]
        final_height=new_success_height+delay
        wait_until(lambda:rpc(incoming_node,'getinfo')['blockheight']==final_height
                   and confirmed(incoming_node,sweep_id),incoming_node['proc'],timeout=90)
        final=bridge(verify=True)
        expected_proof=dict(previous['proof'],heights=[commitment_height,new_success_height,final_height])
        assert final['verified'] and final['proof']==expected_proof and bridge(verify=True)==final
        assert all(path.read_bytes()==data for path,data in snapshots.items())
        assert rpc(outgoing_chain,'getblockcount')==outgoing_height
        current=lab.rpc([*outgoing_node['cli'],'-k'],'listsendpays','payment_hash='+payment_hash)['payments']
        assert len(current)==1 and current[0]['id']==attempt['id'] and current[0]['status']=='complete'
        print('PASS: original success reconfirmed; backend rejected premature sweep; new CSV delay matured; verification recovered without close or release',flush=True)
        if whole:print('PASS: original commitment, claim and mature sweep reconfirmed; all transaction IDs and mutation journals preserved',flush=True)
        return final
    recovered=rollback_claim(recovered)
    rollback_claim(recovered,whole=True)
    print('PASS: fixture verified confirmed '+incoming_name+' HTLC-success and CSV sweep; original '+outgoing_name+' attempt settled off-chain; '+outgoing_name+' height stayed fixed',flush=True)


def run_deadline(lab,payer,swap_btc,swap_xbt,receiver,btc,xbt,invoice,
                 btc_invoice,binding,plugin,initial,pay_process,pay_log,watch=False):
    assert not watch
    return run_bound_deadline(lab,payer,swap_btc,swap_xbt,receiver,btc,xbt,invoice,
                              btc_invoice,binding,plugin,initial,pay_process,pay_log)


def run_reverse_deadline(lab,payer,incoming,outgoing,receiver,xbt,btc,
                         invoice,decoded,xbt_invoice,route,quote,plugin,
                         initial,paying,pay_log,deadline=False):
    assert deadline
    gate=lab.rpc(incoming['cli'],'reverse-status',invoice['payment_hash'])
    assert gate['terms']==quote and gate['phase']=='held'
    return run_bound_deadline(lab,payer,incoming,outgoing,receiver,xbt,btc,invoice,
                              xbt_invoice,gate['binding'],plugin,initial,paying,pay_log,reverse=True)


def main():
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert os.environ.get('SEPARATE_CONTROLLER')=='1'
    mode=sys.argv[1];assert mode in ('normal','lost-reply','reverse-normal','reverse-lost-reply')
    reverse=mode.startswith('reverse-')
    os.environ['DEADLINE_FIXTURE_MODE']=mode.removeprefix('reverse-')
    os.umask(0o077);check_bundle('/usr/local/libexec/cln-swap')
    source=json.loads(Path('/opt/xbt/share/xbt-cln/source-lock.json').read_text());assert source['cln_commit']==PIN
    os.chown(ROOT,0,0);ROOT.chmod(0o700)
    root=Path(tempfile.mkdtemp(prefix='funded-deadline-',dir='/results'))
    print('Test directory: '+str(root),flush=True)
    # Reuse pinned funding, invoice and holding fixture unchanged.
    import btc_deadline,swap_regtest,reverse_onchain,reverse_regtest
    btc_deadline.run_deadline=run_deadline
    reverse_onchain.exercise_onchain=run_reverse_deadline
    lab=image_remote.RemoteLab(root,'/test-bitcoind','/usr/bin/bitcoin-cli')
    try:
        if reverse:reverse_regtest.run(lab,recovery='gate-deadline')
        else:swap_regtest.run(lab,btc_deadline=True)
    finally:lab.close()
    print('Funded packaged '+('XBT' if reverse else 'BTC')+' deadline OK ('+mode+('; controller downtime' if os.environ.get('DEADLINE_CONTROLLER_DOWNTIME')=='1' else '')+'; packaged gate recovery; fixture-verified claim; regtest only)',flush=True)


if __name__=='__main__':main()
