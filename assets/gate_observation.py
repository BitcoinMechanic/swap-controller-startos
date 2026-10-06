"""Identity- and generation-bound, read-only BTC/XBT gate observations."""
import json
from pathlib import Path
import sys
import time
from controller import load_config, private_load, save, locked, require, NETWORKS
from read_only_rpc import Client

PROFILES = {'btc': 'live-pilot-v1', 'xbt': 'reverse-live-v1'}
METHOD = {'btc': 'xbt-pilot-info', 'xbt': 'reverse-pilot-info'}

def record_name(role):
    require(role in PROFILES, 'invalid_gate_role')
    return role+'-gate-observation.json'

RECORD = 'btc-gate-observation.json'
PROFILE = 'live-pilot-v1'

class GateClient(Client):
    methods = ('getinfo', 'xbt-pilot-info')


class XbtGateClient(Client):
    methods = ('getinfo', 'reverse-pilot-info')


def check(node, rune, factory=None, role='btc'):
    require(role in PROFILES, 'invalid_gate_role')
    factory = factory or (GateClient if role == 'btc' else XbtGateClient)
    client = factory(node['url'], rune, ca_data=node['ca_pem'])
    info = client.call('getinfo')
    require(info.get('id') == node['node_id'] and info.get('network') == NETWORKS[role], 'identity_mismatch')
    require(not any(k.startswith('warning') and bool(v) for k,v in info.items()), 'node_warning')
    gate = client.call(METHOD[role])
    require(gate.get('profile') == PROFILES[role], 'unsupported_gate_profile')
    if role == 'xbt':
        require(gate.get('gate_active') is True, 'gate_inactive')
        return dict(observation='verified', profile=PROFILES[role], gate_active=True)
    count = gate.get('registered_quotes')
    require(type(count) is int and count >= 0, 'invalid_quote_count')
    return dict(observation='verified', profile=PROFILE, registered_quotes=count)


def read_record(root, config, role='btc'):
    record = private_load(root/record_name(role))
    require(set(record) == {'schema','generation','node_id','rune'} and record['schema'] == 1,
            'invalid_gate_observation_record')
    require(record['generation'] == config['generation'] and record['node_id'] == config['nodes'][role]['node_id'],
            'gate_pairing_changed')
    return record


def observe(root, config, factory=None, role='btc'):
    try:
        record = read_record(root, config, role)
        result = check(config['nodes'][role], record['rune'], factory, role)
        require(read_record(root, config, role) == record, 'gate_credential_changed')
        return result
    except FileNotFoundError:
        return dict(observation='not_paired')
    except Exception:
        return dict(observation='unavailable')


def pair(root, rune, confirmed, factory=None, monitor_factory=Client, clock=time.monotonic, role='btc'):
    require(role in PROFILES, 'invalid_gate_role')
    require(confirmed is True, 'confirmation_required')
    with locked(root):
        config = load_config(root)
        require(config is not None, 'pair_nodes_first')
        start = clock()
        # Verify both existing monitor identities before storing any new credential.
        for node_role, network in NETWORKS.items():
            node = config['nodes'][node_role]
            info = monitor_factory(node['url'], node['rune'], ca_data=node['ca_pem']).call('getinfo')
            require(info.get('id') == node['node_id'] and info.get('network') == network, 'identity_mismatch')
        result = check(config['nodes'][role], rune, factory, role)
        require(load_config(root) == config and 0 <= clock()-start <= 120, 'pairing_changed_or_expired')
        save(root/record_name(role), dict(schema=1, generation=config['generation'], node_id=config['nodes'][role]['node_id'], rune=rune))
        return dict(read_only=True, live_payment_enabled=False, **{role+'_gate':result})


def main():
    try:
        request=json.loads(sys.stdin.read(16385))
        result=pair(Path(sys.argv[1]), request['rune'], request.get('confirmed'), role=request.get('role','btc'))
        print(json.dumps(result))
    except Exception:
        print('{"error":"gate_pairing_unavailable","read_only":true,"live_payment_enabled":false}')
        raise SystemExit(1)

if __name__ == '__main__': main()
