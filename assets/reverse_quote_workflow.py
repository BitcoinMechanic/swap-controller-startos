"""Reviewed direct XBT -> BTC quotes using the pinned, fixed regtest policy."""
import copy
import json
import re
import secrets
import time
from controller import private_load, require, save
import executor
import lifecycle
import quote_policy
import quote_workflow as q


class ReverseRemote(q.QuoteRemote):
    publication_network = 'xbt-regtest'


def check_invoice(decoded):
    require(all(decoded.get(k)==v for k,v in dict(valid=True,type='bolt11 invoice',currency='bcrt',
            amount_msat=100000000).items()),'reverse_fixture_invoice_required')
    for key in ('payment_hash','payment_secret'):
        require(isinstance(decoded.get(key),str) and re.fullmatch('[0-9a-f]{64}',decoded[key]),'invalid_invoice_hex')
    require(isinstance(decoded.get('payee'),str) and re.fullmatch('0[23][0-9a-f]{64}',decoded['payee']),'invalid_payee')
    require(type(decoded.get('created_at')) is int and type(decoded.get('expiry')) is int and
            decoded['created_at']<=int(time.time()) and decoded['created_at']+decoded['expiry']>int(time.time())+120,
            'invoice_expired_or_short')
    require(type(decoded.get('min_final_cltv_expiry')) is int and 0<decoded['min_final_cltv_expiry']<=40,'unsupported_cltv')


def preflight(data,rpc):
    observed_at=int(time.time())
    state=data['controller'];terms=data['terms']
    decoded=rpc(state['btc_cli'],'decode',state['btc_invoice']);check_invoice(decoded)
    expected=dict(payment_hash=state['payment_hash'],payment_secret=state['btc_secret'],payee=state['route'][0]['id'])
    require(all(decoded.get(k)==v for k,v in expected.items()),'invoice_binding_changed')
    require(int(time.time())<terms['expires_at']<=decoded['created_at']+decoded['expiry']-60,'quote_expired')
    quote_policy.check(data,decoded,'reverse',observed_at=observed_at,now=int(time.time()))
    route=state['route'][0]
    channels=[c for c in rpc(state['btc_cli'],'listpeerchannels')['channels']
              if c.get('short_channel_id')==route['channel'] and c.get('peer_id')==route['id']]
    require(len(channels)==1 and channels[0].get('state')=='CHANNELD_NORMAL' and
            channels[0].get('peer_connected') is True and channels[0].get('spendable_msat',0)>=100000000
            and not channels[0].get('htlcs'),'direct_channel_not_ready')
    incoming=[c for c in rpc(state['xbt_cli'],'listpeerchannels')['channels']
              if c.get('short_channel_id')==terms['xbt_channel']]
    require(len(incoming)==1 and incoming[0].get('state')=='CHANNELD_NORMAL' and
            incoming[0].get('peer_connected') is True,'incoming_channel_not_ready')
    require(not any(p.get('payment_hash')==terms['payment_hash'] for p in rpc(state['btc_cli'],'listsendpays')['payments']),
            'outgoing_attempt_already_exists')
    quote_policy.check(data,decoded,'reverse',observed_at=observed_at,now=int(time.time()))


def prepare(manager,name,request,core=None,factory=ReverseRemote):
    executor.guard()
    require(isinstance(request,dict) and set(request)=={'connections','btc_invoice','xbt_sats'},'invalid_quote_request')
    invoice=request['btc_invoice']
    require(isinstance(invoice,str) and 0<len(invoice)<=65536 and invoice.startswith('lnbcrt') and
            invoice.isascii() and not any(c.isspace() for c in invoice),'regtest_invoice_required')
    require(type(request['xbt_sats']) is int and request['xbt_sats']==200000,'reverse_fixture_price_required')
    root=q.job(manager,name)
    with lifecycle.locked(manager):
        executor.execution_allowed(root);root.mkdir(mode=0o700,exist_ok=True)
    core=q.service() if core is None else core
    with executor.lock(root):
        executor.execution_allowed(root)
        if (root/'review.json').exists():
            record,_,_=q.review(root)
            require(record.get('direction')=='reverse' and record['request_digest']==executor.digest(request),'existing_review_changed')
            return q.report(root)
        require(not any(p.name!='executor.lock' for p in root.iterdir()),'incomplete_review_requires_inspection')
        config={role+'_cli':next((c['cli'] for c in request['connections'] if c.get('network')==network),None)
                for role,network in (('btc','regtest'),('xbt','xbt-regtest'))}
        with q.transport(core,request['connections'],factory=factory) as rpc:
            decoded=rpc(config['btc_cli'],'decode',invoice);check_invoice(decoded)
            incoming=[c for c in rpc(config['xbt_cli'],'listpeerchannels')['channels']
                      if c.get('state')=='CHANNELD_NORMAL' and c.get('peer_connected') is True and not c.get('htlcs')
                      and c.get('short_channel_id')]
            require(len(incoming)==1,'single_incoming_fixture_channel_required')
            outgoing=[c for c in rpc(config['btc_cli'],'listpeerchannels')['channels']
                      if c.get('peer_id')==decoded['payee'] and c.get('state')=='CHANNELD_NORMAL' and
                      c.get('peer_connected') is True and not c.get('htlcs') and c.get('short_channel_id')
                      and c.get('spendable_msat',0)>=100000000]
            require(len(outgoing)==1,'single_direct_channel_required')
            terms=dict(payment_hash=decoded['payment_hash'],payment_secret=secrets.token_hex(32),
                xbt_amount_msat=200000000,btc_amount_msat=100000000,btc_invoice=invoice,
                xbt_channel=incoming[0]['short_channel_id'],expires_at=min(int(time.time())+900,decoded['created_at']+decoded['expiry']-60),
                min_cltv_delta=100,max_cltv_delta=2000)
            from reverse_gate import validate_terms
            validate_terms(terms)
            state=dict(profile='reverse-regtest-v1',phase='prepared',**config,
                node_ids=[rpc(config[k],'getinfo')['id'] for k in ('xbt_cli','btc_cli')],
                payment_hash=terms['payment_hash'],xbt_amount_msat=200000000,btc_amount_msat=100000000,
                btc_invoice=invoice,btc_secret=decoded['payment_secret'],durable_gate=True,reverse_quote=terms,
                route=[dict(id=decoded['payee'],channel=outgoing[0]['short_channel_id'],amount_msat=100000000,delay=40)])
            data=dict(config=config,terms=terms,controller=state)
            preflight(data,rpc)
            for other in lifecycle.jobs(manager):
                if other==root:continue
                require(not other.is_symlink() and other.is_dir(),'unreadable_existing_job')
                if (other/'review.json').exists():existing=q.review(other)[1]['terms']['payment_hash']
                elif (other/'intent.json').exists():existing=executor.records(other)[1]['payment_hash']
                else:require(False,'unreadable_existing_job')
                require(existing!=terms['payment_hash'],'invoice_already_used')
        (root/'proposal').mkdir(mode=0o700)
        save(root/'proposal'/'quote.json',data)
        save(root/'review.json',dict(schema=1,source_commit=executor.PIN,direction='reverse',request_digest=executor.digest(request),
                                    quote=data,connections=request['connections'],quote_policy=quote_policy.commitment('reverse')))
        return q.report(root)


def approve(manager,name,token,confirmed=False,core=None,factory=ReverseRemote):
    executor.guard();require(confirmed is True,'confirmation_required')
    root=q.job(manager,name);core=q.service() if core is None else core
    with executor.lock(root):
        executor.execution_allowed(root)
        record,data,expected=q.review(root);require(record.get('direction')=='reverse' and token==expected,'review_digest_mismatch')
        quote_policy.check_commitment(record,'reverse')
        terms=data['terms'];approval=dict(digest=token,expires_at=terms['expires_at'])
        if (root/'approval.json').exists():require(private_load(root/'approval.json')==approval,'approval_changed')
        require(int(time.time())<terms['expires_at'],'quote_expired')
        with q.transport(core,record['connections'],writable=True,factory=factory) as rpc:
            if 'xbt_invoice' not in data:
                quote_policy.check_commitment(record,'reverse',required=True)
                preflight(data,rpc);save(root/'approval.json',approval)
                require(rpc(data['config']['xbt_cli'],'reverse-register',json.dumps(terms))=={'registered':True},'registration_refused')
                from swap_invoice import unsigned_invoice
                remaining=terms['expires_at']-int(time.time())-1
                require(remaining>0,'quote_expired')
                unsigned=unsigned_invoice(terms['payment_hash'],terms['payment_secret'],amount_msat=200000000,
                                          expiry=remaining,currency='xbtrt',final_cltv=120)
                invoice=rpc(data['config']['xbt_cli'],'signinvoice',unsigned)['bolt11']
                require(isinstance(invoice,str) and invoice.startswith('lnxbtrt'),'invalid_signed_invoice')
            else:
                require((root/'approval.json').exists(),'approval_missing')
                invoice=data['xbt_invoice']
            decoded=rpc(data['config']['xbt_cli'],'decode',invoice)
            wanted=dict(valid=True,currency='xbtrt',payee=data['controller']['node_ids'][0],
                payment_hash=terms['payment_hash'],payment_secret=terms['payment_secret'],amount_msat=200000000,min_final_cltv_expiry=120)
            require(all(decoded.get(k)==v for k,v in wanted.items()),'signed_invoice_mismatch')
            require(type(decoded.get('created_at')) is int and type(decoded.get('expiry')) is int and
                    int(time.time())<decoded['created_at']+decoded['expiry']<=terms['expires_at'],'signed_expiry_mismatch')
            if 'xbt_invoice' not in data:
                data['xbt_invoice']=invoice;save(root/'proposal'/'quote.json',data)
        return dict(q.report(root),xbt_invoice=data['xbt_invoice'])


def advance(root,core=None,factory=ReverseRemote):
    executor.guard();core=q.service() if core is None else core
    with executor.lock(root):
        executor.execution_allowed(root)
        record,data,token=q.review(root)
        require(record.get('direction')=='reverse','invalid_reverse_review')
        if not (root/'approval.json').exists():return q.report(root)
        require(private_load(root/'approval.json')==dict(digest=token,expires_at=data['terms']['expires_at']),'approval_changed')
        if (root/'intent.json').exists():
            receipt=private_load(root/'handoff.json');initial,_=executor.records(root)
            require(initial['state']==receipt['state'] and initial['connections']==record['connections'] and
                    initial['direction']=='reverse' and receipt['review_digest']==token,'handoff_changed')
        else:
            require('xbt_invoice' in data,'finish_quote_publication')
            with q.transport(core,record['connections'],factory=factory) as rpc:
                terms=data['terms'];state=copy.deepcopy(data['controller'])
                gate=rpc(state['xbt_cli'],'reverse-status',terms['payment_hash'])
                require(gate.get('payment_hash')==terms['payment_hash'] and gate.get('terms')==terms,'held_terms_mismatch')
                if gate['phase']=='quoted':return q.report(root)
                require(gate['phase']=='held' and gate.get('hook_ready') is True,'unexpected_gate_phase')
                binding=gate.get('binding')
                require(isinstance(binding,list) and len(binding)==2 and binding[0]==terms['xbt_channel'] and
                        type(binding[1]) is int and binding[1]>=0 and type(gate.get('cltv_expiry')) is int,'invalid_htlc_binding')
                committed=[h for c in rpc(state['xbt_cli'],'listpeerchannels')['channels']
                    if c.get('short_channel_id')==binding[0] for h in c.get('htlcs',[])
                    if h.get('id')==binding[1] and h.get('direction')=='in' and h.get('payment_hash')==terms['payment_hash']
                    and h.get('state')=='RCVD_ADD_ACK_REVOCATION']
                if not committed:return q.report(root)
                quote_policy.check_commitment(record,'reverse')
                preflight(data,rpc)
                require(len(committed)==1 and committed[0].get('expiry')==gate['cltv_expiry'],'incoming_expiry_mismatch')
                quote_policy.check_held(data,rpc(state['xbt_cli'],'getinfo')['blockheight'],gate['cltv_expiry'])
                state.update(xbt_binding=binding,xbt_expiry=gate['cltv_expiry'])
                from reverse_controller import identity,preflight as controller_preflight
                identity(state);controller_preflight(state)
                receipt=dict(review_digest=token,state=state)
                if (root/'handoff.json').exists():require(private_load(root/'handoff.json')==receipt,'handoff_changed')
                else:save(root/'handoff.json',receipt)
    if not (root/'intent.json').exists():executor.prepare(root,'reverse',receipt['state'],record['connections'])
    initial,state=executor.records(root)
    if state['phase']=='prepared' and not (root/'launched.json').exists():
        executor.authorize(root,executor.digest(initial),data['terms']['expires_at'],confirmed=True)
    from regtest_supervisor import advance as supervise
    return supervise(root)
