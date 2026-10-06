"""Regtest admission policy shared by quote preparation, approval and handoff.

These are the existing disposable fixture limits, NOT the live pilot profiles.
Only authenticated workflow responses are supplied by the caller. No RPC here.
"""
import hashlib
import json

from controller import require
from live_policy import PIN, integer, quote_window_errors, remaining_within


def definition(direction):
    require(direction in ('forward', 'reverse'), 'invalid_policy_direction')
    return dict(version='regtest-quote-admission-v1', source_commit=PIN, direction=direction,
                live_payment_enabled=False, quote_lifetime_seconds=600 if direction=='forward' else 900,
                observation_max_age_seconds=120, recipient_headroom_seconds=60,
                btc_min_msat=1000 if direction=='forward' else 100000000,
                btc_max_msat=1000000000 if direction=='forward' else 100000000,
                btc_multiple_msat=1000,
                xbt_min_msat=1 if direction=='forward' else 200000000,
                xbt_max_msat=1000000000 if direction=='forward' else 200000000,
                route_hops=1, routing_fee_msat=0, route_delay_blocks=40,
                incoming_min_cltv=100, incoming_max_cltv=2000)


def commitment(direction):
    value=definition(direction)
    digest=hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    return dict(version=value['version'],direction=direction,digest=digest)


def check_commitment(record, direction, required=False):
    if required or 'quote_policy' in record:
        require(record.get('quote_policy')==commitment(direction), 'quote_policy_changed')


def check(data, decoded, direction, *, observed_at, now):
    limits=definition(direction);terms=data['terms'];state=data['controller']
    require(state.get('profile','regtest')==('regtest' if direction=='forward' else 'reverse-regtest-v1'),
            'regtest_policy_required')
    require('pilot' not in terms and 'profile' not in terms,'regtest_policy_required')
    for asset in ('btc','xbt'):
        amount=terms.get(asset+'_amount_msat')
        require(integer(amount,limits[asset+'_min_msat'],limits[asset+'_max_msat']),'quote_policy_amount')
        # The pinned forward regtest template stores only the outgoing amount.
        if asset=='xbt' or direction=='reverse' or asset+'_amount_msat' in state:
            require(type(state.get(asset+'_amount_msat')) is int and state[asset+'_amount_msat']==amount,
                    'quote_policy_amount')
    require(terms['btc_amount_msat']%limits['btc_multiple_msat']==0,'quote_policy_amount')
    route=state.get('route')
    require(type(route) is list and len(route)==1 and type(route[0]) is dict,'quote_policy_route')
    hop=route[0];outgoing='btc' if direction=='reverse' else 'xbt'
    require(type(hop.get('amount_msat')) is int and hop['amount_msat']==terms[outgoing+'_amount_msat'],
            'quote_policy_fee')
    require(type(hop.get('delay')) is int and hop['delay']==40,'quote_policy_route_delay')
    require(integer(decoded.get('amount_msat'),1,1000000000) and
            decoded['amount_msat']==hop['amount_msat'],'quote_policy_invoice_amount')
    require(integer(decoded.get('min_final_cltv_expiry'),1,hop['delay']),'quote_policy_recipient_cltv')
    require(integer(decoded.get('created_at'),0,2**53-1) and integer(decoded.get('expiry'),1,2**53-1),
            'quote_policy_invoice_time')
    require(decoded['created_at']<=now,'quote_policy_invoice_time')
    require(not quote_window_errors(terms.get('expires_at'),decoded['created_at']+decoded['expiry'],
                observed_at,now,lifetime=limits['quote_lifetime_seconds']), 'quote_policy_expiry_or_observation')
    require(type(terms.get('min_cltv_delta')) is int and terms['min_cltv_delta']==100 and
            type(terms.get('max_cltv_delta')) is int and terms['max_cltv_delta']==2000,'quote_policy_gate_cltv')


def check_held(data, height, expiry):
    terms=data['terms']
    require(remaining_within(height,expiry,terms['min_cltv_delta'],terms['max_cltv_delta']),
            'quote_policy_held_margin')
