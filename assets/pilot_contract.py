"""Immutable single forward pilot contract. No network or mutation authority."""
import hashlib
import json
import re
import market_terms as mt

PROFILE = 'startos-forward-pilot-v1'
ROUTED = 'startos-forward-routed-v1'
PLAN_ERRORS = frozenset({'bounded_route_unavailable', 'route_outside_grant',
                         'route_planning_refused', 'invalid_recipient_invoice'})
FIELDS = ('channel_id','short_channel_id','funding_txid','funding_outnum','peer_id')

def routed(c): return c.get('profile') in (ROUTED,mt.FORWARD)
PIN = '81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe'

def require(ok, reason):
    if not ok: raise ValueError(reason)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def hex32(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None

def validate(c):
    require(type(c) is dict, 'invalid_contract')
    extra = {'incoming_channels','route'} if routed(c) else set()
    if mt.market(c): extra.add('pricing')
    require(set(c)-extra=={'profile','source_commit','nonce','nodes','channels','invoice',
        'payment_hash','payment_secret','recipient','created_at','admission_until'}, 'invalid_contract')
    require(c['profile'] in (PROFILE,ROUTED,mt.FORWARD) and c['source_commit']==PIN and hex32(c['nonce']), 'invalid_profile')
    require(type(c['nodes']) is dict and set(c['nodes'])=={'btc','xbt'}, 'invalid_nodes')
    require(all(type(n) is str and re.fullmatch('0[23][0-9a-f]{64}',n) for n in c['nodes'].values())
            and c['nodes']['btc']!=c['nodes']['xbt'], 'invalid_nodes')
    require(type(c['channels']) is dict and set(c['channels'])=={'btc','xbt'}, 'invalid_channels')
    if routed(c):
        require(type(c.get('incoming_channels')) is list and 1<=len(c['incoming_channels'])<=8, 'invalid_incoming_channels')
        require(c['channels']['btc'] in c['incoming_channels'], 'invalid_incoming_channels')
        require(len({p.get('channel_id') for p in c['incoming_channels']})==len(c['incoming_channels'])
                and len({p.get('short_channel_id') for p in c['incoming_channels']})==len(c['incoming_channels']), 'duplicate_incoming_channel')
    for pin in [*c['channels'].values(), *(c['incoming_channels'] if routed(c) else [])]:
        require(type(pin) is dict and set(pin)=={'channel_id','short_channel_id','funding_txid','funding_outnum','peer_id'},'invalid_channel_pin')
        require(hex32(pin['channel_id']) and hex32(pin['funding_txid']) and
            type(pin['short_channel_id']) is str and re.fullmatch('[0-9]+x[0-9]+x[0-9]+',pin['short_channel_id'])
            and type(pin['funding_outnum']) is int and 0<=pin['funding_outnum']<2**32
            and type(pin['peer_id']) is str and re.fullmatch('0[23][0-9a-f]{64}',pin['peer_id']), 'invalid_channel_pin')
    if routed(c):
        validate_route(c['route'], c['nodes']['xbt'], c['recipient'],recipient_amount=mt.amounts(c)['xbt'])
        require(c['route'][0]['id']==c['channels']['xbt']['peer_id'] and
                c['route'][0]['channel']==c['channels']['xbt']['short_channel_id'], 'route_first_hop_changed')
    else: require(c['recipient']==c['channels']['xbt']['peer_id'],'recipient_channel_mismatch')
    require(hex32(c['payment_hash']) and hex32(c['payment_secret']),'invalid_invoice_binding')
    require(type(c['invoice']) is str and c['invoice'].startswith('lnxbt') and len(c['invoice'])<=16384,'invalid_invoice')
    require(type(c['created_at']) is int and type(c['admission_until']) is int
            and c['admission_until']==c['created_at']+1800,'invalid_admission_window')
    mt.validate(c)
    return c

def route(c):
    if routed(c): return c['route']
    return [dict(id=c['recipient'],channel=c['channels']['xbt']['short_channel_id'],amount_msat=2000000,delay=40)]

def pin_matches(channel,pin):
    return all(type(channel.get(k)) is type(v) and channel.get(k)==v for k,v in pin.items())


def validate_route(hops, source, recipient, *, recipient_amount=2000000):
    require(type(hops) is list and 1<=len(hops)<=4, 'route_hop_limit')
    nodes={source};channels=set();amount=recipient_amount+10000;delay=80
    for h in hops:
        require(type(h) is dict and set(h)=={'id','channel','amount_msat','delay'},'invalid_route')
        require(type(h['id']) is str and re.fullmatch('0[23][0-9a-f]{64}',h['id']) and h['id'] not in nodes,
                'route_loop_or_invalid_node')
        require(type(h['channel']) is str and re.fullmatch('[0-9]+x[0-9]+x[0-9]+',h['channel'])
                and h['channel'] not in channels,'route_loop_or_invalid_channel')
        require(type(h['amount_msat']) is int and recipient_amount<=h['amount_msat']<=amount
                and type(h['delay']) is int and 40<=h['delay']<=delay,'route_fee_or_delay_limit')
        nodes.add(h['id']);channels.add(h['channel']);amount=h['amount_msat'];delay=h['delay']
    require(hops[-1]['id']==recipient and amount==recipient_amount and delay==40, 'route_recipient_changed')
    return hops


def sent_amount(c): return route(c)[0]['amount_msat']


def incoming_pins(c): return c['incoming_channels'] if routed(c) else [c['channels']['btc']]
