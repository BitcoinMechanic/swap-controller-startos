"""Host launcher for the packaged regtest quote flow; no live endpoints."""
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
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tests/remote'))
from quote_protocol import QUOTE_STAGES, validate_job

spec=importlib.util.spec_from_file_location('separated',Path(__file__).with_name('test-separated-controller.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
STAGES=QUOTE_STAGES


def command(repo,shared,image,network,name,stage):
    if stage not in STAGES:raise ValueError('invalid_quote_stage')
    return ['docker','run','--rm','--name',name,'--network',network,'--read-only','--cap-drop=ALL',
        '--security-opt=no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=16m',
        '-e','BTC_XBT_DISPOSABLE_CONTAINER=1','-e','PYTHONDONTWRITEBYTECODE=1',
        *base.mount(shared/'control'/('verification' if stage=='verify-claim' else '.'),'/controller-state'),*base.mount(repo/'tests/remote','/remote-tests',True),
        '--entrypoint','python3',image,'/remote-tests/quote_step.py',stage]


def absent(name):
    if base.docker('ps','-aq','--filter','name=^/'+name+'$').strip():
        raise RuntimeError('quote_controller_still_present')
    return subprocess.CompletedProcess([],0,'{"controller_absent":true}','')


def run(repo,results,prefix,backend,btc,controller,direction,downtime=False,interrupt=False,failure=False,supervise=False,pending=False,pilot=False):
    if failure and (not downtime or interrupt):raise ValueError("invalid_failure_downtime_mode")
    shared=results/'exchange';shared.mkdir()
    for folder in ('control','jobs'):(shared/folder).mkdir()
    nodes=results/'nodes';nodes.mkdir()
    suffix=uuid.uuid4().hex[:12];network='quote-test-'+suffix;node='quote-nodes-'+suffix;child='quote-controller-'+suffix
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
            fixture=subprocess.Popen(['docker','exec','-e','COORDINATOR_IP='+address,'-e','QUOTE_SETTLED_DOWNTIME='+('1' if downtime else '0'),
                '-e','QUOTE_INTERRUPT_RESOLUTION='+('1' if interrupt else '0'),
                '-e','QUOTE_FAILED_DOWNTIME='+('1' if failure else '0'),
                '-e','QUOTE_SUPERVISED='+('1' if supervise else '0'),
                '-e','QUOTE_PENDING_DOWNTIME='+('1' if pending else '0'),node,
                '/usr/bin/python3','/remote-tests/'+('image_pilot.py' if pilot else 'image_quote.py'),direction],stdout=output,stderr=subprocess.STDOUT)
            end=time.monotonic()+1200
            while fixture.poll() is None:
                sys.stdout.write(reader.read());sys.stdout.flush()
                if time.monotonic()>end:raise RuntimeError('quote_fixture_timeout')
                for request in sorted((shared/'jobs').glob('*.request')):
                    if request.name in seen:continue
                    if request.is_symlink() or not re.fullmatch('[0-9a-f]{32}\\.request',request.name) or request.stat().st_size>1024:
                        raise ValueError('invalid_quote_mailbox')
                    try:job=json.loads(request.read_text())
                    except PermissionError:continue
                    validate_job(job,(*STAGES,'assert-controller-absent'))
                    seen.add(request.name)
                    if job['stage']=='assert-controller-absent':
                        if not downtime:raise ValueError('quote_downtime_not_enabled')
                        result=absent(child)
                    else:
                        result=subprocess.run(command(repo,shared,controller,network,child,job['stage']),
                            text=True,capture_output=True,timeout=90)
                    (results/(str(len(seen))+'-'+job['stage']+'.log')).write_text(result.stdout+result.stderr)
                    base.respond(request.with_suffix('.response'),result)
                time.sleep(.05)
            sys.stdout.write(reader.read());sys.stdout.flush()
        if fixture.returncode or not seen:raise RuntimeError('packaged_quote_fixture_failed; inspect '+str(results))
    finally:
        for name in (child,node):subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
        if fixture is not None:
            try:fixture.wait(timeout=10)
            except subprocess.TimeoutExpired:fixture.kill();fixture.wait()
        if created:subprocess.run(['docker','network','rm',network],capture_output=True,timeout=30)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('btc_image');p.add_argument('xbt_image');p.add_argument('controller_image');p.add_argument('bitcoind',type=Path)
    p.add_argument('direction',choices=('forward','reverse','all'),nargs='?',default='forward')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--settle-during-downtime',action='store_true')
    modes.add_argument('--fail-during-downtime',action='store_true')
    modes.add_argument('--pending-during-downtime',action='store_true')
    p.add_argument('--supervise',action='store_true')
    args=p.parse_args()
    if args.supervise and not (args.settle_during_downtime or args.fail_during_downtime or args.pending_during_downtime):p.error('--supervise requires a downtime scenario')
    os.umask(0o077)
    repo=Path(__file__).resolve().parents[1];backend=args.bitcoind.resolve()
    if not backend.is_file() or not os.access(backend,os.X_OK):p.error('executable regtest bitcoind required')
    if not (repo.parent/'btc-cln-startos/tests/image_pair.py').is_file():p.error('BTC packaging sibling required')
    btc,xbt,controller=[base.image_id(n) for n in (args.btc_image,args.xbt_image,args.controller_image)]
    # Run the policy tests against exactly the module pin baked into this image.
    subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=16m',
        *base.mount(repo.parent/'btc-cln-startos/tests','/btc-tests',True),
        '--entrypoint','/usr/bin/python3',btc,'/btc-tests/test_bound_release.py','-v'],check=True)
    for test in ('test_protection_credentials.py','test_live_policy.py','test_quote_workflow.py','test_quote_actions.py','test_reverse_quote_workflow.py','test_quote_policy.py','test_quote_downtime.py','test_regtest_supervisor.py','test_supervision_arming.py'):
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','-e','PYTHONPATH=/app',
        *base.mount(repo/'tests','/quote-tests',True),'--entrypoint','python3',controller,
        '/quote-tests/'+test,'-v'],check=True)
    results=Path(tempfile.mkdtemp(prefix='cln-quote-flow-'))
    print('Disposable logs: '+str(results),flush=True)
    print('BTC image: '+btc+'\nXBT image: '+xbt+'\nController image: '+controller,flush=True)
    with tempfile.TemporaryDirectory(prefix='quote-binaries-') as temporary:
        prefix=Path(temporary);container=base.docker('create','--network','none',xbt)
        try:base.docker('cp',container+':/usr/local/.',str(prefix)+'/')
        finally:base.docker('rm',container)
        directions=('forward','reverse') if args.direction=='all' else (args.direction,)
        for direction in directions:
            for interrupt in ((False,True) if args.settle_during_downtime else (False,)):
                work=results/(direction+('-interrupted-release' if interrupt else '-normal'));work.mkdir()
                run(repo,work,prefix,backend,btc,controller,direction,
                    args.settle_during_downtime or args.fail_during_downtime or args.pending_during_downtime,
                    interrupt,args.fail_during_downtime,args.supervise or args.pending_during_downtime,args.pending_during_downtime)


if __name__=='__main__':main()
