"""Explicit regtest recovery admission; no administrator RPC authority."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request
import executor
import recovery
from controller import private_load, require, save
from execution_rpc import Remote

NETWORKS={'regtest','xbt-regtest'}


def refused(config, method):
    # Only an authentication refusal counts; malformed params or outages do not.
    remote=Remote(config)
    request=urllib.request.Request(remote.client.url+'/v1/'+method,data=b'{}',
        headers={'Content-Type':'application/json','Rune':config['rune']},method='POST')
    try:
        with remote.client.opener.open(request,timeout=10): pass
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        require(code in (401,403),'credential_not_auth_refused')
        return
    raise ValueError('credential_unexpectedly_authorized')


def verify(original,replacement):
    # This proves the endpoint is reachable with the expected identity before
    # interpreting refusals. It is not a proof of arbitrary rune restrictions.
    info=Remote(replacement).call('getinfo')
    require(info.get('id')==original['node_id'] and info.get('network')==original['network'],
            'recovery_identity_changed')
    refused(original,'getinfo')
    for method in ('sendpay','pay','withdraw','createrune','blacklistrune'):
        refused(replacement,method)


def binding(root,digest,connections):
    executor.guard()
    require(root.parent.name=='jobs','managed_snapshot_required')
    require(private_load(root.parent.parent/'restored.json')=={'blocked':True},'restore_barrier_required')
    intent,_=executor.records(root)
    require(executor.digest(intent)==digest,'recovery_digest_mismatch')
    recovery.recovery_factory(intent,connections,root.parent.parent/'recovery-audit.jsonl')
    fixed=dict(schema=1,digest=digest,credentials_digest=executor.digest(connections))
    path=root/'recovery-admission.json'
    record=private_load(path) if path.exists() else dict(**fixed,confirmed=[])
    require(set(record)==set(fixed)|{'confirmed'} and all(record[k]==v for k,v in fixed.items()),
            'recovery_admission_binding_changed')
    require(isinstance(record['confirmed'],list) and len(set(record['confirmed']))==len(record['confirmed'])
            and set(record['confirmed'])<=NETWORKS,'invalid_confirmations')
    return intent,record,path


def confirm(root,digest,connections,network,acknowledged=False,verifier=verify):
    require(acknowledged is True and network in NETWORKS,'explicit_revocation_confirmation_required')
    with executor.lock(root):
        intent,record,path=binding(root,digest,connections)
        original=next(c for c in intent['connections'] if c['network']==network)
        replacement=next(c for c in connections if c['network']==network)
        verifier(original,replacement)
        updated=dict(record,confirmed=sorted(set(record['confirmed'])|{network}))
        if updated!=record or not path.exists():save(path,updated)
        return dict(regtest_only=True,confirmed_networks=updated['confirmed'],
                    recovery_ready=set(updated['confirmed'])==NETWORKS,outgoing_submission_enabled=False)


def admit(root,digest,connections,verifier=verify):
    with executor.lock(root):
        intent,record,_=binding(root,digest,connections)
        require(set(record['confirmed'])==NETWORKS,'both_revocations_required')
        for original in intent['connections']:
            verifier(original,next(c for c in connections if c['network']==original['network']))
    # Resolver independently locks and verifies the immutable intent and both
    # identities. Admission cannot clear restore barriers or authorize sendpay.


def command(root,digest,credential_file,mode,network=None,confirmed=False):
    require(confirmed is True,'explicit_recovery_confirmation_required')
    connections=private_load(credential_file)
    if mode=='confirm':return confirm(root,digest,connections,network,True)
    require(mode=='run','invalid_recovery_mode')
    admit(root,digest,connections)
    mirror,result=recovery.resolve(root,digest,connections,root.parent.parent/'recovery-audit.jsonl')
    save(root/'recovery-result.json',dict(mirror=mirror,result=result))
    return dict(regtest_only=True,phase=mirror['phase'],restored_block=True,outgoing_submission_enabled=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('confirm','run'))
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--expected-digest',required=True)
    parser.add_argument('--credentials',required=True,type=Path)
    parser.add_argument('--network',choices=sorted(NETWORKS))
    parser.add_argument('--confirm-resolution-only',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    try:report=command(args.root,args.expected_digest,args.credentials,args.mode,args.network,args.confirm_resolution_only)
    except Exception:
        print(json.dumps(dict(event='recovery_admission_blocked',details='withheld')));return 1
    print(json.dumps(report));return 0


if __name__=='__main__':raise SystemExit(main())
