"""Network-free checks around a real resolver process killed after gate RPC."""
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from controller import private_load,save,require
from partition_check import isolation


def check(parent,direction,filename,stage):
    require(direction in ('forward','reverse') and stage in ('before','after'),'invalid_check')
    require(filename==('swap-state.json' if direction=='forward' else 'reverse-state.json'),'invalid_filename')
    require(not (parent/'execution').exists(),'original_executor_recreated')
    root=parent/'stale-prepared'/'jobs'/'swap'
    require(private_load(root.parent.parent/'restored.json')=={'blocked':True},'restore_barrier_changed')
    require(private_load(root/'state.json')['phase']=='prepared','stale_snapshot_advanced')
    receipt=private_load(root/'reconciliation.json')
    require(receipt['outgoing_status'] in ('complete','failed'),'terminal_outgoing_required')
    release=receipt['outgoing_status']=='complete'
    network='regtest' if direction=='forward' else 'xbt-regtest'
    method=('xbt-' if direction=='forward' else 'reverse-')+('release' if release else 'fail')
    require(private_load(parent/'resolution-reply-lost.json')==dict(network=network,method=method),'wrong_crash_point')
    audit=(parent/'remote-audit.jsonl').read_bytes()
    rows=[json.loads(line) for line in audit.splitlines()]
    require(sum(r['method']=='sendpay' for r in rows)==1,'submission_count_changed')
    writes={'xbt-release','xbt-fail','reverse-release','reverse-fail'}
    require([r for r in rows if r['method'] in writes]==[dict(network=network,method=method)],'resolution_count_changed')
    immutable={n:hashlib.sha256((root/n).read_bytes()).hexdigest()
               for n in ('intent.json','remote.json','state.json','permit.json')}
    fixed={k:receipt[k] for k in ('digest','attempt','binding','outgoing_status')}
    evidence=dict(audit_sha256=hashlib.sha256(audit).hexdigest(),immutable=immutable,fixed=fixed)
    mirror=private_load(parent/filename)
    witness=parent/'resolution-reply-witness.json'
    if stage=='before':
        require(receipt['stage']=='resolution_intent','terminal_checkpoint_written_before_crash')
        require(mirror['phase']=='outgoing_started' and 'preimage' not in mirror,'mirror_advanced_before_crash')
        require(not witness.exists(),'duplicate_reply_loss')
        save(witness,evidence)
    else:
        require(private_load(witness)==evidence,'fresh_recovery_mutated_audit_or_binding')
        require(receipt['stage']=='terminal','fresh_recovery_not_terminal')
        expected=('btc_' if direction=='forward' else 'xbt_')+('released' if release else 'failed')
        require(mirror['phase']==expected,'wrong_terminal_mirror')
    return {'lost_resolution_reply_verified':True}


def main():
    isolation()
    direction,filename,stage=sys.argv[1:]
    print(json.dumps(check(Path('/controller-state'),direction,filename,stage),separators=(',',':')))


if __name__=='__main__':main()
