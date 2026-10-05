"""Regtest-only resolution from a stale record; never submits outgoing payments.

Not installed in /app. No production restore import or ownership transfer.
"""
import copy
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, '/app')
import executor
from controller import private_load, require, save
from execution_rpc import Remote
from stale_restore import READS, inspect_locked

WRITES = {'forward': {'xbt-release', 'xbt-fail'}, 'reverse': {'reverse-release', 'reverse-fail'}}


def allowed(network, direction):
    require(network in ('regtest', 'xbt-regtest') and direction in WRITES, 'regtest_direction_required')
    incoming = 'regtest' if direction == 'forward' else 'xbt-regtest'
    return READS | {'xbt-spend-info'} | (WRITES[direction] if network == incoming else set())


class ResolutionClient:
    def __init__(self, config, direction, audit):
        self.methods = allowed(config['network'], direction)
        self.remote = Remote(config)
        self.audit = audit

    def call(self, method, *args):
        require(method in self.methods, 'resolution_only_method_required')
        result = self.remote.call(method, *args)
        if method in set.union(*WRITES.values()):
            with self.audit.open('a') as stream:
                stream.write(json.dumps(dict(network=self.remote.network, method=method))+'\n')
                stream.flush(); os.fsync(stream.fileno())
        return result


def recovery_factory(intent, connections, audit):
    require(isinstance(connections, list) and len(connections) == 2, 'two_recovery_credentials_required')
    indexed = {c['network']: c for c in connections}
    require(set(indexed) == {'regtest','xbt-regtest'}, 'recovery_networks_required')
    for original in intent['connections']:
        replacement = indexed[original['network']]
        require({k:v for k,v in original.items() if k != 'rune'} ==
                {k:v for k,v in replacement.items() if k != 'rune'}, 'changed_recovery_endpoint_or_identity')
        require(replacement['rune'] != original['rune'], 'separate_resolution_credentials_required')
    return lambda original: ResolutionClient(indexed[original['network']], intent['direction'], audit)


def resolve(root, expected_digest, connections, audit, factory=None):
    executor.guard()
    require(root.parent.name == 'jobs', 'managed_snapshot_required')
    with executor.lock(root):
        intent, _ = executor.records(root)
        require(executor.digest(intent) == expected_digest, 'recovery_digest_mismatch')
        # Rebind only to separately issued credentials for exactly the same nodes.
        selected = recovery_factory(intent, connections, audit)
        report, intent, state, payment, gate, clients = inspect_locked(root, factory or selected)
        require(state['phase'] == 'prepared', 'pre_submission_snapshot_required')
        direction = intent['direction']; forward = direction == 'forward'
        status = report['outgoing_status']
        require({'id','groupid'} <= payment.keys(), 'missing_attempt_identity')
        attempt = {k:payment.get(k, 0) for k in ('id','groupid','partid')}
        require(all(type(v) is int and v >= 0 for v in attempt.values()), 'invalid_attempt_identity')
        receipt_path = root/'reconciliation.json'
        previous = private_load(receipt_path) if receipt_path.exists() else None
        fixed = dict(digest=expected_digest, attempt=attempt, binding=state['btc_binding' if forward else 'xbt_binding'])
        if previous:
            require(all(previous.get(k) == v for k,v in fixed.items()), 'changed_recovery_attempt')
        incoming = clients['regtest' if forward else 'xbt-regtest']
        if forward and gate['phase'] == 'held':
            info = incoming.call('xbt-spend-info', state['payment_hash'])
            expected = dict(payment_hash=state['payment_hash'], binding=state['btc_binding'],
                            xbt_invoice=state['xbt_invoice'], xbt_amount_msat=state['xbt_amount_msat'],
                            btc_amount_msat=100000000, min_cltv_delta=100, max_cltv_delta=2000)
            require(all(info.get(k) == v for k,v in expected.items()), 'forward_fixture_quote_mismatch')
            channels = incoming.call('listpeerchannels')['channels']
            matches = [h for c in channels if c.get('short_channel_id') == state['btc_binding'][0]
                       for h in c.get('htlcs', []) if h.get('direction') == 'in' and h.get('id') == state['btc_binding'][1]]
            require(len(matches) == 1 and matches[0].get('amount_msat') == 100000000
                    and matches[0].get('expiry') == info.get('cltv_expiry'), 'forward_incoming_terms_changed')
        if status == 'pending':
            require(previous is None or previous.get('stage') == 'observed_pending', 'outcome_regressed')
            save(receipt_path, dict(**fixed, stage='observed_pending'))
        else:
            expected_gate = 'resolved' if status == 'complete' else 'failed'
            if previous and previous.get('stage') in ('resolution_intent','terminal'):
                require(previous.get('outgoing_status') == status, 'outcome_changed')
            if gate['phase'] != expected_gate:
                # A lost reply is not permission to repeat a mutation. A fresh
                # process may only reconcile a terminal gate or stop for inspection.
                require(previous is None or previous.get('stage') == 'observed_pending', 'resolution_outcome_unknown')
                if status == 'complete':
                    method = 'xbt-release' if forward else 'reverse-release'
                    args = (payment['payment_preimage'],) if forward else (state['payment_hash'],json.dumps(state['xbt_binding']),payment['payment_preimage'])
                    expected_reply = {'released':1}
                else:
                    method = 'xbt-fail' if forward else 'reverse-fail'
                    args = (state['payment_hash'],json.dumps(fixed['binding']))
                    expected_reply = {'failed':1}
                save(receipt_path, dict(**fixed, stage='resolution_intent', outgoing_status=status))
                require(incoming.call(method,*args) == expected_reply, 'resolution_reply_unknown')
                fresh = incoming.call('xbt-quote-status' if forward else 'reverse-status',state['payment_hash'])
                require(fresh.get('payment_hash') == state['payment_hash'] and fresh.get('binding') == fixed['binding']
                        and fresh.get('phase') == expected_gate, 'resolution_not_confirmed')
                if not forward:
                    require(fresh.get('terms') == state['reverse_quote'] and fresh.get('cltv_expiry') == state['xbt_expiry'], 'resolved_gate_terms_changed')
            save(receipt_path, dict(**fixed, stage='terminal', outgoing_status=status))
        # Compatibility result is private to the existing node/balance fixture.
        # It is reconstructed from stale state and remote evidence; no original
        # executor journal or fixture mirror is consumed by this resolver.
        mirror = copy.deepcopy(state)
        if not forward: mirror.setdefault('btc_payment_metadata',None)
        if status == 'pending':
            mirror['phase']='outgoing_started'
            result=dict(phase='outgoing_started',outcome='pending')
        else:
            mirror['phase']=('btc_released' if forward else 'xbt_released') if status=='complete' else ('btc_failed' if forward else 'xbt_failed')
            if status=='complete': mirror['preimage']=payment['payment_preimage']
            result=executor.terminal_result(direction,mirror)
        return mirror,result
