"""Node-fixture administrator only: revoke original regtest controller runes.

No credentials for blacklistrune are ever given to a controller container.
Revocation does not cancel an RPC already accepted by a coordinator.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
sys.path.insert(0, '/controller-assets')
from controller import private_load, require, save
from https_rpc import Remote

NETWORKS={'regtest','xbt-regtest'}


def guard():
    require(os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
            and os.environ.get('OWNER_FENCE_TEST')=='1','disposable_owner_fence_required')


def rpc(config, method, *args):
    result=subprocess.run([*config['cli'],method,*map(str,args)],capture_output=True,text=True,timeout=15)
    require(result.returncode==0,'fixture_admin_rpc_failed')
    return json.loads(result.stdout)


def refused(config, method):
    # Intentionally bypass the client method filter to test CLN authorization.
    remote=Remote(config)
    request=urllib.request.Request(remote.client.url+'/v1/'+method,data=b'{}',
        headers={'Content-Type':'application/json','Rune':config['rune']})
    try:
        with remote.client.opener.open(request,timeout=10): pass
    except urllib.error.HTTPError as exc:
        code=exc.code;exc.close()
        require(code in (401,403),'old_credential_not_auth_refused')
        return
    raise ValueError('old_credential_accepted')


def check_node(record, local=rpc, reject=refused, factory=Remote):
    original=record['original']
    uid=record['unique_id']
    ranges=local(original,'blacklistrune')['blacklist']
    require(any(r['start']<=uid<=r['end'] for r in ranges),'revocation_missing')
    for config in (original,dict(original,rune=record['derived_rune'])):
        for method in ('getinfo','sendpay'):
            reject(config,method)
    for key in ('recovery','unrelated'):
        info=factory(record[key]).call('getinfo')
        require(info.get('id')==original['node_id'] and info.get('network')==original['network'],
                'remaining_credential_identity_changed')


def fence(records, local=rpc, reject=refused, factory=Remote):
    guard()
    require(isinstance(records,list) and len(records)==2
            and {r['original']['network'] for r in records}==NETWORKS,'two_regtest_nodes_required')
    # Validate BOTH identities and all credential bindings before revoking either.
    for record in records:
        original=record['original'];uid=record['unique_id']
        require(type(uid) is int and uid>=0,'invalid_rune_id')
        require(record['derived_rune']!=original['rune'],'derived_credential_required')
        for key in ('recovery','unrelated'):
            require({k:v for k,v in record[key].items() if k!='rune'}==
                    {k:v for k,v in original.items() if k!='rune'},'credential_binding_changed')
            require(record[key]['rune']!=original['rune'],'distinct_credential_required')
        info=local(original,'getinfo')
        require(info.get('id')==original['node_id'] and info.get('network')==original['network'],
                'coordinator_identity_changed')
    for record in records:
        local(record['original'],'blacklistrune',record['unique_id'],record['unique_id'])
    for record in records: check_node(record,local,reject,factory)
    return dict(old_credentials_revoked=True,derived_credentials_rejected=True,
                recovery_credentials_ready=True,unrelated_credentials_ready=True)


def main():
    guard()
    root=Path('/exchange/control')
    if os.environ.get('DROP_SUBMISSION_REPLY_TEST')=='1':
        original=root/'old-owner'/'jobs'/'swap'
        state=private_load(original/'state.json')
        intent=private_load(original/'intent.json')
        expected='xbt-regtest' if intent['direction']=='forward' else 'regtest'
        require(private_load(original/'submission-reply-lost.json')==dict(network=expected,method='sendpay',reply_discarded=True),
                'discarded_submission_reply_required')
        require(state['phase']=='outgoing_started' and 'preimage' not in state,'original_submission_checkpoint_required')
    report=fence(private_load(root/'fence-nodes.json'))
    save(root/'owner-fence.json',report)
    print('{"coordinator_fence_verified":true}')


if __name__=='__main__':
    try: main()
    except Exception:
        print('{"event":"coordinator_fence_failed","details":"withheld"}')
        raise SystemExit(1) from None
