"""Fixed test entry point. Inputs remain private in the controller-only volume."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,'/app')
from controller import private_load,save
from partition_check import isolation


def worker(manager,interrupt=False):
    import executor
    import lifecycle
    if not interrupt:return lifecycle.tick(manager)
    from unittest.mock import patch
    original=executor.step;exits=[]
    def interrupted(root,*args,**kwargs):
        initial,_=executor.records(root)
        forward=initial['direction']=='forward'
        kwargs['flags']=('--crash-after-btc' if forward else '--crash-after-xbt-resolution',)
        result=original(root,*args,**kwargs)
        assert result.returncode==(87 if forward else 89)
        exits.append(result.returncode)
        return result
    with patch.object(executor,'step',side_effect=interrupted):lifecycle.tick(manager)
    assert len(exits)==1
    save(manager/'resolution-interrupted.json',dict(returncode=exits[0]))


def main():
    isolation()
    stage=sys.argv[1]
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert stage in ('prepare','approve','status','review','worker','worker-interrupt-resolution')
    root=Path('/controller-state');manager=root/'execution'
    if stage in ('worker','worker-interrupt-resolution'):
        worker(manager,stage=='worker-interrupt-resolution')
        mode='review';request=dict(job='swap')
    elif stage=='prepare':
        # Test-only provisioning. The StartOS action itself cannot supply or
        # replace endpoints/credentials and never uses live monitor pairing.
        import lifecycle
        lifecycle.setup(manager)
        raw=private_load(root/'quote-input.json')
        config=manager/'regtest-quote-nodes.json'
        if config.exists():assert private_load(config)==raw['connections']
        else:save(config,raw['connections'])
        if 'btc_invoice' in raw:
            mode='prepare-reverse';request=dict(job='swap',btcInvoice=raw['btc_invoice'],xbtSats=raw['xbt_sats'])
        else:mode='prepare';request=dict(job='swap',xbtInvoice=raw['xbt_invoice'],btcSats=raw['btc_sats'])
    elif stage=='approve':
        raw=private_load(root/'quote-input.json')
        mode='approve';request=dict(job='swap',expectedDigest=raw['digest'],confirmed=raw['confirmed'])
    else:
        mode=stage;request={} if stage=='status' else dict(job='swap')
    command=['python3','/app/quote_actions.py',str(manager),mode]
    body=json.dumps(request)
    result=subprocess.run(command,input=body,text=True,capture_output=True,timeout=60)
    if result.returncode:
        print('{"event":"packaged_quote_action_failed","details":"withheld"}')
        return result.returncode
    # The BTC invoice is returned to the disposable payer through a private
    # response file, never through host console/status logs.
    save(root/'quote-output.json',json.loads(result.stdout))
    print('{"packaged_quote_action":true}')
    return 0


if __name__=='__main__':raise SystemExit(main())
