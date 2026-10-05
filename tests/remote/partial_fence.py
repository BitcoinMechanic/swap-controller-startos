"""Node-fixture-only loss of the second coordinator's administration socket."""
import os
from pathlib import Path
import stat
import sys
sys.path.insert(0,'/controller-assets')
from controller import private_load, require, save
import owner_fence

ROOT=Path('/exchange/control')


def socket_path(config):
    require(config['network'] in owner_fence.NETWORKS,'regtest_required')
    dirs=[a.split('=',1)[1] for a in config['cli'] if a.startswith('--lightning-dir=')]
    require(len(dirs)==1,'one_fixture_directory_required')
    directory=Path(dirs[0]).resolve()
    require(directory.is_relative_to('/results'),'disposable_node_directory_required')
    path=directory/config['network']/'lightning-rpc'
    require(not path.is_symlink() and stat.S_ISSOCK(path.stat().st_mode),'fixture_socket_required')
    return path


def disconnect(path):
    hidden=path.with_name('lightning-rpc.partial-fence')
    require(not hidden.exists(),'socket_already_hidden')
    inode=path.stat().st_ino
    path.rename(hidden)
    return dict(path=str(path),hidden=str(hidden),inode=inode)


def reconnect(record):
    path=Path(record['path']);hidden=Path(record['hidden'])
    require(path.is_absolute() and path.parent.resolve().is_relative_to('/results')
            and hidden==path.with_name('lightning-rpc.partial-fence'),'fixture_socket_path_changed')
    require(not path.exists() and not path.is_symlink(),'socket_path_replaced')
    require(not hidden.is_symlink() and stat.S_ISSOCK(hidden.stat().st_mode)
            and hidden.stat().st_ino==record['inode'],'hidden_socket_changed')
    hidden.rename(path)


def begin(root=ROOT, local=owner_fence.rpc, reject=owner_fence.refused,
          factory=owner_fence.Remote, locate=socket_path, disconnect_socket=disconnect):
    owner_fence.guard()
    require(not (root/'owner-fence.json').exists() and not (root/'partial-fence.json').exists(),'fresh_fence_required')
    records=private_load(root/'fence-nodes.json')
    require(len(records)==2,'two_nodes_required')
    second=records[1];first=records[0];interrupted=[]
    def interrupted_rpc(config,method,*args):
        if method=='blacklistrune' and args and config['network']==second['original']['network']:
            require(not interrupted,'second_revocation_repeated')
            # This hook runs after fence() checked both identities and revoked first.
            record=disconnect_socket(locate(config));interrupted.append(record)
            save(root/'partial-socket.json',record)
            # Exercise the actual failed local CLI call against the absent socket.
            try: local(config,method,*args)
            except (OSError,ValueError): raise InterruptedError('second_admin_socket_unavailable') from None
            raise AssertionError('unavailable_admin_socket_accepted_rpc')
        return local(config,method,*args)
    # CLNRest needs the RPC socket; check only while it is available.
    info=factory(second['original']).call('getinfo')
    require(info.get('id')==second['original']['node_id'] and info.get('network')==second['original']['network'],
            'second_old_credential_not_available')
    try: owner_fence.fence(records,interrupted_rpc,reject,factory)
    except InterruptedError: pass
    else: raise AssertionError('partial_fence_unexpectedly_completed')
    require(len(interrupted)==1 and not (root/'owner-fence.json').exists(),'partial_fence_evidence_missing')
    owner_fence.check_node(first,local,reject,factory)
    save(root/'partial-fence.json',dict(first_revoked=True,second_admin_unavailable=True,
                                     second_old_credential_active_before_outage=True,replacement_authorized=False))


def resume(root=ROOT, local=owner_fence.rpc, restore=reconnect, factory=owner_fence.Remote):
    owner_fence.guard()
    require(private_load(root/'partial-recovery-blocked.json')=={'replacement_blocked':True,'journal_and_audit_unchanged':True},
            'blocked_replacement_check_required')
    require(private_load(root/'partial-fence.json')==dict(first_revoked=True,second_admin_unavailable=True,
        second_old_credential_active_before_outage=True,replacement_authorized=False),'partial_fence_record_required')
    require(not (root/'owner-fence.json').exists(),'premature_fence_completion')
    restore(private_load(root/'partial-socket.json'))
    records=private_load(root/'fence-nodes.json')
    for index,record in enumerate(records):
        ranges=local(record['original'],'blacklistrune')['blacklist']
        revoked=any(r['start']<=record['unique_id']<=r['end'] for r in ranges)
        require(revoked==(index==0),'partial_revocation_state_changed')
    second=records[1]
    # CLNRest needs the RPC socket; check only while it is available.
    info=factory(second['original']).call('getinfo')
    require(info.get('id')==second['original']['node_id'] and info.get('network')==second['original']['network'],
            'second_old_credential_not_available')
    save(root/'partial-admin-restored.json',{'restored':True})


def main():
    require(len(sys.argv)==2 and sys.argv[1] in ('begin','resume'),'fixed_fixture_stage_required')
    (begin if sys.argv[1]=='begin' else resume)()
    print('{"partial_fence_stage_verified":true}')


if __name__=='__main__':
    try: main()
    except Exception as exc:
        import json
        import traceback
        print(json.dumps({
            "event": "partial_fence_test_failed",
            "error_type": type(exc).__name__,
            "frames": [
                {"file": Path(f.filename).name, "line": f.lineno}
                for f in traceback.extract_tb(exc.__traceback__)
            ],
        }))
        raise SystemExit(1) from None
