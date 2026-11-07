"""Admit a stopped legacy controller as retained, non-executable history.

This is not a node-settlement certificate. No old pairing/authority is imported.
The exact old execution barrier remains in place. New authority requires separate
initialization and verified pairing; root restore fences always refuse admission.
"""
import json
from pathlib import Path
import sys
import time
import legacy_audit as audit
import market_terms as mt


class Refused(Exception):
    pass


def need(ok, reason):
    if not ok:
        raise Refused(reason)


def validate(root, mode='admit'):
    need(mode in ('admit','history'), 'invalid_mode')
    snapshot = audit.Snapshot(root)
    report = audit.audit(root)
    allowed_issues = {'restore_markers_present'}
    if mode == 'history':
        allowed_issues.add('new_runtime_state_present')
    need(set(report['issues']) <= allowed_issues, 'legacy_records_need_review')
    need(report['other_restore_marker_count'] == 0, 'unknown_restore_marker')
    markers = report['restore_marker_review']
    for key in ('root_restore_barrier','root_node_restore_barrier','root_restored_marker'):
        need(not markers[key]['present'], 'restored_controller_blocked')
    for key in ('legacy_execution_barrier','legacy_restore_epoch'):
        need(not markers[key]['present'] or markers[key]['recognized_shape'], 'legacy_restore_marker_invalid')
    need(not markers['legacy_restore_epoch']['present'] or markers['legacy_execution_barrier']['present'], 'legacy_restore_marker_incomplete')
    for row in report['directions'].values():
        need(set(row['restore_epoch_comparisons']) <= {'both_absent','matches_current_epoch'}, 'legacy_record_from_other_restore')
    need(set(snapshot.dirs.get('',[])) >= {'pairing.json','execution'}, 'legacy_layout_required')
    permitted_root_dirs = {'execution','mainnet-swaps-v1'} if mode == 'history' else {'execution'}
    for directory in snapshot.dirs:
        need(not directory or directory.split('/')[0] in permitted_root_dirs, 'unknown_legacy_directory')
    execution_names = {'jobs','forward-swaps','reverse-swaps','forward-pilot','reverse-pilot',
        'lifecycle.lock','restored.json','forward-pilot-restore-epoch.json','heartbeat.json',
        'forward-pilot-status.json','reverse-pilot-status.json','history.json','regtest-quote-nodes.json'}
    need(set(snapshot.dirs.get('execution',[])) <= execution_names, 'unknown_execution_state')
    records = []
    for path in snapshot.files:
        if not path.endswith('/record.json'):
            continue
        r = snapshot.json(path)
        direction = 'reverse' if path.startswith('execution/reverse-') else 'forward'
        module = audit.reverse if direction == 'reverse' else audit.forward
        c = module.validate(r['contract'])
        permitted = {'schema','pilot_id','contract','generation','phase','restore_epoch','transport',
            'gate_profile','invoice','terms','binding','expiry','incoming_pin'}
        need(set(r) <= permitted, 'unknown_legacy_record_fields')
        folder = str(Path(path).parent)
        need(set(snapshot.dirs[folder]) <= {'record.json','credentials.json'}, 'unknown_swap_state')
        need(not any(d.startswith(folder+'/') for d in snapshot.dirs), 'nested_swap_state')
        incoming = 'xbt' if direction == 'reverse' else 'btc'
        pins = c['incoming_channels'] if module.routed(c) else [c['channels'][incoming]]
        phase = r['phase']
        has_credentials = folder+'/credentials.json' in snapshot.files
        if has_credentials:
            credentials = snapshot.json(folder+'/credentials.json')
            need(type(credentials) is dict and set(credentials) == {'btc','xbt'} and
                all(type(v) is str and 0 < len(v) <= 32768 for v in credentials.values()), 'invalid_legacy_credentials')
        if phase in ('settled','failed'):
            binding = r.get('binding')
            need(type(binding) is list and len(binding)==2 and type(binding[1]) is int and
                0 <= binding[1] < 2**63 and any(binding[0]==p['short_channel_id'] for p in pins), 'invalid_terminal_binding')
            need(type(r.get('expiry')) is int and 0 < r['expiry'] < 2**32, 'invalid_terminal_expiry')
            need(has_credentials and 'invoice' in r and 'terms' in r, 'incomplete_terminal_record')
            if module.routed(c):
                need(r.get('incoming_pin') in pins and r['incoming_pin']['short_channel_id']==binding[0], 'terminal_funding_changed')
        if phase == 'cancelled':
            need(not has_credentials and not any(k in r for k in ('invoice','terms','binding','expiry','incoming_pin')), 'cancelled_record_has_authority')
        if phase == 'expired':
            need(not any(k in r for k in ('binding','expiry','incoming_pin')), 'expired_record_has_execution')
            expires = c['pricing']['expires_at'] if mt.market(c) else c['admission_until']
            need(expires < time.time(), 'retirement_not_expired')
        if 'invoice' in r or 'terms' in r:
            need(type(r.get('invoice')) is str and 0 < len(r['invoice']) <= 20000 and
                r['invoice'].startswith('lnxbt' if direction == 'reverse' else 'lnbc'), 'invalid_publication')
            t = r.get('terms')
            need(type(t) is dict and t.get('payment_hash')==c['payment_hash'] and
                audit.forward.hex32(t.get('payment_secret')), 'publication_hash_changed')
            for asset, amount in mt.amounts(c).items():
                need(type(t.get(asset+'_amount_msat')) is int and t[asset+'_amount_msat']==amount, 'publication_amount_changed')
            outgoing = 'btc' if direction == 'reverse' else 'xbt'
            need(t.get(outgoing+'_invoice') == c['invoice'], 'publication_invoice_changed')
            need(type(t.get('expires_at')) is int and c['created_at'] < t['expires_at'] <= c['admission_until']+120, 'publication_expiry_changed')
            if mt.market(c):
                need(t['expires_at']==c['pricing']['expires_at'], 'market_publication_expiry_changed')
        records.append(dict(direction=direction,phase=phase))
    # Every entry in each history directory must be an inspected contract directory.
    known_folders = {str(Path(p).parent) for p in snapshot.files if p.endswith('/record.json')}
    for direction in ('forward','reverse'):
        parent = 'execution/'+direction+'-swaps'
        for name in snapshot.dirs.get(parent,[]):
            need(parent+'/'+name in known_folders, 'unrecognized_swap_entry')
        pilot = 'execution/'+direction+'-pilot'
        need(pilot not in snapshot.dirs or pilot in known_folders, 'incomplete_pilot')
    need(snapshot.stable(), 'legacy_state_changed')
    return dict(admitted=mode=='admit', legacy_records_preserved=True, legacy_authority_imported=False,
        legacy_execution_barrier_preserved=markers['legacy_execution_barrier']['present'],
        records=records, settlement_proof_reverified=False)


if __name__ == '__main__':
    try:
        need(len(sys.argv)==3, 'invalid_arguments')
        print(json.dumps(validate(sys.argv[1],sys.argv[2])))
    except Exception as error:
        reason=str(error) if isinstance(error,Refused) else 'legacy_state_unverified'
        print(json.dumps(dict(admitted=False,reason=reason,records_preserved=True)))
        sys.exit(1)
