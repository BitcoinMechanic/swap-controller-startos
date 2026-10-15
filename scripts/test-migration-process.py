#!/usr/bin/env python3
"""Real compiled SDK handlers and packaged service processes; synthetic TLS peers.

No daemon binary, real coins, StartOS host or live market is exercised here.
The separate 0100 funded fixture supplies those real daemon/payment checks.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('compiled',type=Path);p.add_argument('swap',type=Path);p.add_argument('controller',type=Path)
args=p.parse_args()
for key in ('compiled','swap','controller'):setattr(args,key,getattr(args,key).resolve())
sys.path[:0]=[str(args.controller),str(args.swap)]
import test_startos_actions as actions
import test_package_process as process

bridge=Path(__file__).with_name('migration-action-fixture.cjs').resolve()
program="""const fs=require('node:fs'),{main}=require(process.argv[1]);
const input=JSON.parse(fs.readFileSync(0,'utf8'));
main(input.request,input.locations).then(v=>process.stdout.write(JSON.stringify(v)))
.catch(e=>{console.error(e);process.exitCode=1})"""

def action(lab,asset,directory,aid=None,input=None,lifecycle=False):
    data=dict(request=dict(disposable_regtest=True,asset=asset,generation=lab.runtime.name,
        directory=str(directory),id=aid,input=input or {},lifecycle=lifecycle),
        locations=dict(modules=str(args.compiled),releases=str(lab.runtime.parent),
                       wrapper=str(args.swap/'fixture_package_service.py')))
    until=time.monotonic()+15
    while True:
        r=subprocess.run(['node','--no-deprecation','-e',program,str(bridge)],input=json.dumps(data),
                         capture_output=True,text=True,timeout=30)
        if r.returncode==0:
            assert not r.stderr,r.stderr
            result=json.loads(r.stdout)
            return result['command'] if lifecycle else result['result']
        if 'worker is updating' not in r.stderr or time.monotonic()>=until:
            raise RuntimeError('synthetic SDK action failed: '+str(aid)+' '+r.stderr)
        time.sleep(.03)

actions.action=action;process.Driver=actions.Driver
for direction in ('forward','reverse'):
    for failed in (False,True):process.run(direction,failed,args.swap,args.controller)
print('PASS: four compiled SDK/service subprocess cases; synthetic TLS only; pause, restart, immutable invoice and single send')
