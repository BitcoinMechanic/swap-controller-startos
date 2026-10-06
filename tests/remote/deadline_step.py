"""Test-only packaged deadline entry point; no node files or CLI available."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from controller import private_load,save
from deadline_boundary import DeadlineRemote,step
from partition_check import isolation

ROOT=Path('/controller-state')

class Audited(DeadlineRemote):
    def __init__(self,config,target,drop):
        super().__init__(config,target);self.drop=drop

    def _request(self,method,params):
        result=super()._request(method,params)
        # Durable private fixture evidence, separate from adapter state.
        with (ROOT/'deadline-audit.jsonl').open('a') as out:
            out.write(json.dumps(dict(network=self.network,method=method))+ '\n')
            out.flush();os.fsync(out.fileno())
        if method=='close':
            save(ROOT/'fixture-close-reply.json',result)
            if self.drop:os._exit(89)
        return result


def main():
    os.umask(0o077)
    isolation()
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    assert sys.argv[1] in ('step','drop-close-reply')
    assert not (ROOT/'remote.json').exists()
    (ROOT/'deadline-job').mkdir(mode=0o700,exist_ok=True)
    request=private_load(ROOT/'deadline-input.json');spec=request['spec']
    assert spec['direction']=='forward'
    clients={}
    for config in request['connections']:
        role={'regtest':'btc','xbt-regtest':'xbt'}[config['network']]
        assert role not in clients
        clients[role]=Audited(config,spec['channel']['channel_id'] if role=='btc' else None,
                              sys.argv[1]=='drop-close-reply')
    result=step(ROOT/'deadline-job',spec,clients)
    save(ROOT/'deadline-output.json',result)
    print('{"packaged_deadline_step":true}')


if __name__=='__main__':
    try:main()
    except Exception:
        print('{"event":"packaged_deadline_failed","details":"withheld"}')
        raise SystemExit(1)
