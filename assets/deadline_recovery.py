"""Explicit disposable-regtest gate release after a pinned deadline close.

CLN owns on-chain transactions. This only hands a verified outgoing preimage
back to the original incoming gate. It does not prove a claim or sweep confirmed.
"""
import hashlib
import json
from pathlib import Path
import re
import time
from controller import private_load,require,save
from deadline_boundary import DeadlineRemote,validate,initial_state
from executor import PIN,guard,lock,execution_allowed,digest


class ClaimRemote(DeadlineRemote):
    def __init__(self,config,direction,payment_hash,binding):
        require(direction in ('forward','reverse'),'invalid_direction')
        super().__init__(config)
        self.incoming_network='regtest' if direction=='forward' else 'xbt-regtest'
        self.release='xbt-release' if direction=='forward' else 'reverse-release'
        self.payment_hash=payment_hash;self.binding=binding

    def call(self,method,*args,named=False):
        require(not named and method!='close','claim_rpc_refused')
        if method not in ('xbt-release','reverse-release'):
            return super().call(method,*args)
        guard()
        require(method==self.release and self.network==self.incoming_network,'incoming_release_only')
        require(len(args)==(1 if method=='xbt-release' else 3),'invalid_release_args')
        preimage=args[-1]
        require(type(preimage) is str and re.fullmatch('[0-9a-f]{64}',preimage)
                and hashlib.sha256(bytes.fromhex(preimage)).hexdigest()==self.payment_hash,'invalid_preimage')
        params=dict(preimage=preimage)
        if method=='reverse-release':
            require(args[:2]==(self.payment_hash,json.dumps(self.binding)),'release_binding_changed')
            params.update(payment_hash=self.payment_hash,binding=self.binding)
        super().call('getinfo')  # HTTPS identity check immediately before mutation.
        if method=='xbt-release':
            params['payment_hash']=self.payment_hash
            return self._request('xbt-release-bound',params)
        return self._request(method,params)


def resolve(root,spec,clients,*,clock=time.monotonic,modules=Path('/opt/swap')):
    guard();validate(spec)
    require((modules/'SOURCE_COMMIT').read_text().strip()==PIN,'source_pin_mismatch')
    incoming='btc' if spec['direction']=='forward' else 'xbt'
    outgoing='xbt' if incoming=='btc' else 'btc'
    binding=[spec['channel']['short_channel_id'],spec['htlc_id']]
    require(set(clients)=={'btc','xbt'},'two_nodes_required')
    for role,network in (('btc','regtest'),('xbt','xbt-regtest')):
        require(clients[role].network==network and clients[role].node_id==spec['node_ids'][role], 'regtest_identity_required')
    with lock(root):
        execution_allowed(root)
        require(not (root/'restored.json').exists() and not (root/'restored.json').is_symlink(),'restored_execution_blocked')
        record=private_load(root/'deadline.json');validate(record['spec'])
        require(record['source_commit']==PIN and digest(record['spec'])==digest(spec),'deadline_binding_changed')
        state=record['state'];mutable={incoming+'_close_intent',incoming+'_close_result'}
        require({k:v for k,v in state.items() if k not in mutable}==initial_state(spec),'deadline_state_changed')
        expected=dict(binding=binding,payment_hash=spec['payment_hash'],expiry=spec['expiry'])
        if incoming=='btc':expected['channel_id']=spec['channel']['channel_id']
        else:expected['channel']=spec['channel']
        require(digest(state.get(incoming+'_close_intent'))==digest(expected),'original_close_intent_required')
        path=root/'claim-receipt.json';previous=private_load(path) if path.exists() else None
        start=clock()
        def fresh():require(0<=clock()-start<=120,'claim_observation_expired')
        def call(role,method,*args):
            fresh();value=clients[role].call(method,*args);fresh();return value
        for role in ('btc','xbt'):
            info=call(role,'getinfo')
            require(info.get('id')==spec['node_ids'][role] and info.get('network')==clients[role].network,'operator_identity_mismatch')
            require(not any(k.startswith('warning') for k in info),'node_warning')
        payments=call(outgoing,'listsendpays',spec['payment_hash']).get('payments')
        require(type(payments) is list and len(payments)==1 and type(payments[0]) is dict,'original_attempt_required')
        payment=dict(payments[0]);payment.setdefault('partid',0)
        fixed=dict(payment_hash=spec['payment_hash'],groupid=spec['groupid'],partid=spec['partid'],amount_sent_msat=spec['outgoing_amount_msat'])
        require(all(payment.get(k)==v and type(payment.get(k)) is type(v) for k,v in fixed.items()),'outgoing_attempt_changed')
        require(type(payment.get('id')) is int and payment['id']>=0,'invalid_attempt_id')
        gate_method='xbt-quote-status' if incoming=='btc' else 'reverse-status'
        def gate_read():
            gate=call(incoming,gate_method,spec['payment_hash'])
            require(gate.get('payment_hash')==spec['payment_hash'] and gate.get('binding')==binding,'gate_binding_changed')
            if incoming=='xbt':
                terms=gate.get('terms',{})
                require(type(gate.get('cltv_expiry')) is int and gate['cltv_expiry']==spec['expiry']
                    and terms.get('payment_hash')==spec['payment_hash']
                    and terms.get('xbt_amount_msat')==spec['incoming_amount_msat']
                    and terms.get('btc_amount_msat')==spec['outgoing_amount_msat'],'gate_terms_changed')
            return gate
        gate=gate_read()
        receipt_fixed=dict(spec_digest=digest(spec),attempt={k:payment[k] for k in ('id','groupid','partid')})
        if previous:
            require(all(previous.get(k)==v for k,v in receipt_fixed.items()) and previous.get('stage') in ('release_intent','gate_resolved'),'claim_receipt_changed')
        report=dict(regtest_only=True,live_payment_enabled=False,payment_started=False,onchain_claim_verified=False)
        if payment.get('status')=='pending':
            require(previous is None and gate.get('phase')=='held' and not payment.get('payment_preimage'),'outcome_regressed')
            return dict(report,phase='outgoing_pending')
        require(payment.get('status')=='complete','completed_outgoing_required')
        preimage=payment.get('payment_preimage')
        require(type(preimage) is str and re.fullmatch('[0-9a-f]{64}',preimage)
                and hashlib.sha256(bytes.fromhex(preimage)).hexdigest()==spec['payment_hash'],'invalid_preimage')
        require(gate.get('phase') in ('held','resolved'),'gate_outcome_changed')
        if gate['phase']=='resolved':
            require(previous is not None,'unrecorded_gate_resolution')
        else:
            require(previous is None,'release_outcome_unknown')
            channels=call(incoming,'listpeerchannels').get('channels',[])
            matches=[c for c in channels if c.get('channel_id')==spec['channel']['channel_id']]
            require(len(matches)==1 and matches[0].get('state')=='ONCHAIN' and
                    all(matches[0].get(k)==v and type(matches[0].get(k)) is type(v) for k,v in spec['channel'].items()),'original_onchain_channel_required')
            if incoming=='btc':
                spend=call(incoming,'xbt-spend-info',spec['payment_hash'])
                terms=dict(payment_hash=spec['payment_hash'],binding=binding,cltv_expiry=spec['expiry'],
                           btc_amount_msat=spec['incoming_amount_msat'],xbt_amount_msat=spec['outgoing_amount_msat'])
                require(all(spend.get(k)==v and type(spend.get(k)) is type(v) for k,v in terms.items()),'gate_terms_changed')
            fresh();save(path,dict(**receipt_fixed,stage='release_intent'))
            method='xbt-release' if incoming=='btc' else 'reverse-release'
            args=(preimage,) if incoming=='btc' else (spec['payment_hash'],json.dumps(binding),preimage)
            require(call(incoming,method,*args)=={'released':1},'release_reply_unknown')
            require(gate_read().get('phase')=='resolved','gate_resolution_unconfirmed')
        receipt=dict(**receipt_fixed,stage='gate_resolved')
        if previous!=receipt:save(path,receipt)
        return dict(report,phase='gate_resolved')
