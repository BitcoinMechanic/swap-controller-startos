"""Fixture-only network adaptation. Never copied into any service image.

All actual daemon identities must be regtest. Only currency/network labels and
invoice encoding are adapted; node authority, journals and mutations are the
packaged production implementation. Does not establish live-chain safety.
"""
import json
import os
from pathlib import Path
import sys
assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
sys.path.insert(0,'/usr/local/libexec/btc-controller')
sys.path.insert(0,'/usr/local/libexec/cln-swap')
import pilot_node
import swap_invoice
import swap_session
REAL=pilot_node.Node
encode=swap_invoice.unsigned_invoice

def unsigned(*args,**kwargs):
    assert kwargs['currency']=='bc';kwargs['currency']='bcrt'
    return encode(*args,**kwargs)
swap_invoice.unsigned_invoice=unsigned

class FixtureRPC:
    def __init__(self,root,role):
        self.root=root;self.role=role;self.network='regtest' if role=='btc' else 'xbt-regtest'
        self.rpc=pilot_node.LocalRPC(root,self.network)
    def __call__(self,method,**params):
        info=self.rpc('getinfo');assert info['network']==self.network
        if method=='xbt-register':
            params=dict(params,quote=dict(params['quote']));assert params['quote'].pop('pilot') in ('live-pilot-v1',swap_session.PROFILE)
        result=self.rpc(method,**params)
        if method=='getinfo':result=dict(result,network='bitcoin' if self.role=='btc' else 'xbt')
        if method=='decode':
            assert result['currency']==('bcrt' if self.role=='btc' else 'xbtrt')
            result=dict(result,currency='bc' if self.role=='btc' else 'xbt')
        if method=='xbt-pilot-info':
            assert result['profile']=='regtest';result=dict(result,profile=swap_session.PROFILE if os.environ.get('PILOT_SCENARIO','').startswith('repeat-') else 'live-pilot-v1')
        if method=='xbt-spend-info':result=dict(result,pilot=swap_session.PROFILE if os.environ.get('PILOT_SCENARIO','').startswith('repeat-') else 'live-pilot-v1')
        if method=='close':pilot_node.save(self.root/'fixture-close.json',result)
        if os.environ.get('PILOT_SCENARIO') in ('lost-reply','repeat-lost-reply','repeat-routed-lost-reply') and method in ('sendpay','xbt-release-bound'):
            raise ValueError('fixture_discarded_mutation_reply')
        return result

class FixtureNode(REAL):
    def __init__(self,root,role):super().__init__(root,role,FixtureRPC(root,role))

class FixtureSession(swap_session.Session):
    def __init__(self,root,role):super().__init__(root,role,FixtureRPC(root,role))
    def enable_gate(self):pass # Production gate profile tested separately; daemon must stay regtest.

if __name__=='__main__':
    role,root,mode=sys.argv[1:];root=Path(root)
    if mode=='authorize':
        request=json.load(sys.stdin)
        with pilot_node.locked(root):result=FixtureNode(root,role).authorize(request['contract'],request['confirmed'])
        print(json.dumps(result))
    else:
        assert mode=='plugin';pilot_node.Node=FixtureNode;swap_session.Session=FixtureSession
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
