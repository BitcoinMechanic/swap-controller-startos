"""Validate the candidate pilot in disposable regtest containers only."""
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
spec=importlib.util.spec_from_file_location('quote_launcher',Path(__file__).with_name('test-quote-flow.py'))
quote=importlib.util.module_from_spec(spec);spec.loader.exec_module(quote)
base=quote.base
old_command=quote.command

def command(*args,**kwargs):
    cmd=old_command(*args,**kwargs)
    return ['/remote-tests/pilot_step.py' if s=='/remote-tests/quote_step.py' else s for s in cmd]
quote.command=command

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('btc_image','xbt_image','controller_image'):parser.add_argument(name)
    parser.add_argument('bitcoind',type=Path);parser.add_argument('--repeat',action='store_true');args=parser.parse_args()
    if args.repeat:quote.STAGES=(*quote.STAGES,'repeat-setup','repeat-prepare','repeat-approve','repeat-worker')
    repo=Path(__file__).resolve().parents[1];backend=args.bitcoind.resolve();os.umask(0o077)
    if not backend.is_file() or not os.access(backend,os.X_OK):parser.error('executable regtest bitcoind required')
    btc,xbt,controller=[base.image_id(n) for n in (args.btc_image,args.xbt_image,args.controller_image)]
    subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
        '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','-e','PYTHONPATH=/app',
        *base.mount(repo/'tests','/pilot-tests',True),*base.mount(repo.parent/'btc-cln-startos/assets/swaps','/node-assets',True),
        '--entrypoint','python3',controller,'/pilot-tests/test_forward_pilot.py','-v'],check=True)
    if args.repeat:
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','-e','PYTHONPATH=/app',
            *base.mount(repo/'tests','/pilot-tests',True),*base.mount(repo.parent/'btc-cln-startos/assets/swaps','/node-assets',True),
            '--entrypoint','python3',controller,'/pilot-tests/test_repeat_swaps.py','-v'],check=True)
        subprocess.run(['docker','run','--rm','--network','none','--read-only','--cap-drop=ALL',
            '--tmpfs','/tmp:rw,nosuid,nodev,size=16m','-e','BTC_GATE_TEST_SOURCE=/usr/local/libexec/cln-swap/quote_plugin.py',
            *base.mount(repo.parent/'btc-cln-startos/tests','/gate-tests',True),
            '--entrypoint','python3',btc,'/gate-tests/test_repeat_gate.py','-v'],check=True)
    results=Path(tempfile.mkdtemp(prefix='cln-forward-pilot-'));print('Disposable logs: '+str(results),flush=True)
    print('BTC image: '+btc+'\nXBT image: '+xbt+'\nController image: '+controller,flush=True)
    with tempfile.TemporaryDirectory(prefix='pilot-binaries-') as tmp:
        prefix=Path(tmp);container=base.docker('create','--network','none',xbt)
        try:base.docker('cp',container+':/usr/local/.',str(prefix)+'/')
        finally:base.docker('rm',container)
        for scenario in (('repeat-normal','repeat-lost-reply','repeat-failure') if args.repeat else ('normal','lost-reply','failure','pending-close')):
            work=results/scenario;work.mkdir()
            quote.run(repo,work,prefix,backend,btc,controller,scenario,pilot=True)
if __name__=='__main__':main()
