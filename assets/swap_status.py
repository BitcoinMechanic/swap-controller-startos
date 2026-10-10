"""Read-only operator summary. Never plan, enroll, publish or advance a swap."""
import json
import os
from pathlib import Path
import sys
import time

import controller
import forward_swaps
import reverse_swaps
import swap_setup
import market_terms as mt
from reverse_contract import GRANT_LIMITS


def whole(value, minimum=0, maximum=2**53-1):
    return type(value) is int and minimum <= value <= maximum


def grant_row(row, node, token, role, reverse):
    """Validate identity and bounds before exporting only non-sensitive fields."""
    expected = json.loads(token)
    controller.require(type(row) is dict and row.get('session_id') == expected['session_id']
        and row.get('node_id') == node['node_id']
        and row.get('network') == {'btc': 'bitcoin', 'xbt': 'xbt'}[role], 'invalid_grant_status')
    controller.require(all(type(row.get(k)) is bool for k in ('current', 'paused', 'gate_ready'))
        and type(row.get('routed', False)) is bool and whole(row.get('remaining'), maximum=10)
        and whole(row.get('expires_at')), 'invalid_grant_status')
    routed = row.get('routed', False)
    if reverse:
        cap = GRANT_LIMITS.get(row.get('profile'))
        controller.require(routed and cap is not None
            and type(row.get('max_delay_blocks', cap)) is int
            and row.get('max_delay_blocks', cap) == cap, 'invalid_grant_status')
    else:
        cap = 80 if routed else 40
    extra={}
    if row.get('market_limits') is not None:
        caps=mt.limits(row['market_limits']);reserved=row.get('market_reserved',{})
        controller.require(type(reserved) is dict and len(reserved)<=10,'invalid_grant_status')
        controller.require(all(type(v) is dict and set(v)=={'btc','xbt'} and all(whole(v[k],maximum=mt.CAPS[k]) for k in v) for v in reserved.values()),'invalid_grant_status')
        extra['market_priced']=True
        for k in ('btc','xbt'):
            remaining=caps['total_'+k+'_msat']-sum(v[k] for v in reserved.values())
            controller.require(remaining>=0,'invalid_grant_status')
            extra['max_'+k+'_sats']=caps['max_'+k+'_msat']//1000
            extra['remaining_'+k+'_msat']=remaining
    return dict(state='available',**extra, **{k: row[k] for k in ('remaining', 'expires_at', 'paused', 'current', 'gate_ready')},
                routed=routed, max_delay_blocks=cap, max_hops=4 if routed else 1,
                max_fee_msat=10000 if routed else 0)


def records(root, module):
    try:
        rows = module.status(root)['swaps']
        return dict(active=sum(r['phase'] not in module.TERMINAL for r in rows),
                    needs_attention=any(r.get('needs_attention') or r.get('restore_blocked') for r in rows))
    except Exception:
        return dict(active=0, needs_attention=True)


def summary(root, *, factories=None, now=None):
    started = time.monotonic()
    now = int(time.time()) if now is None else now
    factories = factories or {'forward': forward_swaps.SessionRemote, 'reverse': reverse_swaps.SessionRemote}
    execution = root / 'execution'
    result = dict(read_only=True, checked_at=now, pairing='unavailable', inspection='unavailable',
                  worker='unavailable', restore_barrier_present=os.path.lexists(execution/'restored.json'),
                  backup_paused=os.path.lexists(execution/'backup-paused.json'), directions={})
    try:
        config = controller.load_config(root)
        binding = swap_setup.binding(root, config) if config else None
        connection = controller.status(root, now)
        result['pairing'] = 'ready' if connection.get('ready') is True else 'waiting' if config else 'missing'
    except Exception:
        config = binding = None
    try:
        heartbeat = controller.private_load(execution/'heartbeat.json')
        result['worker'] = 'fresh' if whole(heartbeat.get('checked_at')) and 0 <= now-heartbeat['checked_at'] <= 30 else 'stale'
    except FileNotFoundError:
        result['worker'] = 'waiting'
    except Exception:
        pass
    try:
        setup, setup_config = swap_setup.saved(root)
        result['inspection'] = 'saved' if setup_config == config and all(type(setup['credentials'][k]) is str and setup['credentials'][k] for k in ('btc','xbt')) else 'changed'
    except FileNotFoundError:
        result['inspection'] = 'missing'
    except ValueError as error:
        result['inspection'] = {'inspection_setup_required':'missing', 'inspection_setup_changed_pair_again':'changed'}.get(str(error),'unavailable')
    except Exception:
        pass
    history = {name: records(root, module) for name, module in (('forward', forward_swaps), ('reverse', reverse_swaps))}
    legacy = execution/'forward-pilot'/'record.json'
    result['legacy_pilot_present'] = os.path.lexists(legacy)
    legacy_pending = False
    if result['legacy_pilot_present']:
        try: legacy_pending = controller.private_load(legacy).get('phase') not in ('settled','failed')
        except Exception: legacy_pending = True
    common = []
    if result['pairing'] != 'ready': common.append('connections_need_attention')
    if result['inspection'] != 'saved': common.append('inspection_setup_required')
    if result['worker'] != 'fresh': common.append('worker_not_fresh')
    if result['backup_paused']: common.append('backup_in_progress')
    if any(r['active'] for r in history.values()): common.append('finish_current_swap_first')
    if any(r['needs_attention'] for r in history.values()): common.append('records_need_attention')
    if legacy_pending: common.append('legacy_pilot_unfinished')
    for name, module in (('forward', forward_swaps), ('reverse', reverse_swaps)):
        direction = dict(state='blocked', blockers=list(common), nodes={}, **history[name])
        result['directions'][name] = direction
        if config is None:
            direction['blockers'].append('pair_nodes_first')
            continue
        try:
            saved, saved_config = module.credentials(root)
            controller.require(saved_config == config, 'repeat_pairing_changed')
        except ValueError as error:
            direction['blockers'].append('grants_not_paired' if str(error) == 'repeat_setup_required' else 'grant_pairing_changed')
            continue
        except Exception:
            direction['blockers'].append('grant_pairing_unavailable')
            continue
        for role in ('btc','xbt'):
            try:
                token = saved['credentials'][role]
                row = factories[name](config['nodes'][role], token).request('info')
                clean = grant_row(row, config['nodes'][role], token, role, name == 'reverse')
                direction['nodes'][role] = clean
                reasons = []
                if not clean['current']: reasons.append('grant_replaced')
                if clean['paused']: reasons.append('grant_paused')
                if not clean['gate_ready']: reasons.append('gate_not_ready')
                if clean['remaining'] == 0: reasons.append('grant_exhausted')
                if clean.get('market_priced') and any(clean['remaining_'+k+'_msat']<1000 for k in ('btc','xbt')): reasons.append('market_budget_exhausted')
                if clean['expires_at'] <= now: reasons.append('grant_expired')
                clean['blockers'] = reasons
                if reasons: direction['blockers'].append(role+'_grant_blocked')
            except Exception:
                direction['nodes'][role] = dict(state='unavailable')
                direction['blockers'].append(role+'_grant_unavailable')
        rows = list(direction['nodes'].values())
        if all(r['state']=='available' for r in rows):
            if rows[0].get('market_priced',False) != rows[1].get('market_priced',False): direction['blockers'].append('market_grant_mismatch')
            if rows[0]['routed'] != rows[1]['routed']: direction['blockers'].append('grant_modes_differ')
            if rows[0]['max_delay_blocks'] != rows[1]['max_delay_blocks']: direction['blockers'].append('grant_timing_limits_differ')
        # Re-check the saved credential pair as well as the coordinator pairing.
        try: controller.require(module.credentials(root) == (saved, config), 'changed')
        except Exception:
            direction['nodes'] = {}
            direction['blockers'].append('configuration_changed')
        if not direction['blockers']: direction['state'] = 'ready_to_prepare'
    try:
        unchanged = controller.load_config(root) == config and (not config or swap_setup.binding(root, config) == binding)
        unchanged = unchanged and time.monotonic()-started <= 60
    except Exception:
        unchanged = False
    if not unchanged:
        for direction in result['directions'].values():
            direction.update(state='blocked', nodes={})
            direction['blockers'].append('configuration_changed')
    return result


if __name__ == '__main__':
    try:
        controller.require(len(sys.argv) == 2, 'invalid_arguments')
        print(json.dumps(summary(Path(sys.argv[1]))))
    except Exception:
        print(json.dumps(dict(reason='status_unavailable')))
        raise SystemExit(1) from None
