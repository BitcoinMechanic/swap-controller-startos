"""Disposable regtest bridge to pinned deadline guards; no live/worker entry point.

This only hands an exact incoming channel to CLN's unilateral-close machinery.
It does not implement preimage claims, timeout proofs, fee bumping or settlement.
"""
import copy
import importlib
import os
from pathlib import Path
import re
import sys
import time
from controller import private_load, require, save
from execution_rpc import Remote
from executor import PIN, guard, lock, execution_allowed

PIN_KEYS = {'channel_id','funding_txid','funding_outnum','peer_id','short_channel_id'}
CLOSING = {'AWAITING_UNILATERAL','FUNDING_SPEND_SEEN','ONCHAIN'}


class DeadlineRemote(Remote):
    """Separate regtest transport. No send, release or fail capability."""
    def __init__(self, config, channel_id=None):
        super().__init__(config)  # Refuses bitcoin/xbt before transport creation.
        require(channel_id is None or re.fullmatch('[0-9a-f]{64}',channel_id), 'invalid_close_target')
        self.channel_id=channel_id

    def call(self, method, *args, named=False):
        require(not named, 'named_deadline_rpc_refused')
        schema={'getinfo':0,'listpeerchannels':0,'listsendpays':1}
        schema.update({'xbt-quote-status':1,'xbt-spend-info':1} if self.network=='regtest' else {'reverse-status':1})
        if method=='close':
            guard()
            require(self.channel_id is not None and args==(self.channel_id,1) and type(args[1]) is int, 'close_target_refused')
            params={'id':self.channel_id,'unilateraltimeout':1}
        else:
            require(method in schema and len(args)==schema[method], 'deadline_rpc_refused')
            if args:require(type(args[0]) is str and re.fullmatch('[0-9a-f]{64}',args[0]),'invalid_payment_hash')
            params={'payment_hash':args[0]} if args else {}
        info=self._request('getinfo',{})
        require(info.get('id')==self.node_id and info.get('network')==self.network,'operator_identity_mismatch')
        return info if method=='getinfo' else self._request(method,params)


def validate(spec):
    require(type(spec) is dict and set(spec)=={'direction','node_ids','channel','htlc_id','payment_hash',
            'expiry','incoming_amount_msat','outgoing_amount_msat','groupid','partid'},'invalid_deadline_spec')
    require(spec['direction'] in ('forward','reverse'),'invalid_direction')
    require(type(spec['node_ids']) is dict and set(spec['node_ids'])=={'btc','xbt'},'invalid_nodes')
    for node in spec['node_ids'].values():require(type(node) is str and re.fullmatch('0[23][0-9a-f]{64}',node),'invalid_node_id')
    require(spec['node_ids']['btc']!=spec['node_ids']['xbt'],'distinct_nodes_required')
    pin=spec['channel'];require(type(pin) is dict and set(pin)==PIN_KEYS,'invalid_channel_pin')
    for key in ('channel_id','funding_txid'):
        require(type(pin[key]) is str and re.fullmatch('[0-9a-f]{64}',pin[key]),'invalid_channel_pin')
    require(type(pin['peer_id']) is str and re.fullmatch('0[23][0-9a-f]{64}',pin['peer_id']),'invalid_channel_pin')
    require(type(pin['short_channel_id']) is str and re.fullmatch(r'[0-9]+x[0-9]+x[0-9]+',pin['short_channel_id']),'invalid_channel_pin')
    require(type(pin['funding_outnum']) is int and 0<=pin['funding_outnum']<2**32,'invalid_channel_pin')
    require(type(spec['payment_hash']) is str and re.fullmatch('[0-9a-f]{64}',spec['payment_hash']),'invalid_payment_hash')
    for key in ('htlc_id','expiry','incoming_amount_msat','outgoing_amount_msat','groupid','partid'):
        require(type(spec[key]) is int and 0<=spec[key]<2**53,'invalid_numeric_binding')
    require(0<spec['expiry']<500000000 and spec['incoming_amount_msat']>0 and spec['outgoing_amount_msat']>0,'invalid_numeric_binding')


def initial_state(spec):
    binding=[spec['channel']['short_channel_id'],spec['htlc_id']]
    state=dict(phase='outgoing_started',payment_hash=spec['payment_hash'],btc_cli=['btc'],xbt_cli=['xbt'])
    if spec['direction']=='forward':
        state.update(quote_gate=True,btc_deadline_guard=True,btc_binding=binding)
    else:
        state.update(profile='reverse-regtest-v1',durable_gate=True,xbt_deadline_guard=True,
            xbt_onchain_claim=True, # Required by pinned guard; NOT proof of packaged claim support.
            node_ids=[spec['node_ids']['xbt'],spec['node_ids']['btc']],xbt_binding=binding,
            xbt_expiry=spec['expiry'],xbt_amount_msat=spec['incoming_amount_msat'],incoming_channel=spec['channel'])
    return state


def step(root, spec, clients, *, modules=Path('/opt/swap'), clock=time.monotonic):
    guard();validate(spec)
    require((modules/'SOURCE_COMMIT').read_text().strip()==PIN,'source_pin_mismatch')
    require(set(clients)=={'btc','xbt'},'two_nodes_required')
    for role,network in (('btc','regtest'),('xbt','xbt-regtest')):
        require(clients[role].network==network and clients[role].node_id==spec['node_ids'][role], 'regtest_identity_required')
    incoming='btc' if spec['direction']=='forward' else 'xbt'
    outgoing='xbt' if incoming=='btc' else 'btc'
    require(clients[incoming].channel_id==spec['channel']['channel_id'] and clients[outgoing].channel_id is None,'close_authority_binding_mismatch')
    with lock(root):
        execution_allowed(root)
        require(not os.path.lexists(root/'restored.json'),'restored_execution_blocked')
        start=clock();base=initial_state(spec);path=root/'deadline.json'
        mutable={incoming+'_close_intent',incoming+'_close_result'}
        if path.exists():
            record=private_load(path)
            require(record['source_commit']==PIN and record['spec']==spec,'deadline_binding_changed')
            state=record['state']
            require({k:v for k,v in state.items() if k not in mutable}==base,'deadline_state_changed')
        else:state=copy.deepcopy(base)
        for role in ('btc','xbt'):
            info=clients[role].call('getinfo')
            require(info.get('id')==spec['node_ids'][role] and info.get('network')==clients[role].network,'operator_identity_mismatch')
            require(not any(k.startswith('warning') for k in info),'node_warning')
            require(type(info.get('blockheight')) is int and 0<=info['blockheight']<500000000,'invalid_block_height')
        payments=clients[outgoing].call('listsendpays',spec['payment_hash']).get('payments')
        expected=dict(payment_hash=spec['payment_hash'],groupid=spec['groupid'],partid=spec['partid'],
                      amount_sent_msat=spec['outgoing_amount_msat'],status='pending')
        require(type(payments) is list and len(payments)==1 and type(payments[0]) is dict,'original_outgoing_attempt_not_pending')
        # CLN omits partid for the non-MPP part zero. No other missing field
        # receives a default, and explicit bool/string/null values still fail.
        payment=dict(payments[0]);payment.setdefault('partid',0)
        require(all(payment.get(k)==v and type(payment.get(k)) is type(v) for k,v in expected.items()),'original_outgoing_attempt_not_pending')

        def persist(_path,value):
            require({k:v for k,v in value.items() if k not in mutable}==base,'deadline_state_changed')
            save(path,dict(source_commit=PIN,spec=spec,state=value))

        def rpc(cli,method,*args):
            require(cli==[incoming],'incoming_deadline_rpc_only')
            require(0<=clock()-start<=120,'deadline_observation_expired')
            if method=='close':
                # Persisted intent must exist before any force-close RPC.
                record=private_load(path)
                require(record['spec']==spec and incoming+'_close_intent' in record['state'],'durable_close_intent_required')
                require(args==(spec['channel']['channel_id'],1),'close_target_refused')
            result=clients[incoming].call(method,*args)
            if method=='getinfo':
                require(type(result.get('blockheight')) is int and 0<=result['blockheight']<500000000,'invalid_block_height')
            if method=='listpeerchannels':
                matches=[c for c in result['channels'] if c.get('channel_id')==spec['channel']['channel_id']]
                require(len(matches)==1 and all(matches[0].get(k)==v and type(matches[0].get(k)) is type(v) for k,v in spec['channel'].items()),'incoming_funding_pin_changed')
                c=matches[0]
                if c['state'] not in CLOSING:
                    expected_htlc=dict(id=spec['htlc_id'],direction='in',payment_hash=spec['payment_hash'],
                        amount_msat=spec['incoming_amount_msat'],expiry=spec['expiry'],state='RCVD_ADD_ACK_REVOCATION')
                    h=[h for h in c.get('htlcs',[]) if h.get('id')==spec['htlc_id'] and h.get('direction')=='in']
                    require(len(h)==1 and all(h[0].get(k)==v and type(h[0].get(k)) is type(v) for k,v in expected_htlc.items())
                            and h[0].get('local_trimmed',False) is False,'incoming_htlc_changed')
            if method in ('xbt-spend-info','reverse-status'):
                require(type(result.get('cltv_expiry')) is int and result['cltv_expiry']==spec['expiry'],'incoming_expiry_changed')
            return result
        sys.path.insert(0,str(modules))
        if incoming=='btc':
            importlib.import_module('deadline_guard').protect(path,state,rpc,persist)
        else:
            gate=rpc(['xbt'],'reverse-status',spec['payment_hash'])
            importlib.import_module('reverse_deadline').protect(path,state,rpc,persist,gate)
        return dict(regtest_only=True,live_payment_enabled=False,payment_started=False,
                    close_intent_recorded=incoming+'_close_intent' in state,
                    close_reply_recorded=incoming+'_close_result' in state,
                    onchain_claim_verified=False)
