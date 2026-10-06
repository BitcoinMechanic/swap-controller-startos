"""Host launcher for the funded bidirectional deadline/claim fixture; no live endpoints."""
import argparse
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

spec=importlib.util.spec_from_file_location('separated',Path(__file__).with_name('test-separated-controller.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
STAGES=('step','drop-close-reply')


def command(repo,shared,image,network,name,stage):
    if stage not in STAGES:raise ValueError('invalid_deadline_stage')
    return ['docker','run','--rm','--name',name,'--network',network,'--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=16m',
        '-e','BTC_XBT_DISPOSABLE_CONTAINER=1','-e','PYTHONDONTWRITEBYTECODE=1',
        *base.mount(shared/'control','/controller-state'),*base.mount(repo/'tests/remote','/remote-tests',True),
        '--entrypoint','python3',image,'/remote-tests/deadline_step.py',stage]


def run(repo,results,prefix,backend,btc,controller,direction):
    results=results/direction;results.mkdir()
    shared=results/'exchange';shared.mkdir()
    for folder in ('control','jobs'):(shared/folder).mkdir()
    nodes=results/'nodes';nodes.mkdir()
    suffix=uuid.uuid4().hex[:12];network='deadline-test-'+suffix;node='deadline-nodes-'+suffix;child='deadline-controller-'+suffix
    fixture=None;created=False
    try:
        base.docker('network','create','--internal',network);created=True
        base.docker('run','-d','--name',node,'--network',network,'--init',
            '-e','BTC_XBT_DISPOSABLE_CONTAINER=1','-e','SEPARATE_CONTROLLER=1','-e','PYTHONDONTWRITEBYTECODE=1',
            *base.mount(backend,'/test-bitcoind',True),*base.mount(prefix,'/opt/xbt',True),
            *base.mount(repo.parent/'btc-cln-startos/tests','/pair-fixtures',True),
            *base.mount(repo/'assets','/controller-assets',True),*base.mount(repo/'tests/remote','/remote-tests',True),
            *base.mount(nodes,'/results'),*base.mount(shared,'/exchange'),
            '--entrypoint','/usr/bin/python3',btc,'-c','import time; time.sleep(1800)')
        inspected=json.loads(base.docker('inspect',node))[0]
        address=str(ipaddress.IPv4Address(inspected['NetworkSettings']['Networks'][network]['IPAddress']))
        log=results/'fixture.log';seen=set()
        with log.open('w') as output,log.open() as reader:
            fixture=subprocess.Popen(['docker','exec','-e','COORDINATOR_IP='+address,node,
                '/usr/bin/python3','/remote-tests/image_deadline.py',direction],stdout=output,stderr=subprocess.STDOUT)
            end=time.monotonic()+1200
            while fixture.poll() is None:
                sys.stdout.write(reader.read());sys.stdout.flush()
                if time.monotonic()>end:raise RuntimeError('deadline_fixture_timeout')
                for request in sorted((shared/'jobs').glob('*.request')):
                    if request.name in seen:continue
                    if request.is_symlink() or not re.fullmatch('[0-9a-f]{32}\\.request',request.name) or request.stat().st_size>1024:
                        raise ValueError('invalid_deadline_mailbox')
                    try:job=json.loads(request.read_text())
                    except PermissionError:continue
                    if set(job)!={'stage'} or job['stage'] not in STAGES:raise ValueError('invalid_deadline_job')
                    seen.add(request.name)
                    result=subprocess.run(command(repo,shared,controller,network,child,job['stage']),
                        text=True,capture_output=True,timeout=90)
                    (results/(str(len(seen))+'-'+job['stage']+'.log')).write_text(result.stdout+result.stderr)
                    base.respond(request.with_suffix('.response'),result)
                time.sleep(.05)
            sys.stdout.write(reader.read());sys.stdout.flush()
        if fixture.returncode or not seen:raise RuntimeError('packaged_deadline_fixture_failed; inspect '+str(results))
    finally:
        for name in (child,node):subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
        if fixture is not None:
            try:fixture.wait(timeout=10)
            except subprocess.TimeoutExpired:fixture.kill();fixture.wait()
        if created:subprocess.run(['docker','network','rm',network],capture_output=True,timeout=30)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('btc_image');p.add_argument('xbt_image');p.add_argument('controller_image');p.add_argument('bitcoind',type=Path)
    p.add_argument('mode',choices=('all','normal','lost-reply','reverse','reverse-normal','reverse-lost-reply'),nargs='?',default='all')
    args=p.parse_args();os.umask(0o077)
    repo=Path(__file__).resolve().parents[1];backend=args.bitcoind.resolve()
    if not backend.is_file() or not os.access(backend,os.X_OK):p.error('executable regtest bitcoind required')
    if not (repo.parent/'btc-cln-startos/tests/image_pair.py').is_file():p.error('BTC packaging sibling required')
    btc,xbt,controller=[base.image_id(n) for n in (args.btc_image,args.xbt_image,args.controller_image)]
    # Check the adapter against the module pin baked into this image.
    for test in ('test_deadline_boundary.py','test_funded_deadline.py'):
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','-e','PYTHONPATH=/app',
        *base.mount(repo/'tests','/deadline-tests',True),'--entrypoint','python3',controller,
        '/deadline-tests/'+test,'-v'],check=True)
    results=Path(tempfile.mkdtemp(prefix='cln-deadline-'))
    print('Disposable logs: '+str(results),flush=True)
    print('BTC image: '+btc+'\nXBT image: '+xbt+'\nController image: '+controller,flush=True)
    with tempfile.TemporaryDirectory(prefix='deadline-binaries-') as temporary:
        prefix=Path(temporary);container=base.docker('create','--network','none',xbt)
        try:base.docker('cp',container+':/usr/local/.',str(prefix)+'/')
        finally:base.docker('rm',container)
        modes={'all':('normal','lost-reply','reverse-normal','reverse-lost-reply'),
               'reverse':('reverse-normal','reverse-lost-reply')}.get(args.mode,(args.mode,))
        for mode in modes:
            run(repo,results,prefix,backend,btc,controller,mode)


if __name__=='__main__':main()
