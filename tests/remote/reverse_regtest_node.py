"""Fixture-only network/currency labels; packaged reverse authority unchanged."""
import json
import os
from pathlib import Path
import sys
assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
sys.path[:0]=['/usr/local/libexec/btc-controller','/usr/local/libexec/cln-swap']
import pilot_node
import reverse_node
import reverse_session
import swap_invoice
import reverse_invoice
encode=swap_invoice.unsigned_invoice

def unsigned(*args,**kwargs):
    assert kwargs['currency']=='xbt';kwargs['currency']='xbtrt'
    return encode(*args,**kwargs)
swap_invoice.unsigned_invoice=unsigned
reverse_invoice.unsigned=lambda h,s,cltv,amount_msat=3000000,expiry=120:encode(h,s,amount_msat,expiry,currency='xbtrt',final_cltv=cltv)

class FixtureRPC:
    def __init__(self,root,role):
        self.root=root;self.role=role;self.network='regtest' if role=='btc' else 'xbt-regtest'
        self.rpc=pilot_node.LocalRPC(root,self.network)
    def __call__(self,method,**params):
        assert self.rpc('getinfo')['network']==self.network
        result=self.rpc(method,**params)
        if method=='getinfo':result=dict(result,network='bitcoin' if self.role=='btc' else 'xbt')
        if method=='decode':
            assert result['currency']==('bcrt' if self.role=='btc' else 'xbtrt')
            result=dict(result,currency='bc' if self.role=='btc' else 'xbt')
        if method=='close':pilot_node.save(self.root/'fixture-close.json',result)
        if os.environ.get('PILOT_SCENARIO')=='reverse-routed-lost-reply' and method in ('sendpay','reverse-release'):
            raise ValueError('fixture_discarded_mutation_reply')
        return result

class FixtureSession(reverse_session.Session):
    def __init__(self,root,role):super().__init__(root,role,FixtureRPC(root,role))

if __name__=='__main__':
    role,root,mode=sys.argv[1:];root=Path(root)
    assert mode=='plugin';reverse_session.Session=FixtureSession
    original=sys.stdin
    def lines():
        for line in original:
            if line.strip():
                msg=json.loads(line)
                if msg.get('method')=='init':
                    config=msg['params']['configuration']
                    assert config['network']==('regtest' if role=='btc' else 'xbt-regtest')
                    config['network']='bitcoin' if role=='btc' else 'xbt'
                line=json.dumps(msg)+'\n'
            yield line
    sys.stdin=lines();pilot_node.plugin(root,role)
