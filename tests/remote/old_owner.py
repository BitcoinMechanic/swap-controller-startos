"""Retain and run the old executable journal after coordinator revocation."""
import os
from pathlib import Path
import sys
sys.path.insert(0,'/app')
import executor
from controller import private_load, require, save
from owner_fence import guard, refused


def check_old_owner(parent, runner=None, reject=refused):
    guard()
    require(private_load(parent/'owner-fence.json')==dict(old_credentials_revoked=True,
        derived_credentials_rejected=True,recovery_credentials_ready=True,
        unrelated_credentials_ready=True),'both_coordinator_fences_required')
    require(private_load(parent/'fence-restarts.json')=={'networks':['regtest','xbt-regtest']},
            'restart_fence_verification_required')
    root=parent/'old-owner'/'jobs'/'swap'
    require(not (root.parent.parent/'restored.json').exists(),'old_owner_must_not_have_local_restore_barrier')
    require(private_load(root/'state.json')['phase']=='outgoing_started','original_pending_journal_required')
    names=('intent.json','remote.json','state.json','permit.json','launched.json','remote-audit.jsonl')
    before={n:(root/n).read_bytes() for n in names}
    # Authorization denial must be observed explicitly; a network outage is not proof.
    for config in private_load(root/'remote.json'):
        for method in ('getinfo','sendpay'):
            reject(config,method)
    result=executor.step(root,**({'runner':runner} if runner is not None else {}))
    require(result.returncode==1,'old_executor_not_refused')
    require(before=={n:(root/n).read_bytes() for n in names},'old_executor_changed_journal_or_audit')
    report=parent/'old-owner-check.json'
    count=private_load(report)['checks'] if report.exists() else 0
    save(report,dict(checks=count+1,retained_journal_blocked=True))
