"""Immutable single forward pilot contract. No network or mutation authority."""
import hashlib
import json
import re

PROFILE = 'startos-forward-pilot-v1'
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
    require(type(c) is dict and set(c)=={'profile','source_commit','nonce','nodes','channels','invoice',
        'payment_hash','payment_secret','recipient','created_at','admission_until'}, 'invalid_contract')
    require(c['profile']==PROFILE and c['source_commit']==PIN and hex32(c['nonce']), 'invalid_profile')
    require(type(c['nodes']) is dict and set(c['nodes'])=={'btc','xbt'}, 'invalid_nodes')
    require(all(type(n) is str and re.fullmatch('0[23][0-9a-f]{64}',n) for n in c['nodes'].values())
            and c['nodes']['btc']!=c['nodes']['xbt'], 'invalid_nodes')
    require(type(c['channels']) is dict and set(c['channels'])=={'btc','xbt'}, 'invalid_channels')
    for pin in c['channels'].values():
        require(type(pin) is dict and set(pin)=={'channel_id','short_channel_id','funding_txid','funding_outnum','peer_id'},'invalid_channel_pin')
        require(hex32(pin['channel_id']) and hex32(pin['funding_txid']) and
            type(pin['short_channel_id']) is str and re.fullmatch('[0-9]+x[0-9]+x[0-9]+',pin['short_channel_id'])
            and type(pin['funding_outnum']) is int and 0<=pin['funding_outnum']<2**32
            and type(pin['peer_id']) is str and re.fullmatch('0[23][0-9a-f]{64}',pin['peer_id']), 'invalid_channel_pin')
    require(c['recipient']==c['channels']['xbt']['peer_id'],'recipient_channel_mismatch')
    require(hex32(c['payment_hash']) and hex32(c['payment_secret']),'invalid_invoice_binding')
    require(type(c['invoice']) is str and c['invoice'].startswith('lnxbt') and len(c['invoice'])<=16384,'invalid_invoice')
    require(type(c['created_at']) is int and type(c['admission_until']) is int
            and c['admission_until']==c['created_at']+1800,'invalid_admission_window')
    return c

def route(c):
    return [dict(id=c['recipient'],channel=c['channels']['xbt']['short_channel_id'],amount_msat=2000000,delay=40)]

def pin_matches(channel,pin):
    return all(type(channel.get(k)) is type(v) and channel.get(k)==v for k,v in pin.items())
