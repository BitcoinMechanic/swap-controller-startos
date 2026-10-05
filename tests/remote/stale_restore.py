"""Fault-injection only: inspect stale regtest journals without execution authority.

Not a StartOS backup format, recovery importer, or permission to clear a barrier.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, '/app')
import executor
import lifecycle
from controller import private_load, save, require
from execution_rpc import Remote

READS = {'getinfo', 'decode', 'listsendpays', 'listpeerchannels',
         'xbt-quote-status', 'reverse-status'}


class Observer:
    def __init__(self, connection):
        self.remote = Remote(connection)

    def call(self, method, *args):
        require(method in READS, 'inspection_read_only')
        return self.remote.call(method, *args)


def inspect(root, factory=Observer):
    executor.guard()
    require(root.parent.name == 'jobs', 'managed_snapshot_required')
    with executor.lock(root):
        require(private_load(root.parent.parent/'restored.json') == {'blocked': True}, 'restore_barrier_required')
        intent, state = executor.records(root)
        require(state['phase'] in ('prepared', 'outgoing_started'), 'stale_fixture_phase_required')
        direction = intent['direction']
        clients = {c['network']: factory(c) for c in intent['connections']}
        # Verify BOTH coordinators before reading any swap-specific information.
        for config in intent['connections']:
            info = clients[config['network']].call('getinfo')
            require(info.get('id') == config['node_id'] and info.get('network') == config['network'], 'identity_mismatch')
        forward = direction == 'forward'
        outgoing = clients['xbt-regtest' if forward else 'regtest']
        incoming = clients['regtest' if forward else 'xbt-regtest']
        binding = state['btc_binding' if forward else 'xbt_binding']
        invoice = state['xbt_invoice' if forward else 'btc_invoice']
        amount = state['xbt_amount_msat' if forward else 'btc_amount_msat']
        require(len(state['route']) == 1, 'direct_fixture_required')
        require(state.get('quote_gate') is True if forward else state.get('durable_gate') is True, 'durable_gate_required')
        decoded = outgoing.call('decode', invoice)
        expected = dict(valid=True, type='bolt11 invoice', currency='xbtrt' if forward else 'bcrt',
                        payment_hash=state['payment_hash'], amount_msat=amount,
                        payee=state['route'][-1]['id'],
                        payment_secret=state['payment_secret' if forward else 'btc_secret'])
        require(all(decoded.get(k) == v for k,v in expected.items()), 'invoice_binding_mismatch')
        gate = incoming.call('xbt-quote-status' if forward else 'reverse-status', state['payment_hash'])
        require(gate.get('payment_hash') == state['payment_hash'] and gate.get('binding') == binding, 'gate_binding_mismatch')
        if not forward:
            require(gate.get('terms') == state['reverse_quote'] and gate.get('cltv_expiry') == state['xbt_expiry'], 'gate_terms_mismatch')
        payments = [p for p in outgoing.call('listsendpays')['payments'] if p.get('payment_hash') == state['payment_hash']]
        require(len(payments) == 1, 'outgoing_missing_or_ambiguous')
        payment = payments[0]
        expected = dict(amount_msat=amount, amount_sent_msat=state['route'][0]['amount_msat'], destination=state['route'][-1]['id'])
        if not forward: expected['bolt11'] = invoice
        require(all(payment.get(k) == v for k,v in expected.items()), 'outgoing_binding_mismatch')
        status = payment.get('status')
        require(status in ('pending', 'complete', 'failed'), 'unknown_outgoing_status')
        if status == 'complete':
            raw = bytes.fromhex(payment.get('payment_preimage', ''))
            require(len(raw) == 32 and hashlib.sha256(raw).hexdigest() == state['payment_hash'], 'invalid_preimage')
        else:
            require(not payment.get('payment_preimage'), 'inconsistent_preimage')
        require(gate.get('phase') in {'pending': ('held',), 'complete': ('held', 'resolved'), 'failed': ('held', 'failed')}[status], 'inconsistent_gate_outcome')
        if gate['phase'] == 'held':
            channels = [c for c in incoming.call('listpeerchannels')['channels'] if c.get('short_channel_id') == binding[0]]
            require(len(channels) == 1 and channels[0].get('state') == 'CHANNELD_NORMAL', 'incoming_channel_missing')
            htlcs = [h for h in channels[0].get('htlcs', []) if h.get('direction') == 'in' and h.get('id') == binding[1]]
            require(len(htlcs) == 1 and htlcs[0].get('payment_hash') == state['payment_hash']
                    and htlcs[0].get('state') == 'RCVD_ADD_ACK_REVOCATION', 'incoming_htlc_changed')
            if not forward:
                require(htlcs[0].get('amount_msat') == state['xbt_amount_msat'] and htlcs[0].get('expiry') == state['xbt_expiry'], 'incoming_terms_changed')
        return dict(read_only=True, stale_phase=state['phase'], outgoing_status=status,
                    gate_phase=gate['phase'], original_attempt_found=True,
                    restored_block=True, execution_authorized=False)


def capture(source, manager):
    """Synthetic copy, deliberately NOT the package's credential-free backup."""
    require(not manager.exists(), 'snapshot_already_exists')
    lifecycle.setup(manager)
    target = manager/'jobs'/'swap'; target.mkdir(mode=0o700)
    for name in ('intent.json', 'remote.json', 'state.json', 'permit.json', 'launched.json'):
        if (source/name).exists(): save(target/name, private_load(source/name))
    lifecycle.restored(manager)


def check_copies(parent):
    reports = []
    for phase in ('prepared', 'outgoing_started'):
        manager = parent/('stale-'+phase)
        root = manager/'jobs'/'swap'
        before = {p.name: p.read_bytes() for p in root.iterdir() if p.suffix == '.json'}
        for recover_only in (False, True):
            try: executor.step(root, recover_only=recover_only)
            except ValueError as exc: require(str(exc) == 'restored_execution_blocked', 'wrong_restore_refusal')
            else: raise AssertionError('restored journal executed')
        require(lifecycle.tick(manager) == {}, 'restored_worker_executed')
        report = inspect(root)
        require(before == {p.name: p.read_bytes() for p in root.iterdir() if p.suffix == '.json'}, 'inspection_changed_snapshot')
        require(lifecycle.snapshot(manager)['worker_mode'] == 'restored', 'restore_barrier_cleared')
        reports.append(report)
    return reports


def record(parent, reports):
    path = parent/'stale-inspection.json'
    history = private_load(path)['observed_statuses'] if path.exists() else []
    statuses = sorted(set(history) | {r['outgoing_status'] for r in reports})
    save(path, dict(reports=reports, observed_statuses=statuses))
