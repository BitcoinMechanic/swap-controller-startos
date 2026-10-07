"""Disposable isolated controller step using the packaged pilot implementation."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,'/app')
assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
import forward_pilot as pilot
from live_preflight import Inspector
from controller import private_load,save
root=Path('/controller-state')
class FixtureInspector(Inspector):
    def call(self,method,**params):
        value=super().call(method,**params)
        if method=='getinfo':
            assert value['network'] in ('regtest','xbt-regtest')
            value=dict(value,network='bitcoin' if value['network']=='regtest' else 'xbt')
        if method=='decode':
            assert value['currency']=='xbtrt';value=dict(value,currency='xbt')
        return value
stage=sys.argv[1]
request=private_load(root/'quote-input.json')
if stage.startswith('repeat-'):
    import forward_swaps as swaps
    import swap_setup
    if stage=='repeat-setup':
        swap_setup.configure(root,request['inspection'],factory=FixtureInspector)
        result=swaps.configure(root,request['grants'])
    elif stage=='repeat-prepare':result=swaps.prepare(root,request,inspector=FixtureInspector)
    elif stage=='repeat-approve':
        import lifecycle
        lifecycle.tick(root/'execution');result=swaps.approve(root,request)
    elif stage=='repeat-worker':
        try:swaps.tick(root)
        except ValueError:pass
        result=swaps.status(root)
    else:raise ValueError('unknown_repeat_stage')
elif stage=='prepare':result=pilot.prepare(root,request,factory=FixtureInspector)
elif stage=='approve':
    import lifecycle
    lifecycle.tick(root/'execution')
    result=pilot.approve(root,request)
elif stage=='worker':
    try:result=pilot.tick(root)
    except ValueError:result={'phase':'uncertain'}
elif stage=='status':result=pilot.status(root)
else:raise ValueError('unknown_stage')
save(root/'quote-output.json',result)
print(json.dumps(result))
