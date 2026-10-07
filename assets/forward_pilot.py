"""One explicitly approved forward pilot, separate from regtest execution.

Remote authority is an immutable node-side contract. This worker never has a
raw sendpay/close/signinvoice rune and never retries an uncertain mutation.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.request
from controller import load_config, private_load, save, require
from executor import digest
import lifecycle
from live_preflight import Inspector, channel, identity
from pilot_contract import PIN, PROFILE, hex32, pin_matches, validate
from read_only_rpc import Client


class Remote:
    def __init__(self,node,rune):self.client=Client(node['url'],rune,ca_data=node['ca_pem'])
    def call(self,method,**params):
        require(method in ('swap-pilot-observe','swap-pilot-step'),'pilot_method_refused')
        require(set(params)==({'pilot_id'} if method=='swap-pilot-observe' else {'pilot_id','operation','preimage'}),'pilot_parameters_refused')
        request=urllib.request.Request(self.client.url+'/v1/'+method,data=json.dumps(params).encode(),
            headers={'Content-Type':'application/json','Rune':self.client.rune},method='POST')
        try:
            with self.client.opener.open(request,timeout=20) as response:raw=response.read(1048577)
            require(len(raw)<=1048576,'pilot_response_unavailable')
            result=json.loads(raw);require(type(result) is dict and 'error' not in result,'pilot_response_unavailable')
            return result
        except Exception:raise ValueError('pilot_rpc_refused_or_uncertain') from None


def directory(root, swap_id=None):
    if swap_id is None: return root/'execution'/'forward-pilot'
    require(hex32(swap_id), 'invalid_swap_id')
    parent=root/'execution'/'forward-swaps'
    require(not parent.is_symlink(), 'invalid_pilot_directory')
    return parent/swap_id

def restore_epoch(root):
    path=root/'execution'/'forward-pilot-restore-epoch.json'
    return digest(private_load(path)) if os.path.lexists(path) else None


def allowed(root,r=None):
    require(not os.path.lexists(root/'execution'/'backup-paused.json'),'backup_in_progress')
    if r is not None:
        require(r.get('restore_epoch')==restore_epoch(root),'restored_pilot_authority_blocked')
    # A fresh contract may be reviewed after restore, but old authority never
    # resumes. Approval needs unused node-side one-contract slots and the new
    # pairing generation. Existing executor/restored.json remains untouched.


def record(root, swap_id=None):
    require(not directory(root, swap_id).is_symlink(),'invalid_pilot_directory')
    r=private_load(directory(root, swap_id)/'record.json');validate(r['contract'])
    require(r['pilot_id']==digest(r['contract']) and (swap_id is None or swap_id==r['pilot_id']),'contract_changed')
    pairing=load_config(root)
    require(pairing and pairing['generation']==r['generation'],'pairing_changed')
    require({k:v['node_id'] for k,v in pairing['nodes'].items()}==r['contract']['nodes'],'node_identity_changed')
    return r,pairing


def reserve(outputs):
    require(type(outputs) is list,'reserve_unavailable')
    amount=0
    for output in outputs:
        require(type(output) is dict and type(output.get('amount_msat')) is int
            and type(output.get('reserved')) is bool,'reserve_unavailable')
        if output.get('status')=='confirmed' and output['reserved'] is False:amount+=output['amount_msat']
    require(amount>=50000000,'confirmed_reserve_insufficient')


def untrimmed(ch,amount):
    dust=ch.get('dust_limit_msat');fee=ch.get('feerate',{}).get('perkw')
    require(type(dust) is int and dust>=0 and type(fee) is int and fee>0
        and amount>dust+((703*fee+999)//1000)*1000,'htlc_trimmed_at_current_fee')


def prepare(root,request,*,factory=Inspector,now=None,repeat=False):
    now=int(time.time()) if now is None else now
    require(set(request)=={'invoice','incomingChannel','outgoingChannel','btcRune','xbtRune'},'invalid_request')
    with lifecycle.locked(root/'execution'):
        allowed(root)
        if repeat:
            from forward_swaps import guard
            guard(root)
        else:
            require(not directory(root).exists(),'one_pilot_only')
        config=load_config(root);require(config,'pair_nodes_first')
        clients={k:factory(config['nodes'][k],request[k+'Rune']) for k in ('btc','xbt')}
        start=time.monotonic()
        for k in clients:identity(clients[k],config['nodes'][k],'bitcoin' if k=='btc' else 'xbt')
        d=clients['xbt'].call('decode',string=request['invoice'])
        require(d.get('valid') is True and d.get('type')=='bolt11 invoice' and d.get('currency')=='xbt'
            and type(d.get('amount_msat')) is int and d['amount_msat']==2000000
            and hex32(d.get('payment_hash')) and hex32(d.get('payment_secret')),'invalid_recipient_invoice')
        require(type(d.get('created_at')) is int and type(d.get('expiry')) is int and d['created_at']<=now
            and d['created_at']+d['expiry']>=now+1980 and type(d.get('min_final_cltv_expiry')) is int
            and 1<=d['min_final_cltv_expiry']<=40,'recipient_invoice_needs_33_minutes')
        selected={}
        for k in clients:
            reserve(clients[k].call('listfunds').get('outputs'))
            selected[k]=channel(clients[k],request['incomingChannel' if k=='btc' else 'outgoingChannel'],1000000 if k=='btc' else 2000000,k=='btc')
            require(clients[k].call('listsendpays',payment_hash=d['payment_hash']).get('payments')==[],'payment_hash_already_used')
        require(selected['xbt']['peer_id']==d['payee'],'direct_recipient_required')
        c=dict(profile=PROFILE,source_commit=PIN,nonce=secrets.token_hex(32),nodes={k:v['node_id'] for k,v in config['nodes'].items()},
            channels={k:{f:selected[k][f] for f in ('channel_id','short_channel_id','funding_txid','funding_outnum','peer_id')} for k in clients},
            invoice=request['invoice'],payment_hash=d['payment_hash'],payment_secret=d['payment_secret'],recipient=d['payee'],created_at=now,admission_until=now+1800)
        validate(c)
        require(0<=time.monotonic()-start<=120 and load_config(root)==config,'inspection_expired_or_pairing_changed')
        target=directory(root,digest(c) if repeat else None)
        if repeat: target.parent.mkdir(mode=0o700,exist_ok=True)
        target.mkdir(mode=0o700)
        r=dict(schema=1,pilot_id=digest(c),contract=c,generation=config['generation'],phase='review',restore_epoch=restore_epoch(root))
        if repeat: r.update(transport='session',gate_profile='startos-fixed-repeat-v1')
        save(target/'record.json',r)
        return dict(pilot_id=r['pilot_id'],contract=c,btc_sats=1000,xbt_sats=2000,payment_started=False,
            approval_required=True,admission_until=c['admission_until'])


def observations(r,clients):
    start=time.monotonic();c=r['contract'];obs={}
    for role in ('btc','xbt'):
        v=clients[role].call('swap-pilot-observe',pilot_id=r['pilot_id'])
        require(v.get('pilot_id')==r['pilot_id'] and v.get('node_id')==c['nodes'][role]
            and v.get('network')==('bitcoin' if role=='btc' else 'xbt') and type(v.get('blockheight')) is int,'pilot_identity_changed')
        require(pin_matches(v.get('channel',{}),c['channels'][role]),'pilot_channel_changed')
        obs[role]=v
    require(0<=time.monotonic()-start<=60,'pilot_observation_expired')
    return obs


def preflight(r,obs,now):
    c=r['contract'];require(c['created_at']<=now<c['admission_until'],'admission_expired')
    d=obs['xbt'].get('decoded',{})
    expected=dict(valid=True,currency='xbt',type='bolt11 invoice',amount_msat=2000000,
        payee=c['recipient'],payment_hash=c['payment_hash'],payment_secret=c['payment_secret'])
    require(all(d.get(k)==v and type(d.get(k)) is type(v) for k,v in expected.items()),'recipient_invoice_changed')
    require(type(d.get('created_at')) is int and type(d.get('expiry')) is int
        and d['created_at']+d['expiry']>=now+180,'recipient_invoice_expiring')
    for role in ('btc','xbt'):
        reserve(obs[role].get('outputs'));ch=obs[role]['channel']
        require(ch.get('state')=='CHANNELD_NORMAL' and ch.get('peer_connected') is True,'channel_not_ready')
        untrimmed(ch,1000000 if role=='btc' else 2000000)
    require(obs['xbt'].get('payments')==[] and obs['xbt']['channel'].get('htlcs')==[]
        and obs['xbt']['channel'].get('spendable_msat',0)>=2000000,'outgoing_not_ready')


def approve(root,request,*,factory=Remote,now=None,swap_id=None):
    require(set(request)=={'pilotId','btcRune','xbtRune','confirmed'} and request['confirmed'] is True,'explicit_approval_required')
    now=int(time.time()) if now is None else now
    with lifecycle.locked(root/'execution'):
        r,config=record(root,swap_id);allowed(root,r)
        require(request['pilotId']==r['pilot_id'],'review_digest_mismatch')
        require(r['phase'] in (('review','authorizing') if swap_id else ('review',)),'already_approved_inspect_status')
        heartbeat=private_load(root/'execution'/'heartbeat.json')
        require(type(heartbeat.get('checked_at')) is int and 0<=now-heartbeat['checked_at']<=30,'worker_heartbeat_required')
        if swap_id:
            save(directory(root,swap_id)/'credentials.json',{k:request[k+'Rune'] for k in ('btc','xbt')})
            r['phase']='authorizing';save(directory(root,swap_id)/'record.json',r)
        clients={k:factory(config['nodes'][k],request[k+'Rune']) for k in ('btc','xbt')}
        obs=observations(r,clients);preflight(r,obs,now)
        gate=obs['btc'].get('gate_profile',{})
        require(gate.get('profile')==r.get('gate_profile','live-pilot-v1')
            and type(gate.get('registered_quotes')) is int and gate['registered_quotes']>=0
            and (swap_id is not None or gate['registered_quotes']==0)
            and obs['btc']['channel'].get('htlcs')==[],'unused_gate_required')
        # Persist authority before publication. No caller-supplied RPC names or CLI.
        save(directory(root,swap_id)/'credentials.json',{k:request[k+'Rune'] for k in clients})
        r['phase']='publishing';save(directory(root,swap_id)/'record.json',r)
        result=clients['btc'].call('swap-pilot-step',pilot_id=r['pilot_id'],operation='publish',preimage='')
        require(type(result.get('invoice')) is str and type(result.get('terms')) is dict,'publication_reply_unknown')
        r.update(phase='waiting_for_btc',invoice=result['invoice'],terms=result['terms']);save(directory(root,swap_id)/'record.json',r)
        return report(r)


def held(r,obs):
    c=r['contract'];btc=obs['btc'];gate=btc.get('gate',{});spend=btc.get('spend',{})
    binding=gate.get('binding')
    require(gate.get('phase')=='held' and gate.get('payment_hash')==c['payment_hash'] and
        type(binding) is list and len(binding)==2 and binding[0]==c['channels']['btc']['short_channel_id']
        and type(binding[1]) is int,'original_held_gate_required')
    expected=dict(payment_hash=c['payment_hash'],binding=binding,btc_amount_msat=1000000,xbt_amount_msat=2000000,
        xbt_invoice=c['invoice'],pilot=r.get('gate_profile','live-pilot-v1'))
    require(all(spend.get(k)==v for k,v in expected.items()) and type(spend.get('cltv_expiry')) is int,'held_terms_changed')
    if 'binding' in r:require(r['binding']==binding and r['expiry']==spend['cltv_expiry'],'held_htlc_changed')
    return binding,spend['cltv_expiry']


def payment(r,obs):
    rows=obs['xbt'].get('payments')
    require(type(rows) is list and len(rows)==1,'original_attempt_required')
    p=dict(rows[0]);p.setdefault('partid',0)
    expected=dict(payment_hash=r['contract']['payment_hash'],groupid=1,partid=0,amount_sent_msat=2000000)
    require(all(p.get(k)==v and type(p.get(k)) is type(v) for k,v in expected.items()),'outgoing_attempt_changed')
    require(p.get('status') in ('pending','failed','complete'),'unknown_outgoing_outcome')
    if p['status']!='complete':require(not p.get('payment_preimage'),'contradictory_outgoing_outcome')
    else:
        import hashlib
        preimage=p.get('payment_preimage')
        require(hex32(preimage) and hashlib.sha256(bytes.fromhex(preimage)).hexdigest()==r['contract']['payment_hash'],'invalid_preimage')
    return p


def report(r):
    return {k:r[k] for k in ('pilot_id','phase','invoice') if k in r} | dict(btc_sats=1000,xbt_sats=2000,
        payment_started=r['phase'] not in ('review','authorizing','cancelled','expired','retire_intent','publishing','waiting_for_btc'),
        close_requested='close_intent' in r,onchain_claim_verified=False,
        quote_expires_at=r.get('terms',{}).get('expires_at'))


def status(root):
    if not directory(root).exists():return dict(phase='not_prepared',live_payment_enabled=False)
    r,_=record(root)
    result=report(r)
    result['restore_blocked']=r.get('restore_epoch')!=restore_epoch(root)
    attention=root/'execution'/'forward-pilot-status.json'
    if attention.exists():result['needs_attention']=private_load(attention).get('needs_attention') is True
    return result


def tick(root,*,factory=Remote,now=None,swap_id=None):
    now=int(time.time()) if now is None else now
    if not directory(root,swap_id).exists():return
    with lifecycle.locked(root/'execution'):
        r,config=record(root,swap_id);allowed(root,r)
        if r['phase']=='review':return report(r)
        creds=private_load(directory(root,swap_id)/'credentials.json')
        clients={k:factory(config['nodes'][k],creds[k]) for k in ('btc','xbt')}
        obs=observations(r,clients);gate=obs['btc'].get('gate',{})
        def persist():save(directory(root,swap_id)/'record.json',r)
        def mutate(role,operation,preimage=''):
            return clients[role].call('swap-pilot-step',pilot_id=r['pilot_id'],operation=operation,preimage=preimage)
        if r['phase']=='publishing':
            require(type(obs['btc'].get('invoice')) is str,'publication_outcome_requires_inspection')
            r.update(phase='waiting_for_btc',invoice=obs['btc']['invoice'],terms=obs['btc']['terms']);persist()
        if r['phase']=='waiting_for_btc':
            if gate.get('phase')=='quoted':return report(r)
            preflight(r,obs,now);binding,expiry=held(r,obs)
            require(type(r.get('terms',{}).get('expires_at')) is int and now<r['terms']['expires_at'],'quote_expired_before_submission')
            require(288<=expiry-obs['btc']['blockheight']<=2016,'incoming_timing_refused')
            ch=obs['btc']['channel'];hs=[h for h in ch.get('htlcs',[]) if h.get('id')==binding[1] and h.get('direction')=='in']
            require(len(hs)==1 and hs[0].get('payment_hash')==r['contract']['payment_hash'] and hs[0].get('amount_msat')==1000000
                and hs[0].get('expiry')==expiry and hs[0].get('state')=='RCVD_ADD_ACK_REVOCATION'
                and hs[0].get('local_trimmed',False) is False,'committed_incoming_htlc_required')
            r.update(phase='send_intent',binding=binding,expiry=expiry);persist()
            mutate('xbt','send')
            return report(r)
        p=payment(r,obs)
        if gate.get('phase') in ('resolved','failed'):
            require(gate.get('binding')==r.get('binding'),'terminal_gate_binding_changed')
            if gate['phase']=='failed':
                require(r['phase'] in ('fail_intent','failed') and p['status']=='failed'
                    and 'close_intent' not in r,'unexpected_terminal_failure')
                require(obs['btc']['channel'].get('htlcs')==[] and obs['xbt']['channel'].get('htlcs')==[],'settlement_still_pending')
                r['phase']='failed'
            else:
                require(r['phase'] in ('release_intent','settled','onchain_recovery') and p['status']=='complete','unexpected_terminal_release')
                r['phase']='onchain_recovery' if 'close_intent' in r else 'settled'
                if r['phase']=='settled':
                    require(obs['btc']['channel'].get('htlcs')==[] and obs['xbt']['channel'].get('htlcs')==[],'settlement_still_pending')
            persist();return report(r)
        held(r,obs)
        if p['status']=='complete':
            import hashlib
            preimage=p.get('payment_preimage')
            require(hex32(preimage) and hashlib.sha256(bytes.fromhex(preimage)).hexdigest()==r['contract']['payment_hash'],'invalid_preimage')
            require(r['phase'] not in ('release_intent','fail_intent'),'resolution_outcome_requires_inspection')
            if 'close_intent' in r and obs['btc']['channel'].get('state')!='ONCHAIN':return report(r)
            r['phase']='release_intent';persist();mutate('btc','release',preimage)
        elif p['status']=='failed':
            require('close_intent' not in r and 'close' not in obs['btc'].get('intents',{}),'post_close_failure_requires_recovery')
            require(r['phase'] not in ('release_intent','fail_intent'),'resolution_outcome_requires_inspection')
            r['phase']='fail_intent';persist();mutate('btc','fail')
        else:
            require(r['phase'] not in ('release_intent','fail_intent','settled','failed'),'outcome_regressed')
            if 'close_intent' in r:return report(r) # Observe only; node owns original close.
            if r['expiry']-obs['btc']['blockheight']<=72:
                # Re-read the original attempt immediately before the close intent.
                latest=observations(r,clients);require(payment(r,latest)['status']=='pending','outgoing_became_terminal')
                held(r,latest);r['close_intent']=True;r['phase']='close_intent';persist();mutate('btc','close')
        return report(r)


def main():
    os.umask(0o077);root=Path(sys.argv[1]);operation=sys.argv[2]
    if operation=='status':result=status(root)
    elif operation=='tick':result=tick(root)
    else:
        raw=sys.stdin.read(262145);require(len(raw)<=262144,'request_too_large');request=json.loads(raw)
        require(operation in ('prepare','approve'),'operation_refused')
        result=(prepare if operation=='prepare' else approve)(root,request)
    print(json.dumps(result))


def safe_error_reason(error):
    # Only reviewed constants can cross the action boundary. Never raw RPC text.
    allowed_reasons = {
        'backup_in_progress', 'restored_pilot_authority_blocked', 'invalid_request',
        'one_pilot_only', 'pair_nodes_first', 'invalid_recipient_invoice',
        'recipient_invoice_needs_33_minutes', 'reserve_unavailable',
        'confirmed_reserve_insufficient', 'payment_hash_already_used',
        'direct_recipient_required', 'inspection_expired_or_pairing_changed',
        'operator_identity_mismatch', 'node_warning_present', 'invalid_block_height',
        'preflight_rpc_unavailable', 'invalid_channels', 'channel_not_unique',
        'channel_not_ready', 'channel_has_pending_htlcs', 'invalid_numeric_observation',
        'channel_liquidity_insufficient', 'amount_trimmed_at_current_fee',
        'invalid_contract', 'invalid_profile', 'invalid_nodes', 'invalid_channel_pin',
        'recipient_channel_mismatch', 'invalid_invoice_binding', 'invalid_invoice',
        'invalid_admission_window', 'invalid_worker_directory', 'invalid_jobs_directory',
    }
    if isinstance(error, BlockingIOError):
        return 'worker_busy_retry_preparation'
    if isinstance(error, PermissionError):
        return 'pilot_storage_permission_denied'
    if isinstance(error, KeyError) and error.args and error.args[0] in {
        'channel_id', 'short_channel_id', 'funding_txid', 'funding_outnum', 'peer_id',
        'node_id', 'generation', 'payee',
    }:
        return 'missing_field_' + error.args[0]
    if isinstance(error, ValueError) and str(error) in allowed_reasons:
        return str(error)
    return 'pilot_refused_or_uncertain'


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps(dict(error='pilot_refused_or_uncertain', reason=safe_error_reason(error))))
        raise SystemExit(1) from None
