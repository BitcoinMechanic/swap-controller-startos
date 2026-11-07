"""Local-only diagnostic. Never authorizes migration or calls a coordinator."""
import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import pilot_contract as forward
import reverse_contract as reverse

TERMINAL = {'settled', 'failed', 'cancelled', 'expired'}
PHASES = TERMINAL | {'review', 'authorizing', 'publishing', 'waiting_for_btc',
    'waiting_for_xbt', 'send_intent', 'release_intent', 'fail_intent',
    'retire_intent', 'expiring_preparation', 'close_intent', 'onchain_recovery'}
VOLATILE = {'status.json', 'execution/heartbeat.json',
    'execution/forward-pilot-status.json', 'execution/reverse-pilot-status.json'}
MARKERS = {'legacy_execution_barrier': 'execution/restored.json',
    'legacy_restore_epoch': 'execution/forward-pilot-restore-epoch.json',
    'root_restore_barrier': 'swap-restored.json',
    'root_node_restore_barrier': 'node-restored.json',
    'root_restored_marker': 'restored.json'}
MARKER_NAMES = {'restored.json', 'swap-restored.json', 'node-restored.json',
    'forward-pilot-restore-epoch.json'}


def need(ok):
    if not ok:
        raise ValueError('diagnostic_refused')


def unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result)
        result[key] = value
    return result


class Snapshot:
    def __init__(self, root):
        self.root = Path(root).absolute()
        need(all(not p.is_symlink() for p in (self.root, *self.root.parents)))
        self.files = {}
        self.dirs = {}
        self.total = 0
        self.walk('')

    def walk(self, rel):
        path = self.root / rel
        need(stat.S_ISDIR(path.lstat().st_mode))
        names = sorted(p.name for p in path.iterdir())
        need(len(names) <= 4096 and len(Path(rel).parts) <= 8)
        self.dirs[rel] = names
        for name in names:
            child = str(Path(rel) / name)
            # New service authority is deliberately outside this legacy reader.
            if child in ('mainnet-swaps-v1', 'migrated-swaps-v1'):
                need(stat.S_ISDIR((self.root / child).lstat().st_mode))
                continue
            if child in VOLATILE:
                continue
            mode = (self.root / child).lstat().st_mode
            if stat.S_ISDIR(mode):
                self.walk(child)
            else:
                self.files[child] = self.read(child)
                self.total += len(self.files[child])
                need(self.total <= 32 * 1024**2 and len(self.files) <= 4096)

    def read(self, rel):
        fd = os.open(self.root / rel, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            meta = os.fstat(stream.fileno())
            need(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and meta.st_size <= 1048576)
            data = stream.read(1048577)
            need(len(data) <= 1048576)
            return data

    def json(self, rel):
        return json.loads(self.files[rel], object_pairs_hook=unique,
            parse_constant=lambda _: need(False))

    def stable(self):
        return all(self.read(p) == data for p, data in self.files.items()) and all(
            sorted(p.name for p in (self.root / rel).iterdir()) == names
            for rel, names in self.dirs.items())


def audit(root):
    root = Path(root).absolute()
    need(all(not p.is_symlink() for p in (root, root/'execution', *root.parents)))
    # Open an existing lock read-only; never create files or run legacy status helpers.
    fd = os.open(root/'execution/lifecycle.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        need(stat.S_ISREG(os.fstat(fd).st_mode))
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        snap = Snapshot(root)
        issues = collections.Counter()
        result = dict(diagnostic_version=2, migration_authorized=False,
            scope='Controller local journals only; terminal labels are not independent cross-chain settlement proof.',
            directions={}, issues=issues)
        pairing = snap.json('pairing.json')
        need(type(pairing) is dict and pairing.get('schema') == 1)
        generation = pairing.get('generation')
        need(type(generation) is str and re.fullmatch('[0-9a-f]{32}', generation))
        nodes = {k: pairing['nodes'][k]['node_id'] for k in ('btc','xbt')}
        need(all(type(v) is str and re.fullmatch('0[23][0-9a-f]{64}', v) for v in nodes.values()))
        need(nodes['btc'] != nodes['xbt'])
        marker_review = {}
        current_epoch = None
        epoch_valid = True
        for label, path in MARKERS.items():
            item = dict(present=path in snap.files)
            marker_review[label] = item
            if not item['present']:
                continue
            try:
                marker = snap.json(path)
                if label == 'legacy_restore_epoch':
                    valid = type(marker) is dict and set(marker) == {'epoch'} and forward.hex32(marker['epoch'])
                    epoch_valid = bool(valid)
                    if valid:
                        current_epoch = forward.digest(marker)
                elif label == 'legacy_execution_barrier':
                    valid = type(marker) is dict and set(marker) == {'blocked'} and marker['blocked'] is True
                else:
                    valid = type(marker) is dict and set(marker) == {'schema','format','blocked'} and type(marker['schema']) is int and marker['schema'] == 1 and marker['format'] == 'swap-restore-barrier-v1' and marker['blocked'] is True
                item['recognized_shape'] = bool(valid)
                if type(marker) is dict and type(marker.get('blocked')) is bool:
                    item['blocked'] = marker['blocked']
            except Exception:
                item['recognized_shape'] = False
                if label == 'legacy_restore_epoch':
                    epoch_valid = False
            if not item['recognized_shape']:
                issues['unrecognized_restore_marker'] += 1
        result['restore_marker_review'] = marker_review
        result['other_restore_marker_count'] = sum(p.rsplit('/',1)[-1] in MARKER_NAMES
            and p not in MARKERS.values() for p in snap.files)
        known = set()
        hashes = set()
        for direction, module in (('forward', forward), ('reverse', reverse)):
            prefix = 'execution/' + direction + '-swaps/'
            paths = [p for p in snap.files if p.startswith(prefix) and p.endswith('/record.json')]
            pilot = 'execution/' + direction + '-pilot/record.json'
            if pilot in snap.files:
                paths.append(pilot)
            row = dict(records=len(paths), phases=collections.Counter(), valid_contracts=0,
                matching_pairing=0, with_binding=0, with_close_intent=0,
                terminal_labels=0, published_records=0, restore_epoch_comparisons=collections.Counter())
            for path in paths:
                known.add(path)
                r = snap.json(path)
                need(type(r) is dict)
                saved_epoch = r.get('restore_epoch')
                if not epoch_valid:
                    epoch_class = 'current_epoch_unavailable'
                elif saved_epoch is not None and not forward.hex32(saved_epoch):
                    epoch_class = 'invalid_record_epoch'
                elif saved_epoch == current_epoch:
                    epoch_class = 'matches_current_epoch' if current_epoch is not None else 'both_absent'
                elif saved_epoch is None:
                    epoch_class = 'record_has_no_epoch'
                else:
                    epoch_class = 'different_epoch'
                row['restore_epoch_comparisons'][epoch_class] += 1
                phase = r.get('phase')
                phase = phase if type(phase) is str and phase in PHASES else 'unknown'
                row['phases'][phase] += 1
                row['terminal_labels'] += phase in TERMINAL
                if phase not in TERMINAL:
                    issues['nonterminal_or_unknown_record'] += 1
                try:
                    c = module.validate(r['contract'])
                    need(type(r.get('schema')) is int and r['schema'] == 1)
                    need(r.get('pilot_id') == module.digest(c))
                    need(path == pilot or path == prefix + r['pilot_id'] + '/record.json')
                    row['valid_contracts'] += 1
                    if r.get('generation') == generation and c['nodes'] == nodes:
                        row['matching_pairing'] += 1
                    else:
                        issues['pairing_or_identity_mismatch'] += 1
                    if c['payment_hash'] in hashes:
                        issues['duplicate_payment_hash'] += 1
                    hashes.add(c['payment_hash'])
                except Exception:
                    issues['invalid_contract_or_binding'] += 1
                row['with_binding'] += 'binding' in r
                row['with_close_intent'] += 'close_intent' in r
                row['published_records'] += 'invoice' in r or 'terms' in r
                if 'close_intent' in r:
                    issues['onchain_record_requires_review'] += 1
                if phase in ('settled','failed') and not ('binding' in r and 'expiry' in r):
                    issues['terminal_record_missing_binding'] += 1
                if phase in ('cancelled','expired') and any(k in r for k in ('binding','close_intent','expiry')):
                    issues['retired_record_has_execution_binding'] += 1
            result['directions'][direction] = row
        result['other_record_count'] = sum(p.endswith('/record.json') and p not in known for p in snap.files)
        result['executor_job_count'] = len(snap.dirs.get('execution/jobs', []))
        if result['other_record_count'] or result['executor_job_count']:
            issues['additional_execution_state_needs_review'] += 1
        result['restore_markers_present'] = any(p.rsplit('/',1)[-1] in MARKER_NAMES for p in snap.files)
        result['backup_pause_present'] = 'execution/backup-paused.json' in snap.files
        result['new_runtime_state_present'] = any(os.path.lexists(root/p) for p in ('mainnet-swaps-v1','migrated-swaps-v1'))
        for key in ('restore_markers_present','backup_pause_present','new_runtime_state_present'):
            if result[key]:
                issues[key] += 1
        result['records_unchanged_during_audit'] = snap.stable()
        if not result['records_unchanged_during_audit']:
            issues['records_changed_during_audit'] += 1
        return result
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        need(len(sys.argv) == 2)
        print(json.dumps(audit(sys.argv[1]), indent=2, sort_keys=True))
    except BlockingIOError:
        print('{"migration_authorized":false,"reason":"worker_busy_retry_audit"}')
        sys.exit(2)
    except Exception:
        print('{"migration_authorized":false,"reason":"local_records_unavailable_or_invalid","details":"withheld"}')
        sys.exit(1)
