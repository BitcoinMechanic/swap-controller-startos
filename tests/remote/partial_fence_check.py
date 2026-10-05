"""Fresh controller fixture: no recovery authority after a partial revocation."""
import os
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from controller import private_load,require,save
from owner_fence import guard
from partition_check import isolation
import package_step


def snapshot(parent):
    return {str(p.relative_to(parent)):p.read_bytes() for p in parent.rglob('*')
            if p.is_file() and (p.suffix in ('.json','.jsonl') or p.name.endswith('.lock'))}


def check(parent,direction,filename,recover=package_step.recover_lost):
    guard()
    require(direction in ('forward','reverse') and filename==('swap-state.json' if direction=='forward' else 'reverse-state.json'),
            'fixed_state_filename_required')
    require(not (parent/'owner-fence.json').exists(),'full_fence_already_present')
    require(private_load(parent/'partial-fence.json')==dict(first_revoked=True,second_admin_unavailable=True,
        second_old_credential_active_before_outage=True,replacement_authorized=False),'partial_fence_evidence_required')
    before=snapshot(parent)
    for _ in range(2):
        try: recover(parent/filename,direction)
        except FileNotFoundError as exc:
            require(Path(exc.filename)==parent/'owner-fence.json','unexpected_missing_record')
        else: raise AssertionError('replacement_not_blocked')
        require(snapshot(parent)==before,'partial_recovery_changed_journal_or_audit')
    save(parent/'partial-recovery-blocked.json',dict(replacement_blocked=True,journal_and_audit_unchanged=True))


def packaged_check(parent):
    import recovery_workflow as workflow
    root=parent/'stale-prepared'/'jobs'/'swap'
    token=private_load(parent/'lost-journal.json')['digest']
    connections=private_load(parent/'remote-recovery.json')
    records=private_load(parent/'fence-nodes.json')
    first,second=[r['original']['network'] for r in records]
    workflow.confirm(root,token,connections,first,True)
    before=snapshot(parent)
    try:workflow.confirm(root,token,connections,second,True)
    except Exception:pass
    else:raise AssertionError('offline_second_confirmation_accepted')
    for _ in range(2):
        try:workflow.admit(root,token,connections)
        except ValueError as exc:require(str(exc)=='both_revocations_required','unexpected_admission_failure')
        else:raise AssertionError('partial_admission_accepted')
        require(snapshot(parent)==before,'partial_admission_changed_records')
    require(private_load(root/'recovery-admission.json')['confirmed']==[first],'first_confirmation_not_preserved')


if __name__=='__main__':
    isolation()
    direction,filename=sys.argv[1:]
    check(Path('/controller-state'),direction,filename)
    packaged_check(Path('/controller-state'))
    print('{"partial_recovery_blocked":true}')
