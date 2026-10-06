"""Opt-in quote supervisor for disposable regtest jobs only.

Separate close and claim credentials; no automatic enrollment or live authority.
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


def binding(root,request):
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
    require(private_load(root/'launched.json')==dict(digest=executor.digest(initial)),'missing_launch_binding')
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


def advance(root,*,modules=Path('/opt/swap'),clock=time.monotonic):
    executor.guard()
    # Ordinary jobs retain their existing executor path. Enrollment is explicit
    # fixture provisioning, after an original outgoing attempt exists.
    if not os.path.lexists(root/'supervisor.json'):return executor.step(root)
    fd=os.open(root/'supervisor.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with executor.lock(root):
            executor.execution_allowed(root)
            require(not os.path.lexists(root/'restored.json'),'restored_execution_blocked')
            request=private_load(root/'supervisor.json')
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
