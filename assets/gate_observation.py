"""Identity- and generation-bound, read-only BTC gate observation."""
import json
from pathlib import Path
import sys
import time
from controller import load_config, private_load, save, locked, require, NETWORKS
from read_only_rpc import Client

RECORD = 'btc-gate-observation.json'
PROFILE = 'live-pilot-v1'

class GateClient(Client):
    methods = ('getinfo', 'xbt-pilot-info')


def check(node, rune, factory=GateClient):
    client = factory(node['url'], rune, ca_data=node['ca_pem'])
    info = client.call('getinfo')
    require(info.get('id') == node['node_id'] and info.get('network') == 'bitcoin', 'identity_mismatch')
    require(not any(k.startswith('warning') and bool(v) for k,v in info.items()), 'node_warning')
    gate = client.call('xbt-pilot-info')
    require(gate.get('profile') == PROFILE, 'unsupported_gate_profile')
    count = gate.get('registered_quotes')
    require(type(count) is int and count >= 0, 'invalid_quote_count')
    return dict(observation='verified', profile=PROFILE, registered_quotes=count)


def read_record(root, config):
    record = private_load(root/RECORD)
    require(set(record) == {'schema','generation','node_id','rune'} and record['schema'] == 1,
            'invalid_gate_observation_record')
    require(record['generation'] == config['generation'] and record['node_id'] == config['nodes']['btc']['node_id'],
            'gate_pairing_changed')
    return record


def observe(root, config, factory=GateClient):
    try:
        record = read_record(root, config)
        result = check(config['nodes']['btc'], record['rune'], factory)
        require(read_record(root, config) == record, 'gate_credential_changed')
        return result
    except FileNotFoundError:
        return dict(observation='not_paired')
    except Exception:
        return dict(observation='unavailable')


def pair(root, rune, confirmed, factory=GateClient, monitor_factory=Client, clock=time.monotonic):
    require(confirmed is True, 'confirmation_required')
    with locked(root):
        config = load_config(root)
        require(config is not None, 'pair_nodes_first')
        start = clock()
        # Verify both existing monitor identities before storing any new credential.
        for role, network in NETWORKS.items():
            node = config['nodes'][role]
            info = monitor_factory(node['url'], node['rune'], ca_data=node['ca_pem']).call('getinfo')
            require(info.get('id') == node['node_id'] and info.get('network') == network, 'identity_mismatch')
        result = check(config['nodes']['btc'], rune, factory)
        require(load_config(root) == config and 0 <= clock()-start <= 120, 'pairing_changed_or_expired')
        save(root/RECORD, dict(schema=1, generation=config['generation'], node_id=config['nodes']['btc']['node_id'], rune=rune))
        return dict(read_only=True, btc_gate=result, live_payment_enabled=False)


def main():
    try:
        request=json.loads(sys.stdin.read(16385))
        result=pair(Path(sys.argv[1]), request['rune'], request.get('confirmed'))
        print(json.dumps(result))
    except Exception:
        print('{"error":"gate_pairing_unavailable","read_only":true,"live_payment_enabled":false}')
        raise SystemExit(1)

if __name__ == '__main__': main()
