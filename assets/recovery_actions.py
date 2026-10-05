"""StartOS recovery action boundary. Status is local; execution is regtest-only."""
import copy
import json
import os
from pathlib import Path
import re
import sys
import executor
import recovery_workflow as workflow
from controller import private_load,require

JOB=r'[a-z0-9][a-z0-9-]{0,63}'
DIGEST=r'[0-9a-f]{64}'


def job_path(manager,name):
    require(isinstance(name,str) and re.fullmatch(JOB,name),'invalid_job')
    require(manager.is_dir() and not manager.is_symlink(),'invalid_manager')
    require((manager/'jobs').is_dir() and not (manager/'jobs').is_symlink(),'invalid_jobs_directory')
    root=manager/'jobs'/name
    require(root.is_dir() and not root.is_symlink(),'job_not_available')
    return root


def status(manager):
    report=dict(live_payment_enabled=False,recovery_execution_enabled=False,
                restored_block=os.path.lexists(manager/'restored.json'),jobs=[])
    if not manager.exists():return report
    require(manager.is_dir() and not manager.is_symlink(),'invalid_manager')
    jobs=manager/'jobs'
    if not jobs.exists():return report
    require(jobs.is_dir() and not jobs.is_symlink(),'invalid_jobs_directory')
    selected=sorted(jobs.iterdir())
    require(len(selected)<=64,'too_many_jobs')
    for p in selected:
        if not re.fullmatch(JOB,p.name):
            report['jobs'].append(dict(state='unreadable'));continue
        item=dict(job=p.name,state='unreadable')
        try:
            root=job_path(manager,p.name)
            with executor.lock(root):
                intent,state=executor.records(root)
                token=executor.digest(intent)
                require(intent['direction'] in ('forward','reverse') and state['phase'] in executor.PHASES,'invalid_job_state')
                confirmed=[]
                if (root/'recovery-admission.json').exists():
                    record=private_load(root/'recovery-admission.json')
                    require(record.get('digest')==token and record.get('schema')==1,'admission_changed')
                    confirmed=record['confirmed']
                    require(isinstance(confirmed,list) and all(isinstance(n,str) for n in confirmed)
                            and set(confirmed)<=workflow.NETWORKS and len(set(confirmed))==len(confirmed),'invalid_confirmations')
                stage='not_started'
                if (root/'reconciliation.json').exists():
                    receipt=private_load(root/'reconciliation.json')
                    require(receipt.get('digest')==token and receipt.get('stage') in ('observed_pending','resolution_intent','terminal'),
                            'invalid_reconciliation')
                    stage=receipt['stage']
                item=dict(job=p.name,state='recorded',direction=intent['direction'],phase=state['phase'],
                          expected_digest=token,confirmed_networks=sorted(confirmed),reconciliation=stage,
                          fresh_verification_required=True)
        except Exception:pass
        report['jobs'].append(item)
    return report


def action(manager,mode,request):
    require(mode in ('status','confirm','recover'),'invalid_action')
    if mode=='status':
        require(request=={},'status_takes_no_parameters')
        return status(manager)
    # The UI cannot set this flag. Normal StartOS installations stop here,
    # before looking up a job, saving confirmation, or making any RPC call.
    executor.guard()
    require(isinstance(request,dict) and set(request)==
            {'job','expectedDigest','btcRune','xbtRune','confirmed'}|({'network'} if mode=='confirm' else set()),'invalid_request')
    require(request['confirmed'] is True,'explicit_confirmation_required')
    require(isinstance(request['expectedDigest'],str) and re.fullmatch(DIGEST,request['expectedDigest']),'invalid_digest')
    root=job_path(manager,request['job'])
    with executor.lock(root):
        intent,_=executor.records(root)
        require(executor.digest(intent)==request['expectedDigest'],'recovery_digest_mismatch')
        connections=copy.deepcopy(intent['connections'])
    for config in connections:
        rune=request['btcRune' if config['network']=='regtest' else 'xbtRune']
        require(isinstance(rune,str) and 0<len(rune)<=8192 and rune.isascii() and not any(c.isspace() for c in rune),'invalid_rune')
        config['rune']=rune
    if mode=='confirm':
        return workflow.confirm(root,request['expectedDigest'],connections,request['network'],True)
    return workflow.run(root,request['expectedDigest'],connections)


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    os.umask(0o077)
    try:
        require(len(argv)==2,'invalid_arguments')
        raw=sys.stdin.buffer.read(32769)
        require(len(raw)<=32768,'request_too_large')
        result=action(Path(argv[0]),argv[1],json.loads(raw or b'{}'))
    except Exception:
        reason='regtest_only' if len(argv)==2 and argv[1] in ('confirm','recover') and os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')!='1' else 'inspection_required'
        print(json.dumps(dict(event='recovery_action_blocked',reason=reason,details='withheld')));return 1
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
