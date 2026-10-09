"""Repeat reverse swaps with a bounded grant and exact per-swap confirmation."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import reverse_pilot as pilot
import swap_setup
import lifecycle
from controller import load_config, private_load, save, require
from reverse_contract import canonical, hex32, routed, sent_amount, GRANT_LIMITS, route_limit, PLAN_ERRORS
from read_only_rpc import Client

FILE='reverse-session-pairing.json'
TERMINAL=('settled','failed','cancelled','expired')


class SessionRemote:
    def __init__(self,node,credential):
        token=json.loads(credential)
        require(type(token) is dict and set(token)=={'session_id','rune'} and hex32(token['session_id'])
                and type(token['rune']) is str and 0<len(token['rune'])<=16384,'invalid_session_credential')
        self.session_id=token['session_id'];self.client=Client(node['url'],token['rune'],ca_data=node['ca_pem'])
    def request(self,operation,contract='',pilot_id='',preimage=''):
        require(operation in ('info','plan','enroll','observe','publish','send','close','release','fail','retire'),'operation_refused')
        body=dict(session_id=self.session_id,operation=operation,contract=contract,pilot_id=pilot_id,preimage=preimage)
        req=urllib.request.Request(self.client.url+'/v1/swap-reverse-call',data=json.dumps(body).encode(),
                                  headers={'Content-Type':'application/json','Rune':self.client.rune},method='POST')
        try:
            with self.client.opener.open(req,timeout=20) as response:raw=response.read(1048577)
            require(len(raw)<=1048576,'session_rpc_unavailable');result=json.loads(raw)
            require(type(result) is dict and 'error' not in result,'session_rpc_unavailable')
        except Exception:raise ValueError('session_rpc_refused_or_uncertain') from None
        if 'route_error' in result:
            require(operation=='plan' and set(result)=={'route_error'} and type(result['route_error']) is str
                    and result['route_error'] in PLAN_ERRORS,'session_rpc_refused_or_uncertain')
            raise ValueError(result['route_error'])
        return result
    def call(self,method,**params):
        if method=='swap-pilot-observe':
            require(set(params)=={'pilot_id'},'invalid_parameters');return self.request('observe',**params)
        require(method=='swap-pilot-step' and set(params)=={'pilot_id','operation','preimage'},'invalid_parameters')
        return self.request(**params)


def paths(root):
    parent=root/'execution'/'reverse-swaps'
    require(not parent.is_symlink(),'invalid_pilot_directory')
    if not parent.exists():return []
    result=[]
    for path in sorted(parent.iterdir()):
        require(path.is_dir() and not path.is_symlink() and hex32(path.name),'invalid_pilot_directory')
        result.append(path)
    return result


def guard(root, except_id=None, cross=True):
    if cross:
        from forward_swaps import guard as other_guard
        other_guard(root,cross=False)
    old=pilot.directory(root)/'record.json'
    if old.exists():require(private_load(old)['phase'] in ('settled','failed'),'legacy_pilot_unfinished')
    for path in paths(root):
        if path.name!=except_id:require(private_load(path/'record.json')['phase'] in TERMINAL,'finish_current_swap_first')


def credentials(root):
    require(os.path.lexists(root/FILE),'repeat_setup_required')
    saved=private_load(root/FILE);config=load_config(root)
    require(config and all(saved.get(k)==v for k,v in swap_setup.binding(root,config).items()),'repeat_pairing_changed')
    return saved,config


def info(root,saved,config,*,factory=SessionRemote):
    started=time.monotonic(); result={}
    for role,network in (('xbt','xbt'),('btc','bitcoin')):
        row=factory(config['nodes'][role],saved['credentials'][role]).request('info')
        require(row.get('profile') in GRANT_LIMITS, 'reverse_grant_required')
        maximum=GRANT_LIMITS[row['profile']]
        require(type(row.get('max_delay_blocks',maximum)) is int and row.get('max_delay_blocks',maximum)==maximum,
                'invalid_grant_timing')
        row=dict(row,max_delay_blocks=maximum)
        require(row.get('node_id')==config['nodes'][role]['node_id'] and row.get('network')==network,
                'session_identity_changed')
        require(row.get('current') is True and row.get('paused') is False and row.get('gate_ready') is True,
                'session_inactive_or_restart_required')
        require(type(row.get('remaining')) is int and row['remaining']>0 and type(row.get('expires_at')) is int
                and row['expires_at']>int(time.time()),'session_expired_or_exhausted')
        require(type(row.get('channel')) is dict,'invalid_session_channel');result[role]=row
    require(time.monotonic()-started<=60 and load_config(root)==config
            and all(saved.get(k)==v for k,v in swap_setup.binding(root,config).items()),'repeat_pairing_changed')
    require(result['xbt'].get('routed',False)==result['btc'].get('routed',False),'grant_modes_differ')
    require(result['xbt']['max_delay_blocks']==result['btc']['max_delay_blocks'],'grant_timing_limits_differ')
    return result


def configure(root,request,*,factory=SessionRemote):
    require(set(request)=={'xbtCredential','btcCredential','confirmed'} and request['confirmed'] is True,'confirmation_required')
    with lifecycle.locked(root/'execution'):
        pilot.allowed(root);guard(root);config=load_config(root);require(config,'pair_nodes_first')
        saved=dict(**swap_setup.binding(root,config),credentials={k:request[k+'Credential'] for k in ('xbt','btc')})
        observed=info(root,saved,config,factory=factory)
        save(root/FILE,saved)
        return dict(setup_saved=True,max_delay_blocks=observed['btc']['max_delay_blocks'],remaining=min(r['remaining'] for r in observed.values()),
                    expires_at=min(r['expires_at'] for r in observed.values()),payment_started=False)


def prepare(root,request,*,factory=SessionRemote,inspector=swap_setup.Inspector):
    require(set(request)=={'invoice'},'invalid_request')
    pilot.allowed(root)
    saved,config=credentials(root);guard(root)
    observed=info(root,saved,config,factory=factory)
    def bound_inspector(node,rune):
        require(credentials(root)==(saved,config),'repeat_pairing_changed');return inspector(node,rune)
    if observed['xbt'].get('routed') is True:
        try:
            planned=factory(config['nodes']['btc'],saved['credentials']['btc']).request('plan',contract=request['invoice'])
        except ValueError as error:
            if str(error)=='bounded_route_unavailable':
                raise ValueError('bounded_route_unavailable_'+str(observed['btc']['max_delay_blocks'])) from None
            raise
        require(planned.get('channel') in observed['btc'].get('channels',[]),'route_outside_grant')
        setup,inspection_config=swap_setup.saved(root)
        require(inspection_config==config,'repeat_pairing_changed')
        def routed_inspector(node,rune):
            require(swap_setup.saved(root)==(setup,config),'inspection_setup_changed_pair_again')
            return bound_inspector(node,rune)
        # Select a ready admission channel; the paid HTLC may use any immutable
        # XBT grant pin and is bound durably when it actually arrives.
        incoming=observed['xbt']['channels']
        failures={}
        eligible=swap_setup.candidates(routed_inspector(config['nodes']['xbt'],setup['credentials']['xbt']),3000000,True,failures=failures)
        ready=[p for p in incoming if p['short_channel_id'] in eligible]
        if not ready:
            require(not eligible,'incoming_channel_outside_grant')
            reasons={failures.get(p['short_channel_id'],'channel_not_unique') for p in incoming}
            reason={frozenset(['channel_liquidity_insufficient']):'no_eligible_incoming_channels',
                    frozenset(['amount_trimmed_at_current_fee']):'incoming_amount_trimmed',
                    frozenset(['channel_has_pending_htlcs']):'incoming_channels_busy'}.get(frozenset(reasons),'incoming_channels_not_ready')
            require(False,reason)
        result=pilot.prepare(root,dict(invoice=request['invoice'],incomingChannel=ready[0]['short_channel_id'],
            outgoingChannel=planned['channel']['short_channel_id'],xbtRune=setup['credentials']['xbt'],btcRune=setup['credentials']['btc']),
            factory=routed_inspector,repeat=True,routing=dict(route=planned['route'],incoming_channels=incoming,
                max_delay_blocks=observed['btc']['max_delay_blocks']))
    else: raise ValueError('routed_reverse_required')
    return review(root,result['pilot_id'])


def review(root,swap_id):
    r,_=pilot.record(root,swap_id)
    return pilot.report(r) | dict(routed=routed(r['contract']),routing_fee_msat=sent_amount(r['contract'])-1500000,
        max_delay_blocks=route_limit(r['contract']),route_delay_blocks=r['contract']['route'][0]['delay'],
        approval_required=r['phase']=='review',recipient=r['contract']['recipient'],
        incoming_channel='Detected when payment arrives' if routed(r['contract']) else r['contract']['channels']['xbt']['short_channel_id'],
        outgoing_channel=r['contract']['channels']['btc']['short_channel_id'],admission_until=r['contract']['admission_until'])


def pending(root):
    active=[p.name for p in paths(root) if private_load(p/'record.json')['phase'] not in TERMINAL]
    require(len(active)<=1,'multiple_active_swaps_require_inspection')
    return active[0] if active else None


def approval_input(root):
    swap_id=pending(root);require(swap_id,'no_swap_to_confirm')
    r,_=pilot.record(root,swap_id);require(r['phase'] in ('review','authorizing'),'swap_already_approved')
    return dict(pilotId=swap_id)


def approve(root,request,*,factory=SessionRemote):
    require(set(request)=={'pilotId','confirmed'} and request['confirmed'] is True,'explicit_approval_required')
    swap_id=request['pilotId'];r,config=pilot.record(root,swap_id);pilot.allowed(root,r)
    require(pending(root)==swap_id,'review_changed')
    if r['phase']=='review':
        saved,config=credentials(root);observed=info(root,saved,config,factory=factory)
        if routed(r['contract']):
            require(all(route_limit(r['contract'])<=observed[k]['max_delay_blocks'] for k in ('xbt','btc')),
                    'contract_exceeds_grant_timing')
            require(all(observed[k].get('routed') is True for k in ('xbt','btc')) and
                    observed['xbt']['channels']==r['contract']['incoming_channels'] and
                    r['contract']['channels']['btc'] in observed['btc']['channels'],'session_channel_changed')
        else: require(all(observed[k].get('routed',False) is False and observed[k]['channel']==r['contract']['channels'][k] for k in ('xbt','btc')),'session_channel_changed')
        creds=saved['credentials']
    else:
        require(r['phase']=='authorizing','swap_already_approved')
        creds=private_load(pilot.directory(root,swap_id)/'credentials.json')
    # Explicit approval is persisted by pilot.approve before either enrollment.
    # Enrollment retries reserve at most one slot for this exact contract.
    def enrolling(node,token):
        client=factory(node,token)
        result=client.request('enroll',contract=canonical(r['contract']))
        require(result.get('pilot_id')==swap_id,'enrollment_changed');return client
    return pilot.approve(root,dict(pilotId=swap_id,confirmed=True,xbtRune=creds['xbt'],btcRune=creds['btc']),
                         factory=enrolling,swap_id=swap_id)


def cancel(root,request):
    require(set(request)=={'pilotId','confirmed'} and request['confirmed'] is True,'explicit_approval_required')
    with lifecycle.locked(root/'execution'):
        swap_id=request['pilotId'];r,_=pilot.record(root,swap_id);pilot.allowed(root,r)
        require(r['phase']=='review','only_unapproved_draft_can_be_cancelled')
        require(not (pilot.directory(root,swap_id)/'credentials.json').exists(),'enrollment_may_have_started')
        r['phase']='cancelled';save(pilot.directory(root,swap_id)/'record.json',r);return review(root,swap_id)


def retire_unpaid(root,swap_id,*,factory=SessionRemote):
    with lifecycle.locked(root/'execution'):
        r,config=pilot.record(root,swap_id);pilot.allowed(root,r)
        if r['phase'] not in ('waiting_for_xbt','retire_intent'):return False
        if type(r.get('terms',{}).get('expires_at')) is not int or int(time.time())<r['terms']['expires_at']:return False
        creds=private_load(pilot.directory(root,swap_id)/'credentials.json')
        clients={k:factory(config['nodes'][k],creds[k]) for k in ('xbt','btc')}
        obs=pilot.observations(r,clients);gate=obs['xbt'].get('gate',{})
        if r['phase']=='waiting_for_xbt' and gate.get('phase')!='quoted':return False
        require(gate.get('phase') in ('quoted','expired') and gate.get('binding') is None
                and obs['btc'].get('payments')==[] and 'send' not in obs['btc'].get('intents',{})
                and all(obs[k]['channel'].get('htlcs')==[] for k in ('xbt','btc')), 'retirement_needs_inspection')
        if r['phase']!='retire_intent':
            r['phase']='retire_intent';save(pilot.directory(root,swap_id)/'record.json',r)
        # Retire admission atomically at the gate before retiring BTC authority.
        # Both operations are exact-target and explicitly idempotent.
        for role in ('xbt','btc'):
            require(clients[role].request('retire',pilot_id=swap_id)=={'retired':True},'retirement_unknown')
        r['phase']='expired';save(pilot.directory(root,swap_id)/'record.json',r)
        return True


def tick(root,*,factory=SessionRemote):
    results={}
    for path in paths(root):
        r=private_load(path/'record.json')
        if r['phase'] in (*TERMINAL,'review'):continue
        try:
            if r['phase']=='authorizing':results[path.name]=approve(root,dict(pilotId=path.name,confirmed=True),factory=factory)
            elif retire_unpaid(root,path.name,factory=factory):results[path.name]=review(root,path.name)
            else:results[path.name]=pilot.tick(root,factory=factory,swap_id=path.name)
            save(path/'attention.json',dict(needs_attention=False))
        except Exception:
            save(path/'attention.json',dict(needs_attention=True))
            raise
    return results


def status(root):
    rows=[]
    for path in paths(root):
        r=private_load(path/'record.json')
        attention=private_load(path/'attention.json').get('needs_attention') is True if (path/'attention.json').exists() else False
        rows.append(pilot.report(r) | dict(restore_blocked=r.get('restore_epoch')!=pilot.restore_epoch(root),needs_attention=attention))
    return dict(swaps=rows)


def main():
    os.umask(0o077);root=Path(sys.argv[1]);mode=sys.argv[2]
    raw=sys.stdin.read(65537);require(len(raw)<=65536,'request_too_large');request=json.loads(raw or '{}')
    functions={'configure':configure,'prepare':prepare,'approve':approve,'cancel':cancel}
    if mode in functions:return functions[mode](root,request)
    require(request=={} and mode in ('status','approval-input'),'invalid_request')
    return status(root) if mode=='status' else approval_input(root)


if __name__=='__main__':
    try:print(json.dumps(main()))
    except Exception as error:
        safe={'grant_timing_limits_differ','contract_exceeds_grant_timing','route_planning_refused','invalid_grant_timing',
              'incoming_channel_outside_grant','incoming_amount_trimmed','incoming_channels_busy','incoming_channels_not_ready',
              'grant_modes_differ','bounded_route_unavailable','route_outside_grant','no_eligible_incoming_channels','repeat_setup_required','repeat_pairing_changed','session_inactive_or_restart_required',
              'session_expired_or_exhausted','legacy_pilot_unfinished','finish_current_swap_first',
              'review_changed','no_swap_to_confirm','only_unapproved_draft_can_be_cancelled',
              'session_channel_changed','enrollment_may_have_started','swap_already_approved',
              'inspection_setup_required','inspection_setup_changed_pair_again','recipient_invoice_needs_33_minutes',
              'backup_in_progress','restored_pilot_authority_blocked','confirmed_reserve_insufficient',
              'invalid_recipient_invoice','selected_channel_unavailable'}
        safe.update('bounded_route_unavailable_'+str(limit) for limit in GRANT_LIMITS.values())
        reason=str(error) if isinstance(error,ValueError) and str(error) in safe else 'repeat_swap_refused_or_uncertain'
        print(json.dumps(dict(reason=reason)));raise SystemExit(1) from None
