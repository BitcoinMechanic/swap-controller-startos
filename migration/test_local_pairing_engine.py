#!/usr/bin/env python3
"""Native four bounded grants on two coordinator nodes, automatic local pairing."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

import chain_identity as ci
import customer_remote
import held_gate as hg
import local_pairing
import service
import test_authority_engine as authority
import test_customer_engine as customer
import test_route_engine as routes
from test_provider_quote_engine import Lab as BaseLab
from test_engine import require


class Lab(BaseLab):
    def start(self,command,log):
        runtime=self.args.runtime;self.args.runtime=None
        try:return super().start(command,log)
        finally:self.args.runtime=runtime

def run(lab):
    topologies={label:customer.topology(lab,label,profile,grant_amount_msat=10000000)
                for label,profile in [('btc',ci.BTC),('xbt',ci.PRIVKEYIO_XBT)]}
    config={}; paths=[]
    for label,profile in [('btc',ci.BTC),('xbt',ci.PRIVKEYIO_XBT)]:
        _,nodes,clients=topologies[label];node=nodes[0];outgoing=clients[0]
        worker=authority.Worker(lab,dict(node,root=customer.state_dir(node)))
        grant=worker.call('create_grant','incoming',[hg.channel_pin(routes.edge(lab,node,nodes[1]),profile)],
            dict(max_swaps=4,max_amount_msat=10000000,total_amount_msat=30000000,min_cltv_delta=350,max_cltv_delta=2016),
            int(time.time())+3600,confirmed=True)
        gid=grant
        credential=lab.rpc_named(node['cli'],'swap-customer-credential',dict(grant_id=gid,confirmed=True))
        info=lab.rpc_named(node['cli'],'swap-customer-call',dict(grant_id=gid,operation='info',body={}))
        incoming=lab.config(node,credential['rune'],info['grant'])
        out=lab.config(node,outgoing.credential['rune'],outgoing.call('info')['grant'])
        config[label]=dict(url=incoming['url'],ca_pem=incoming['ca_pem'],credentials={k:{f:n[f] for f in ('grant','rune')}
            for k,n in [('incoming',incoming),('outgoing',out)]})
        paths.append(customer.state_dir(node)/'bounded-authority-v1/state.json')
    before=[p.read_bytes() for p in paths]
    root=lab.root/'local-controller';service.initialize(root)
    request=dict(action='pair_local',bindings=local_pairing.bindings(config),confirmed=True)
    def call():
        result=subprocess.run(['python3',str(lab.args.runtime/'package_service.py'),'--disposable-regtest',str(root),'action'],
            input=json.dumps(request),text=True,capture_output=True,timeout=90)
        require(result.returncode==0,'packaged_local_pairing_refused')
        return json.loads(result.stdout)['result']
    lab.checkpoint('local-pair-four-native-grants')
    require(call()['paired'],'local_pairing_not_saved')
    saved=(root/'state.json').read_bytes()
    require(call()['paired'] and (root/'state.json').read_bytes()==saved,'local_retry_mutated_state')
    record=json.loads(saved)
    require(all(record['selected'][d] is not None for d in ('forward','reverse')),'both_directions_not_paired')
    require(before==[p.read_bytes() for p in paths],'pairing_mutated_node_authority')
    for _,nodes,_ in topologies.values():
        for node in nodes:require(lab.rpc(node['cli'],'listsendpays')['payments']==[],'pairing_submitted_payment')
    result=dict(native_https=True,two_coordinator_identities=True,four_existing_grants=True,
                both_directions_paired=True,packaged_controller=True,repeated_pairing_noop=True,
                node_authority_unchanged=True,payment_attempts=0,regtest_only=True)
    (lab.root/'local-result.json').write_text(json.dumps(result,indent=2)+'\n')
    lab.checkpoint('complete');print('PASS: packaged automatic pairing of native BTC/XBT bounded grants',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--disposable-regtest',required=True,action='store_true')
    for key in ('runtime','btc-prefix','xbt-prefix','bitcoind','bitcoin-cli','results-parent'):
        p.add_argument('--'+key,type=Path,required=True)
    args=p.parse_args();os.umask(0o077);lab=Lab(args)
    try:run(lab)
    finally:lab.close()
