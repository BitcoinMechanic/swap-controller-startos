"""Disposable Docker transition; synthetic legacy records, no Lightning payments."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from test_upgrade import fixture, save

IMAGE='sha256:74c6ee1d33cead69464d3178666dedd6594c073f6a06a725232bdd1e1eba99c3'
ENTRY='/usr/local/libexec/swap-controller/package_service.py'
PROGRAM=json.loads((Path(__file__).resolve().parents[2]/'startos/live/legacyProgram.ts').read_text().split('export const legacyProgram = ',1)[1])


def run(args,*,input=None,ok=True):
    p=subprocess.run(args,input=input,text=True,capture_output=True,timeout=30)
    if ok and p.returncode:
        raise AssertionError('disposable command failed: '+p.stdout+p.stderr)
    if not ok:assert p.returncode!=0
    return p.stdout


def container(root,cmd,*,input=None,ok=True,readonly=False):
    return run(['docker','run','--user',str(os.getuid())+':'+str(os.getgid()),'--rm','--network','none','--read-only','--tmpfs','/tmp:rw',
        '-i','-v',str(root)+':/data:'+('ro' if readonly else 'rw'),'--entrypoint',cmd[0],IMAGE,*cmd[1:]],input=input,ok=ok)


def snapshot(root):
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file() and not str(p.relative_to(root)).startswith('mainnet-swaps-v1/')}


def main():
    base=Path(tempfile.mkdtemp(prefix='controller-legacy-',dir=sys.argv[1]))
    root=base/'volume'
    for index in range(1,7):fixture(root,index=index)
    for index in range(7,11):fixture(root,index=index,phase='expired')
    fixture(root,index=11,phase='cancelled')
    for index in range(12,14):fixture(root,index=index,direction='reverse')
    before=snapshot(root)
    check=['python3','-I','-B','-c',PROGRAM,'/data','admit']
    report=json.loads(container(root,check,readonly=True));assert report['admitted'] and len(report['records'])==13
    assert snapshot(root)==before and not (root/'mainnet-swaps-v1').exists()
    # The worker cannot silently initialize itself on first start.
    container(root,[ENTRY,'--mainnet','/data/mainnet-swaps-v1','run'],ok=False)
    assert not (root/'mainnet-swaps-v1').exists()
    container(root,[ENTRY,'--mainnet','/data/mainnet-swaps-v1','init'])
    state=json.loads((root/'mainnet-swaps-v1/state.json').read_text())
    assert state['schema']==2 and state['environment']=='mainnet'
    assert state['jobs']=={} and state['pairings']=={} and all(v is None for v in state['selected'].values())
    container(root,[ENTRY,'--mainnet','/data/mainnet-swaps-v1','init'],ok=False)
    for cycle in range(2):
        cid=run(['docker','run','--user',str(os.getuid())+':'+str(os.getgid()),'-d','--network','none','--read-only','--tmpfs','/tmp:rw',
            '-v',str(root)+':/data:rw','--entrypoint',ENTRY,IMAGE,'--mainnet','/data/mainnet-swaps-v1','run']).strip()
        try:
            deadline=time.monotonic()+15
            while True:
                output=container(root,[ENTRY,'--mainnet','/data/mainnet-swaps-v1','action'],input='{"action":"status"}')
                status=json.loads(output)['result']
                if status['worker_fresh']:break
                assert time.monotonic()<deadline
                time.sleep(.1)
            assert status['swaps']==[]
            state=json.loads((root/'mainnet-swaps-v1/state.json').read_text())
            assert state['jobs']=={} and state['pairings']=={}
            assert snapshot(root)==before
            history=json.loads(container(root,[*check[:-1],'history'],readonly=True))
            assert len(history['records'])==13 and not history['legacy_authority_imported']
        finally:
            run(['docker','stop','-t','10',cid]);run(['docker','rm',cid])
    fenced=base/'restored';shutil.copytree(root,fenced)
    save(fenced,'swap-restored.json',dict(schema=1,format='swap-restore-barrier-v1',blocked=True))
    container(fenced,check,readonly=True,ok=False)
    container(fenced,[ENTRY,'--mainnet','/data/another-fresh-journal','init'],ok=False)
    assert (fenced/'swap-restored.json').exists() and not (fenced/'another-fresh-journal').exists()
    assert snapshot(root)==before
    result=dict(synthetic_legacy_records=13,readonly_admission=True,legacy_bytes_and_barrier_preserved=True,
        implicit_initialization=False,legacy_authority_imported=False,explicit_empty_mainnet_initialization=True,
        worker_start_and_restart=True,restore_fence_refused=True,network_disabled=True,
        lightning_payments=False,live_StartOS_acceptance=False)
    (base/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result));print('Disposable evidence: '+str(base))


if __name__=='__main__':main()
