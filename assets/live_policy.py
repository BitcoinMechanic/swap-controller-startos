"""Pure numeric policy proposal. No RPC, persistence or execution authority.

Gate limits are from PIN. Quote lifetime is a tighter controller proposal.
Passing these checks does not authenticate an invoice, route or held HTLC.
The regtest executor does not consume this proposal; live execution is disabled.
"""
import hashlib
import json
import sys

PIN = '81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe'
POLICY = 'controller-live-policy-proposal-v1'
FORWARD = 'live-pilot-v1'
REVERSE = 'reverse-live-v1'


def policy():
    # Return fresh values: callers cannot mutate the policy for later checks.
    return dict(policy=POLICY, source_commit=PIN, status='proposal_only',
        live_payment_enabled=False, executor_enforcement=False,
        relative_chain_progress_guaranteed=False,
        common=dict(max_active_quotes=1, quote_lifetime_seconds=120,
                    recipient_expiry_headroom_seconds=60, observation_max_age_seconds=120,
                    minimum_confirmed_unreserved_sats_each_node=50000,
                    current_fee_untrimmed_htlc_check_required=True),
        forward=dict(profile=FORWARD, btc_amount_msat=1000000, xbt_amount_msat=2000000,
                     route_hops=1, routing_fee_msat=0, xbt_route_delay_blocks=40,
                     btc_min_remaining_blocks=288, btc_max_remaining_blocks=2016,
                     btc_invoice_cltv_blocks=300, btc_deadline_close_threshold_blocks=72,
                     deadline_protection_integration_required=True),
        reverse=dict(profile=REVERSE, btc_amount_msat=1500000,
                     xbt_min_amount_msat=1000, xbt_max_amount_msat=500000000,
                     xbt_amount_multiple_msat=1000, btc_max_routing_fee_msat=30000,
                     max_route_hops=8, btc_min_route_delay_blocks=40, btc_max_route_delay_blocks=576,
                     recipient_max_final_cltv_blocks=144,
                     timing_model='reverse-timing-candidate-v2',
                     expected_xbt_blocks_per_btc_block=1, btc_submission_slack_blocks=6,
                     xbt_recovery_reserve_blocks=144, xbt_quote_drift_blocks=24,
                     xbt_max_remaining_blocks=2016),
        outstanding=['authenticated_invoice_route_and_htlc_checks',
                     'fresh_identity_bound_node_observations',
                     'liquidity_reserves_and_current_fee_trim_checks',
                     'live_deadline_and_onchain_recovery_integration',
                     'dedicated_execution_credentials', 'restore_barrier_review',
                     'live_executor_integration'])


def digest():
    return hashlib.sha256(json.dumps(policy(), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def review():
    return dict(read_only=True, policy_digest=digest(), **policy())


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def validate(candidate, *, now):
    """Validate numeric candidate inputs only; never return live authorization.

Each height/expiry pair belongs to one chain. Supplied observations are not
authenticated here. After submission, expiry does NOT authorize refund/resend.
"""
    result = dict(read_only=True, policy=POLICY, policy_digest=digest(),
                  numeric_policy_matches=False, live_payment_enabled=False,
                  execution_authorized=False, reasons=[])
    required = {'policy_digest', 'profile', 'btc_amount_msat', 'xbt_amount_msat',
                'routing_fee_msat', 'route_hops', 'route_delay_blocks',
                'recipient_min_final_cltv', 'quote_expires_at', 'recipient_expires_at',
                'observed_at', 'btc_height', 'xbt_height', 'incoming_expiry'}
    if type(candidate) is not dict or set(candidate) != required:
        result['reasons'] = ['invalid_candidate_schema']; return result
    c = candidate
    if not integer(now, 0, 2**53 - 1) or any(
            not integer(c[key], 0, 2**53 - 1) for key in required - {'policy_digest', 'profile'}):
        result['reasons'] = ['invalid_integer']; return result
    reasons = result['reasons']
    if c['policy_digest'] != digest(): reasons.append('policy_digest_mismatch')
    if c['profile'] not in (FORWARD, REVERSE):
        reasons.append('unsupported_profile'); return result
    if not 0 <= now - c['observed_at'] <= 120: reasons.append('observation_not_fresh')
    if not now < c['quote_expires_at'] <= now + 120: reasons.append('quote_lifetime_outside_policy')
    if c['recipient_expires_at'] - c['quote_expires_at'] < 60:
        reasons.append('recipient_expiry_headroom_insufficient')
    if any(not integer(c[key], 0, 499999999) for key in ('btc_height', 'xbt_height', 'incoming_expiry')):
        reasons.append('invalid_block_height')
    if c['profile'] == FORWARD:
        if (c['btc_amount_msat'], c['xbt_amount_msat']) != (1000000, 2000000):
            reasons.append('forward_amounts_outside_profile')
        if (c['route_hops'], c['route_delay_blocks'], c['routing_fee_msat']) != (1, 40, 0):
            reasons.append('forward_direct_route_required')
        if not 1 <= c['recipient_min_final_cltv'] <= 40: reasons.append('recipient_cltv_outside_policy')
        remaining = c['incoming_expiry'] - c['btc_height']
        minimum, invoice_cltv = 288, 300
    else:
        if (c['btc_amount_msat'] != 1500000 or not 1000 <= c['xbt_amount_msat'] <= 500000000
                or c['xbt_amount_msat'] % 1000): reasons.append('reverse_amounts_outside_profile')
        if not 0 <= c['routing_fee_msat'] <= 30000: reasons.append('reverse_fee_cap_exceeded')
        if not 1 <= c['route_hops'] <= 8 or not 40 <= c['route_delay_blocks'] <= 576:
            reasons.append('reverse_route_outside_policy')
        if c['route_hops'] == 1 and c['routing_fee_msat'] != 0:
            reasons.append('direct_route_has_routing_fee')
        if not 1 <= c['recipient_min_final_cltv'] <= min(144, c['route_delay_blocks']):
            reasons.append('recipient_cltv_outside_policy')
        remaining = c['incoming_expiry'] - c['xbt_height']
        minimum = c['route_delay_blocks'] + 6 + 144
        invoice_cltv = minimum + 24
    if not minimum <= remaining <= 2016: reasons.append('incoming_cltv_outside_policy')
    result.update(numeric_policy_matches=not reasons, incoming_remaining_blocks=remaining,
                  minimum_incoming_remaining_blocks=minimum, proposed_invoice_cltv_blocks=invoice_cltv)
    return result


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv != ['review']:
        print('{"error":"invalid_arguments","live_payment_enabled":false}')
        return 1
    print(json.dumps(review()))
    return 0


if __name__ == '__main__': raise SystemExit(main())
