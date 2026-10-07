"""Saved read-only inspection setup and automatic direct-channel selection.

No payment authority is created here. Preparation still uses the pilot's exact
contract checks and both node-side authorizations. A settled pilot is preserved.
"""
import json
import os
from pathlib import Path
import sys
import time

import forward_pilot as pilot
from controller import load_config, private_load, require, save, Refused
from executor import digest
from live_preflight import Inspector, identity, channel
import lifecycle

FILE = 'swap-inspection.json'


def binding(root, config):
    return dict(pairing_digest=digest(config), restore_epoch=pilot.restore_epoch(root))


def configure(root, request, *, factory=Inspector):
    require(set(request) == {'btcRune', 'xbtRune', 'confirmed'} and request['confirmed'] is True,
            'setup_confirmation_required')
    with lifecycle.locked(root / 'execution'):
        pilot.allowed(root)
        config = load_config(root)
        require(config, 'pair_nodes_first')
        bound = binding(root, config)
        started = time.monotonic()
        for role, network in (('btc', 'bitcoin'), ('xbt', 'xbt')):
            rune = request[role + 'Rune']
            require(type(rune) is str and 0 < len(rune) <= 16384, 'invalid_inspection_credential')
            client = factory(config['nodes'][role], rune)
            identity(client, config['nodes'][role], network)
            require(type(client.call('listpeerchannels').get('channels')) is list, 'invalid_channels')
            require(type(client.call('listfunds').get('outputs')) is list, 'reserve_unavailable')
        require(time.monotonic() - started <= 60 and load_config(root) == config
                and binding(root, config) == bound, 'setup_changed_during_inspection')
        save(root / FILE, dict(schema=1, **bound,
                              credentials={k: request[k + 'Rune'] for k in ('btc', 'xbt')}))
        return dict(setup_saved=True, payment_started=False)


def saved(root):
    require(os.path.lexists(root / FILE), 'inspection_setup_required')
    setup = private_load(root / FILE)
    config = load_config(root)
    require(config and setup.get('schema') == 1 and
            all(setup.get(k) == v for k, v in binding(root, config).items()),
            'inspection_setup_changed_pair_again')
    return setup, config


def candidates(client, amount, incoming, recipient=None):
    rows = client.call('listpeerchannels').get('channels')
    require(type(rows) is list and all(type(row) is dict for row in rows), 'invalid_channels')
    # Evaluate this single observation using the same eligibility rules as prepare.
    class Snapshot:
        def call(self, method):
            return {'channels': rows}
    result = []
    for row in rows:
        scid = row.get('short_channel_id')
        if not isinstance(scid, str) or (recipient is not None and row.get('peer_id') != recipient):
            continue
        try:
            channel(Snapshot(), scid, amount, incoming)
        except Refused:
            continue
        result.append(scid)
    return sorted(set(result))


def inspect(root, request, *, factory=Inspector):
    require(set(request) == {'invoice'}, 'invalid_request')
    require(type(request['invoice']) is str and 0 < len(request['invoice']) <= 16384, 'invalid_invoice')
    pilot.allowed(root)
    setup, config = saved(root)
    started = time.monotonic()
    clients = {k: factory(config['nodes'][k], setup['credentials'][k]) for k in ('btc', 'xbt')}
    for role, network in (('btc', 'bitcoin'), ('xbt', 'xbt')):
        identity(clients[role], config['nodes'][role], network)
    decoded = clients['xbt'].call('decode', string=request['invoice'])
    require(decoded.get('valid') is True and decoded.get('type') == 'bolt11 invoice'
            and decoded.get('currency') == 'xbt' and type(decoded.get('amount_msat')) is int
            and decoded['amount_msat'] == 2000000 and type(decoded.get('payee')) is str,
            'invalid_recipient_invoice')
    incoming = candidates(clients['btc'], 1000000, True)
    outgoing = candidates(clients['xbt'], 2000000, False, decoded['payee'])
    require(time.monotonic() - started <= 60 and saved(root) == (setup, config),
            'setup_changed_during_inspection')
    return dict(incoming_channels=incoming, outgoing_channels=outgoing,
                automatic_selection=len(incoming) == len(outgoing) == 1,
                btc_sats=1000, xbt_sats=2000, payment_started=False)


def prepare(root, request, *, factory=Inspector, repeat=False):
    require(set(request) == {'invoice', 'incomingChannel', 'outgoingChannel'}, 'invalid_request')
    # Check the existing slot before RPCs; never reset a completed/live pilot.
    if not repeat: require(not pilot.directory(root).exists(), 'one_pilot_only')
    setup, config = saved(root)
    found = inspect(root, {'invoice': request['invoice']}, factory=factory)
    selected = {}
    for key, field in (('incomingChannel', 'incoming_channels'), ('outgoingChannel', 'outgoing_channels')):
        value = request[key]
        require(value is None or type(value) is str, 'invalid_request')
        value = (value or '').strip()
        choices = found[field]
        require(bool(choices), 'no_eligible_' + field)
        require(value or len(choices) == 1, 'choose_' + field)
        selected[key] = value or choices[0]
        require(selected[key] in choices, 'selected_channel_unavailable')
    # prepare locks and re-inspects; prevent crossing pairing/setup/restore changes
    # between discovery and its locked contract construction.
    def bound_factory(node, rune):
        require(saved(root) == (setup, config), 'setup_changed_during_inspection')
        return factory(node, rune)
    return pilot.prepare(root, dict(invoice=request['invoice'], **selected,
                         btcRune=setup['credentials']['btc'], xbtRune=setup['credentials']['xbt']),
                         factory=bound_factory, repeat=repeat)


SAFE = {'setup_confirmation_required', 'invalid_inspection_credential',
        'setup_changed_during_inspection', 'inspection_setup_required',
        'inspection_setup_changed_pair_again', 'no_eligible_incoming_channels',
        'no_eligible_outgoing_channels', 'choose_incoming_channels',
        'choose_outgoing_channels', 'selected_channel_unavailable'}


def main():
    os.umask(0o077)
    root, mode = Path(sys.argv[1]), sys.argv[2]
    require(mode in ('configure', 'inspect', 'prepare'), 'invalid_request')
    raw = sys.stdin.read(262145)
    require(len(raw) <= 262144, 'invalid_request')
    request = json.loads(raw)
    require(type(request) is dict, 'invalid_request')
    return {'configure': configure, 'inspect': inspect, 'prepare': prepare}[mode](root, request)


if __name__ == '__main__':
    try:
        print(json.dumps(main()))
    except Exception as error:
        reason = str(error) if isinstance(error, ValueError) and str(error) in SAFE else pilot.safe_error_reason(error)
        print(json.dumps(dict(reason=reason)))
        raise SystemExit(1) from None
