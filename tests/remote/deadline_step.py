"""Test-only packaged deadline entry point; no node files or CLI available."""
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,'/app')
from controller import private_load,save
from deadline_boundary import DeadlineRemote,step
from deadline_recovery import ClaimRemote,resolve
from claim_verification import ChainReader,inspect_config
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


class AuditedClaim(ClaimRemote):
    def __init__(self,config,direction,payment_hash,binding,drop):
        super().__init__(config,direction,payment_hash,binding);self.drop=drop

    def _request(self,method,params):
        result=super()._request(method,params)
        with (ROOT/'claim-audit.jsonl').open('a') as out:
            out.write(json.dumps(dict(network=self.network,method=method))+'\n')
            out.flush();os.fsync(out.fileno())
        if self.drop and method in ('xbt-release-bound','reverse-release'):os._exit(89)
        return result


def main():
    os.umask(0o077)
    isolation()
    assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
    stage=sys.argv[1]
    assert stage in ('step','drop-close-reply','recover','drop-release-reply','verify','verify-outage','verify-crash')
    verifying=stage.startswith('verify')
    recovering=stage in ('recover','drop-release-reply')
    assert not (ROOT/'remote.json').exists()
    (ROOT/'deadline-job').mkdir(mode=0o700,exist_ok=True)
    if verifying:
        assert not (ROOT/'deadline-input.json').exists() and not (ROOT/'claim-input.json').exists()
        (ROOT/'deadline-output.json').unlink(missing_ok=True)
        def load_request():
            if stage=='verify-crash':os._exit(89)
            return private_load(ROOT/'verification-input.json')
        result=inspect_config(ROOT/'deadline-job',load_request)
        save(ROOT/'deadline-output.json',result)
        print('{"packaged_deadline_step":true}')
        return
    request=private_load(ROOT/('verification-input.json' if verifying else 'claim-input.json' if recovering else 'deadline-input.json'));spec=request['spec']
    assert spec['direction'] in ('forward','reverse')
    incoming='xbt' if spec['direction']=='reverse' else 'btc'
    clients={}
    for config in request['connections']:
        role={'regtest':'btc','xbt-regtest':'xbt'}[config['network']]
        assert role not in clients
        if recovering:
            assert not (ROOT/'deadline-input.json').exists()
            clients[role]=AuditedClaim(config,spec['direction'],spec['payment_hash'],
                [spec['channel']['short_channel_id'],spec['htlc_id']],stage=='drop-release-reply')
        else:
            clients[role]=Audited(config,spec['channel']['channel_id'] if role==incoming else None,stage=='drop-close-reply')
    result=(resolve if recovering else step)(ROOT/'deadline-job',spec,clients)
    save(ROOT/'deadline-output.json',result)
    print('{"packaged_deadline_step":true}')


if __name__=='__main__':
    try:main()
    except Exception:
        print('{"event":"packaged_deadline_failed","details":"withheld"}')
        raise SystemExit(1)
