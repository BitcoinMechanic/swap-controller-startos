"""One-shot direct-channel live candidate inspection. Never execution authority.

Separate read-only credentials arrive on stdin, are never saved, and must be
bound to the current monitor pairing. No quote, HTLC or payment is created.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request

from controller import load_config, require, Refused
from read_only_rpc import Client
import live_policy as policy

READS = {'getinfo': (), 'listpeerchannels': (), 'listfunds': (),
         'decode': ('string',), 'listsendpays': ('payment_hash',)}
LIMIT = 1024 * 1024


def restrictions():
    return [['method=' + method for method in sorted(READS)]]


class Inspector:
    """No mutation methods, generic passthrough, redirects, proxies or retries."""
    def __init__(self, node, rune):
        self.client = Client(node['url'], rune, ca_data=node['ca_pem'])

    def call(self, method, **params):
        require(method in READS and set(params) == set(READS[method]), 'preflight_method_refused')
        if method == 'decode':
            require(type(params['string']) is str and 0 < len(params['string']) <= 16384,
                    'invalid_invoice')
        if method == 'listsendpays':
            require(hex32(params['payment_hash']), 'invalid_payment_hash')
        body = json.dumps(params, allow_nan=False).encode()
        request = urllib.request.Request(self.client.url + '/v1/' + method, data=body,
            headers={'Content-Type': 'application/json', 'Rune': self.client.rune}, method='POST')
        try:
            with self.client.opener.open(request, timeout=15) as response:
                raw = response.read(LIMIT + 1)
            require(len(raw) <= LIMIT, 'preflight_response_unavailable')
            result = json.loads(raw)
            require(type(result) is dict and 'error' not in result, 'preflight_response_unavailable')
            return result
        except urllib.error.HTTPError as exc:
            exc.close()
            raise Refused('preflight_rpc_unavailable') from None
        except Exception:
            raise Refused('preflight_rpc_unavailable') from None


def hex32(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def number(value, minimum=0):
    require(policy.integer(value, minimum, 2**53 - 1), 'invalid_numeric_observation')
    return value


def identity(client, node, network):
    info = client.call('getinfo')
    require(info.get('id') == node['node_id'] and info.get('network') == network,
            'operator_identity_mismatch')
    require(not any(k.startswith('warning') for k in info), 'node_warning_present')
    require(policy.integer(info.get('blockheight'), 0, 499999999), 'invalid_block_height')
    return info


def channel(client, channel_id, amount, incoming):
    channels = client.call('listpeerchannels').get('channels')
    require(type(channels) is list and all(type(c) is dict for c in channels), 'invalid_channels')
    matches = [c for c in channels if c.get('short_channel_id') == channel_id]
    require(len(matches) == 1, 'channel_not_unique')
    c = matches[0]
    require(c.get('state') == 'CHANNELD_NORMAL' and c.get('peer_connected') is True,
            'channel_not_ready')
    require(type(c.get('htlcs')) is list and not c['htlcs'], 'channel_has_pending_htlcs')
    require(number(c.get('receivable_msat' if incoming else 'spendable_msat')) >= amount,
            'channel_liquidity_insufficient')
    fee = number(c.get('feerate', {}).get('perkw'), 1)
    dust = number(c.get('dust_limit_msat'))
    # Same conservative current-fee non-anchor success weight as pinned live_pilot.
    require(amount > dust + ((703 * fee + 999) // 1000) * 1000,
            'amount_trimmed_at_current_fee')
    return c


def inspect(root, request, *, factory=Inspector, clock=time.time, loader=load_config, derive_timing=False):
    """Discard observations on pairing changes or age/clock failure; persist nothing."""
    report = dict(read_only=True, live_payment_enabled=False, execution_authorized=False,
                  payment_started=False, preflight_matches=False,
                  scope='direct_channel_candidate_only', policy_digest=policy.digest(),
                  held_htlc_verified=False, deadline_protection_verified=False,
                  reasons=[])
    try:
        require(type(request) is dict and set(request) == {'candidate', 'credentials'}, 'invalid_request')
        c = copy.deepcopy(request['candidate'])
        fields = {'policy_digest', 'profile', 'btc_amount_msat', 'xbt_amount_msat',
                  'invoice', 'incoming_channel', 'outgoing_channel', 'route_delay_blocks',
                  'quote_expires_at', 'incoming_expiry'}
        require(type(c) is dict and set(c) == fields, 'invalid_candidate')
        require(c['policy_digest'] == policy.digest(), 'policy_digest_mismatch')
        require(c['profile'] in (policy.FORWARD, policy.REVERSE), 'unsupported_profile')
        require(type(c['invoice']) is str and 0 < len(c['invoice']) <= 16384, 'invalid_invoice')
        for key in ('incoming_channel', 'outgoing_channel'):
            require(type(c[key]) is str and re.fullmatch(r'[0-9]+x[0-9]+x[0-9]+', c[key]), 'invalid_channel')
        for key in ('btc_amount_msat', 'xbt_amount_msat', 'route_delay_blocks', 'quote_expires_at', 'incoming_expiry'):
            number(c[key])
        credentials = request['credentials']
        require(type(credentials) is dict and set(credentials) == {'btc', 'xbt'}, 'both_inspection_credentials_required')
        config = loader(root)
        require(config is not None, 'pair_nodes_first')
        start = int(clock())
        clients = {role: factory(config['nodes'][role], credentials[role]) for role in ('btc', 'xbt')}
        networks = {'btc': 'bitcoin', 'xbt': 'xbt'}
        # Both identities before either invoice, wallet or channel read.
        infos = {role: identity(clients[role], config['nodes'][role], networks[role]) for role in clients}
        reverse = c['profile'] == policy.REVERSE
        outgoing, incoming = ('btc', 'xbt') if reverse else ('xbt', 'btc')
        if derive_timing:
            # Only the local action adapter selects this mode. Later heights still
            # reduce the remaining margin and can invalidate the candidate.
            c['quote_expires_at'] = start + 120
            c['incoming_expiry'] = infos[incoming]['blockheight'] + (c['route_delay_blocks'] + 174 if reverse else 300)
        amount = c[outgoing + '_amount_msat']
        decoded = clients[outgoing].call('decode', string=c['invoice'])
        require(decoded.get('valid') is True and decoded.get('type') == 'bolt11 invoice'
                and decoded.get('currency') == ('bc' if reverse else 'xbt'), 'invoice_invalid_or_wrong_network')
        require(number(decoded.get('amount_msat'), 1) == amount, 'invoice_amount_mismatch')
        require(hex32(decoded.get('payment_hash')) and hex32(decoded.get('payment_secret')), 'invoice_fields_missing')
        require(type(decoded.get('payee')) is str and re.fullmatch(r'0[23][0-9a-f]{64}', decoded['payee']), 'invalid_payee')
        created = number(decoded.get('created_at'))
        expiry = number(decoded.get('expiry'), 1)
        require(created <= start, 'invoice_from_future')
        final_cltv = number(decoded.get('min_final_cltv_expiry'), 1)
        selected = {}
        for role in ('btc', 'xbt'):
            funds = clients[role].call('listfunds').get('outputs')
            require(type(funds) is list, 'invalid_reserve_observation')
            total = 0
            for output in funds:
                require(type(output) is dict and type(output.get('reserved')) is bool
                        and type(output.get('status')) is str, 'invalid_reserve_observation')
                value = number(output.get('amount_msat'))
                if output['status'] == 'confirmed' and output['reserved'] is False: total += value
            require(total >= 50000000, 'confirmed_unreserved_funds_insufficient')
            selected[role] = channel(clients[role], c['incoming_channel' if role == incoming else 'outgoing_channel'],
                                     c[role + '_amount_msat'], role == incoming)
            attempts = clients[role].call('listsendpays', payment_hash=decoded['payment_hash']).get('payments')
            require(type(attempts) is list and not attempts, 'payment_hash_already_used_or_unknown')
        require(selected[outgoing].get('peer_id') == decoded['payee'], 'direct_recipient_channel_required')
        # Observe again after the potentially slow reads. Use the later incoming height.
        for role in clients:
            latest = identity(clients[role], config['nodes'][role], networks[role])
            require(latest['blockheight'] >= infos[role]['blockheight'], 'chain_height_regressed')
            infos[role] = latest
        now = int(clock())
        numeric = dict(policy_digest=c['policy_digest'], profile=c['profile'],
            btc_amount_msat=c['btc_amount_msat'], xbt_amount_msat=c['xbt_amount_msat'],
            route_hops=1, routing_fee_msat=0, route_delay_blocks=c['route_delay_blocks'],
            recipient_min_final_cltv=final_cltv, quote_expires_at=c['quote_expires_at'],
            recipient_expires_at=created + expiry, observed_at=start,
            btc_height=infos['btc']['blockheight'], xbt_height=infos['xbt']['blockheight'],
            incoming_expiry=c['incoming_expiry'])
        checked = policy.validate(numeric, now=now)
        require(checked['numeric_policy_matches'], 'numeric_policy_rejected')
        require(loader(root) == config, 'pairing_changed_during_inspection')
        report.update(preflight_matches=True, observed_at=now,
            candidate_digest=hashlib.sha256(json.dumps(c, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
            incoming_remaining_blocks=checked['incoming_remaining_blocks'],
            minimum_incoming_remaining_blocks=checked['minimum_incoming_remaining_blocks'],
            current_fee_trim_checks_passed=True, confirmed_reserves_checked=True,
            btc_amount_msat=c['btc_amount_msat'], xbt_amount_msat=c['xbt_amount_msat'],
            route_delay_blocks=c['route_delay_blocks'], quote_expires_at=c['quote_expires_at'],
            proposed_incoming_expiry=c['incoming_expiry'])
    except Refused as exc:
        # All Refused strings above are fixed local codes; factory errors remain private.
        safe = {'invalid_request','invalid_candidate','policy_digest_mismatch','unsupported_profile',
            'invalid_invoice','invalid_channel','invalid_numeric_observation','both_inspection_credentials_required',
            'pair_nodes_first','operator_identity_mismatch','node_warning_present','invalid_block_height',
            'invoice_invalid_or_wrong_network','invoice_amount_mismatch','invoice_fields_missing','invalid_payee',
            'invoice_from_future','invalid_reserve_observation','confirmed_unreserved_funds_insufficient',
            'invalid_channels','channel_not_unique','channel_not_ready','channel_has_pending_htlcs',
            'channel_liquidity_insufficient','amount_trimmed_at_current_fee','payment_hash_already_used_or_unknown',
            'direct_recipient_channel_required','chain_height_regressed','numeric_policy_rejected',
            'pairing_changed_during_inspection','preflight_rpc_unavailable'}
        report['reasons'] = [str(exc) if str(exc) in safe else 'preflight_unavailable']
    except Exception:
        report['reasons'] = ['preflight_unavailable']
    return report


def main():
    try:
        require(len(sys.argv) == 3 and sys.argv[2] == 'inspect', 'invalid_arguments')
        raw = sys.stdin.read(262145)
        require(len(raw) <= 262144, 'request_too_large')
        report = inspect(Path(sys.argv[1]), json.loads(raw))
    except Exception:
        report = dict(read_only=True, preflight_matches=False, live_payment_enabled=False,
                      execution_authorized=False, payment_started=False, reasons=['invalid_request'])
    print(json.dumps(report))
    return 0 if report['preflight_matches'] else 1


if __name__ == '__main__': raise SystemExit(main())
