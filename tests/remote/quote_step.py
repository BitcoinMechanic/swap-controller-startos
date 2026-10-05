"""Fixed test entry point. Inputs remain private in the controller-only volume."""
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,'/app')
from controller import private_load,save
from partition_check import isolation


def main():
    isolation()
    stage=sys.argv[1]
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert stage in ('prepare','approve','status','worker')
    root=Path('/controller-state');manager=root/'execution'
    if stage=='worker':
        import lifecycle
        lifecycle.tick(manager)
        command=['python3','/app/quote_workflow.py',str(manager),'status','swap']
        body=''
    else:
        command=['python3','/app/quote_workflow.py',str(manager),stage,'swap']
        body=json.dumps(private_load(root/'quote-input.json')) if stage in ('prepare','approve') else ''
    result=subprocess.run(command,input=body,text=True,capture_output=True,timeout=60)
    if result.returncode:
        print('{"event":"packaged_quote_step_failed","details":"withheld"}')
        return result.returncode
    # The BTC invoice is returned to the disposable payer through a private
    # response file, never through host console/status logs.
    save(root/'quote-output.json',json.loads(result.stdout))
    print('{"packaged_quote_step":true}')
    return 0


if __name__=='__main__':raise SystemExit(main())
