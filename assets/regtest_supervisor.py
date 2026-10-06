"""Opt-in quote supervisor for disposable regtest jobs only.

Separate close and claim credentials; optional pre-submission plan, no live authority.
A close intent permanently selects post-close recovery, never ordinary failure.
"""
import fcntl
import os
from pathlib import Path
import time
from controller import private_load,require,save
import executor
import deadline_boundary as deadline
import deadline_recovery as claim


def binding(root,request,*,launched=True):
    require(type(request) is dict and set(request)=={'intent_digest','spec','deadline_connections','claim_connections'},'invalid_supervisor_request')
    spec=request['spec'];deadline.validate(spec)
    initial,state=executor.records(root)
    require(request['intent_digest']==executor.digest(initial),'supervisor_intent_changed')
    incoming='btc' if initial['direction']=='forward' else 'xbt'
    require(spec['direction']==initial['direction'] and spec['payment_hash']==state['payment_hash']
            and state[incoming+'_binding']==[spec['channel']['short_channel_id'],spec['htlc_id']], 'supervisor_binding_changed')
    from quote_workflow import review
    record,data,token=review(root)
    handoff=private_load(root/'handoff.json')
    require(handoff['review_digest']==token and handoff['state']==initial['state']
            and record['connections']==initial['connections'],'supervisor_handoff_changed')
    terms=data['terms']
    require(spec['incoming_amount_msat']==terms[incoming+'_amount_msat']
            and spec['outgoing_amount_msat']==state['route'][0]['amount_msat'],'supervisor_amount_changed')
    for c in initial['connections']:
        role={'regtest':'btc','xbt-regtest':'xbt'}[c['network']]
        require(spec['node_ids'][role]==c['node_id'],'supervisor_identity_changed')
    if launched:require(private_load(root/'launched.json')==dict(digest=executor.digest(initial)),'missing_launch_binding')
    return initial,state


def clients(request,recovery=False):
    spec=request['spec'];incoming='btc' if spec['direction']=='forward' else 'xbt';result={}
    configs=request['claim_connections' if recovery else 'deadline_connections']
    require(type(configs) is list and len(configs)==2,'two_nodes_required')
    for config in configs:
        role={'regtest':'btc','xbt-regtest':'xbt'}.get(config.get('network'))
        require(role is not None and role not in result,'regtest_networks_required')
        if recovery:
            remote=claim.ClaimRemote(config,spec['direction'],spec['payment_hash'],[spec['channel']['short_channel_id'],spec['htlc_id']])
        else:remote=deadline.DeadlineRemote(config,spec['channel']['channel_id'] if role==incoming else None)
        require(remote.node_id==spec['node_ids'][role],'supervisor_identity_changed')
        result[role]=remote
    return result


def check_plan(root,initial):
    plan=private_load(root/'supervisor-plan.json')
    digest=executor.digest(plan)
    require(initial.get('supervision_plan_digest')==digest and
            private_load(root/'supervisor-required.json')==dict(plan_digest=digest),'supervisor_plan_changed')
    require(type(plan) is dict and set(plan)=={'spec','deadline_connections','claim_connections'},'invalid_supervision_plan')
    require(not {'groupid','partid'} & set(plan['spec']),'attempt_not_yet_assigned')
    request=dict(plan,intent_digest=executor.digest(initial),spec=dict(plan['spec'],groupid=0,partid=0))
    binding(root,request,launched=False)
    return request


def arm(root,initial,state,*,modules=Path('/opt/swap'),clock=time.monotonic):
    """Called under the executor/lifecycle lock before its launch marker or child."""
    executor.guard();executor.execution_allowed(root)
    require(not os.path.lexists(root/'restored.json'),'restored_execution_blocked')
    require(state['phase']=='prepared' and not os.path.lexists(root/'launched.json'),'already_launched')
    require((modules/'SOURCE_COMMIT').read_text().strip()==executor.PIN,'source_pin_mismatch')
    request=check_plan(root,initial);spec=request['spec']
    incoming='btc' if spec['direction']=='forward' else 'xbt';outgoing='xbt' if incoming=='btc' else 'btc'
    start=clock()
    close=clients(request);recovery=clients(request,True)
    def call(remote,method,*args):
        require(0<=clock()-start<=120,'supervision_observation_expired')
        result=remote.call(method,*args)
        require(0<=clock()-start<=120,'supervision_observation_expired')
        return result
    for group in (close,recovery):
        for role in ('btc','xbt'):
            info=call(group[role],'getinfo')
            require(info.get('id')==spec['node_ids'][role] and info.get('network')==group[role].network
                    and not any(k.startswith('warning') for k in info),'supervision_identity_unavailable')
    require(call(close[outgoing],'listsendpays',spec['payment_hash']).get('payments')==[],'outgoing_attempt_already_exists')
    rows=call(close[incoming],'listpeerchannels').get('channels',[])
    found=[c for c in rows if c.get('channel_id')==spec['channel']['channel_id']]
    require(len(found)==1 and found[0].get('state')=='CHANNELD_NORMAL' and
            all(found[0].get(k)==v and type(found[0].get(k)) is type(v) for k,v in spec['channel'].items()),'incoming_pin_changed')
    expected=dict(id=spec['htlc_id'],direction='in',payment_hash=spec['payment_hash'],amount_msat=spec['incoming_amount_msat'],
                  expiry=spec['expiry'],state='RCVD_ADD_ACK_REVOCATION')
    h=[h for h in found[0].get('htlcs',[]) if h.get('id')==spec['htlc_id'] and h.get('direction')=='in']
    require(len(h)==1 and all(h[0].get(k)==v and type(h[0].get(k)) is type(v) for k,v in expected.items())
            and h[0].get('local_trimmed',False) is False,'incoming_htlc_changed')
    method='xbt-spend-info' if incoming=='btc' else 'reverse-status'
    gate=call(close[incoming],method,spec['payment_hash'])
    require(gate.get('payment_hash')==spec['payment_hash'] and gate.get('binding')==state[incoming+'_binding']
            and gate.get('cltv_expiry')==spec['expiry'],'gate_binding_changed')
    status=call(close[incoming],'xbt-quote-status' if incoming=='btc' else 'reverse-status',spec['payment_hash'])
    require(status.get('phase')=='held' and status.get('binding')==state[incoming+'_binding'],'incoming_gate_not_held')
    if incoming=='xbt':require(status.get('hook_ready') is True,'incoming_hook_not_ready')
    height=call(close[incoming],'getinfo').get('blockheight')
    require(type(height) is int and spec['expiry']-height>30,'insufficient_supervision_margin')
    receipt=dict(intent_digest=executor.digest(initial),plan_digest=initial['supervision_plan_digest'])
    path=root/'supervisor-armed.json'
    if path.exists():require(private_load(path)==receipt,'supervisor_arming_changed')
    else:save(path,receipt)


def attach_attempt(root,initial,state):
    """After launch, identify the unique original attempt; never submit again."""
    request=check_plan(root,initial)
    require(private_load(root/'supervisor-armed.json')==dict(intent_digest=executor.digest(initial),
            plan_digest=initial['supervision_plan_digest']),'supervision_not_armed')
    require(private_load(root/'launched.json')==dict(digest=executor.digest(initial)),'missing_launch_binding')
    require(state['phase']!='prepared','launch_outcome_unknown')
    if os.path.lexists(root/'supervisor.json'):
        current=private_load(root/'supervisor.json')
        expected=dict(request,spec=dict(request['spec'],groupid=current['spec']['groupid'],partid=current['spec']['partid']))
        require(current==expected,'supervisor_configuration_changed')
        return current
    spec=request['spec'];outgoing='xbt' if spec['direction']=='forward' else 'btc'
    payments=clients(request)[outgoing].call('listsendpays',spec['payment_hash']).get('payments')
    require(type(payments) is list and len(payments)==1,'original_attempt_required')
    payment=dict(payments[0]);payment.setdefault('partid',0)
    require(payment.get('payment_hash')==spec['payment_hash'] and
            type(payment.get('amount_sent_msat')) is int and
            payment['amount_sent_msat']==spec['outgoing_amount_msat'],'outgoing_attempt_changed')
    for key in ('groupid','partid'):
        require(type(payment.get(key)) is int and 0<=payment[key]<2**53,'invalid_attempt_binding')
    request['spec']=dict(spec,groupid=payment['groupid'],partid=payment['partid'])
    binding(root,request)
    save(root/'supervisor.json',request)
    return request


def advance(root,*,modules=Path('/opt/swap'),clock=time.monotonic):
    executor.guard()
    # Intent records retain the requirement even if a plan file disappears.
    initial,state=executor.records(root)
    planned='supervision_plan_digest' in initial
    if not planned and not os.path.lexists(root/'supervisor.json'):return executor.step(root)
    if planned and state['phase']=='prepared':return executor.step(root)
    fd=os.open(root/'supervisor.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with executor.lock(root):
            executor.execution_allowed(root)
            require(not os.path.lexists(root/'restored.json'),'restored_execution_blocked')
            if planned:
                initial,state=executor.records(root)
                request=attach_attempt(root,initial,state)
            else:request=private_load(root/'supervisor.json')
            initial,state=binding(root,request)
            require((modules/'SOURCE_COMMIT').read_text().strip()==executor.PIN,'source_pin_mismatch')
            # This receipt fixes credentials as well as the spec without copying
            # credentials into reports. An edited enrollment is never adopted.
            receipt=dict(request_digest=executor.digest(request))
            path=root/'supervisor-binding.json'
            if path.exists():require(private_load(path)==receipt,'supervisor_configuration_changed')
            else:save(path,receipt)
        spec=request['spec']
        if os.path.lexists(root/'deadline.json'):
            return claim.resolve(root,spec,clients(request,True),modules=modules,clock=clock)
        if state['phase'] in executor.TERMINAL[initial['direction']]:return executor.step(root)
        if state['phase'] in ('btc_paid','xbt_paid','btc_failed','xbt_failed'):
            return executor.step(root,recover_only=True)
        require(state['phase']=='outgoing_started','supervisor_pending_checkpoint_required')
        remotes=clients(request);outgoing='xbt' if spec['direction']=='forward' else 'btc'
        started=clock()
        payments=remotes[outgoing].call('listsendpays',spec['payment_hash']).get('payments')
        require(0<=clock()-started<=120,'supervisor_observation_expired')
        require(type(payments) is list and len(payments)==1 and type(payments[0]) is dict,'original_attempt_required')
        payment=dict(payments[0]);payment.setdefault('partid',0)
        expected={k:spec[k] for k in ('payment_hash','groupid','partid')}
        expected['amount_sent_msat']=spec['outgoing_amount_msat']
        require(all(payment.get(k)==v and type(payment.get(k)) is type(v) for k,v in expected.items()),'outgoing_attempt_changed')
        outcome=payment.get('status')
        require(outcome in ('pending','complete','failed'),'unknown_outgoing_outcome')
        if outcome!='complete':require(not payment.get('payment_preimage'),'contradictory_outgoing_outcome')
        if outcome=='pending':
            # The guard re-observes the attempt, channel, HTLC and deadline under
            # its own lifecycle lock. A racing terminal outcome refuses close.
            return deadline.step(root,spec,remotes,modules=modules,clock=clock)
        # The executor independently revalidates outcome/preimage and gate before
        # release/failure. It cannot run once a deadline close intent exists.
        return executor.step(root,recover_only=True)
    finally:os.close(fd)
