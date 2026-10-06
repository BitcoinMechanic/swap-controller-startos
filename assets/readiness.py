"""Fresh read-only live readiness observations; never execution authorization."""
import json
import os
from pathlib import Path
import sys
import time
from controller import load_config, require, NETWORKS
from read_only_rpc import Client
from gate_observation import observe, GateClient, XbtGateClient


def base(root):
    result=dict(read_only=True,live_payment_enabled=False,live_ready=False,
        paired=False,connection_ready=False,restore_barrier=os.path.lexists(root/'execution'/'restored.json'),
        gate_activation='not_verified_with_read_only_credentials',
        execution_credentials='not_configured_by_this_release',
        live_amount_fee_expiry_policy='not_supported_by_this_release',
        cross_chain_timing_policy='not_supported_by_this_release',
        live_executor='not_supported_by_this_release',nodes={},
        blockers=['live_execution_not_supported','gate_activation_not_verified','dedicated_execution_credentials_required',
                  'live_amount_fee_expiry_policy_required','live_cross_chain_timing_policy_required'])
    if result['restore_barrier']:result['blockers'].append('restored_execution_remains_blocked')
    return result


def inspect(root,factory=Client,clock=time.time,gate_factory=GateClient,xbt_gate_factory=XbtGateClient):
    result=base(root)
    try:config=load_config(root)
    except Exception:
        result['blockers'].append('pairing_record_unavailable');return result
    if config is None:
        result['blockers'].append('pair_nodes_first');return result
    result['paired']=True
    clients={};infos={};start=clock()
    # Verify BOTH identities before reading either node's channels.
    for role,network in NETWORKS.items():
        node=config['nodes'][role]
        try:
            client=factory(node['url'],node['rune'],ca_data=node['ca_pem'])
            info=client.call('getinfo')
            require(info.get('id')==node['node_id'] and info.get('network')==network,'identity_mismatch')
            infos[role]=dict(identity_matches=True,network=network,
                warning_present=any(k.startswith('warning') and bool(v) for k,v in info.items()))
            clients[role]=client
        except Exception:
            infos[role]=dict(identity_matches=False,observation='identity_or_transport_unavailable')
    result['nodes']=infos
    if len(clients)==2:
        for role,client in clients.items():
            try:
                channels=client.call('listpeerchannels')['channels']
                require(isinstance(channels,list),'invalid_channels')
                normal=connected=pending=0
                for channel in channels:
                    require(isinstance(channel,dict) and isinstance(channel.get('state'),str),'invalid_channel')
                    htlcs=channel.get('htlcs',[])
                    require(isinstance(htlcs,list) and all(isinstance(h,dict) for h in htlcs),'invalid_htlcs')
                    pending+=len(htlcs)
                    if channel['state']=='CHANNELD_NORMAL':
                        normal+=1
                        require(type(channel.get('peer_connected')) is bool,'invalid_peer_connection')
                        connected+=int(channel['peer_connected'])
                infos[role].update(normal_channels=normal,connected_normal_channels=connected,pending_htlcs=pending,
                                   observation='verified')
            except Exception:infos[role]['observation']='channel_observation_unavailable'
    for role,info in infos.items():
        if info.get('observation')!='verified':result['blockers'].append(role+'_observation_unavailable')
        else:
            if info['warning_present']:result['blockers'].append(role+'_node_warning')
            if not info['connected_normal_channels']:result['blockers'].append(role+'_connected_channel_required')
            if info['pending_htlcs']:result['blockers'].append(role+'_pending_htlcs_require_review')
    result['btc_gate']=observe(root,config,gate_factory) if len(clients)==2 else dict(observation='identity_not_verified')
    result['xbt_gate']=observe(root,config,xbt_gate_factory,'xbt') if len(clients)==2 else dict(observation='identity_not_verified')
    end=clock()
    # Do not publish observations against credentials replaced during the probe.
    try:unchanged=load_config(root)==config
    except Exception:unchanged=False
    if not unchanged or not 0<=end-start<=120:
        result['nodes']={};result['connection_ready']=False
        result['btc_gate']=dict(observation='expired_or_pairing_changed')
        result['xbt_gate']=dict(observation='expired_or_pairing_changed')
        result['blockers'].append('pairing_changed_or_observation_expired')
    else:
        result['checked_at']=int(end)
        result['connection_ready']=all(i.get('observation')=='verified' and not i.get('warning_present') for i in infos.values())
    verified={role:result[role+'_gate'].get('observation')=='verified' for role in NETWORKS}
    if any(verified.values()):
        result['blockers'].remove('gate_activation_not_verified')
        if all(verified.values()): result['gate_activation']='both_profiles_verified'
        else:
            ready_role=next(role for role in NETWORKS if verified[role])
            missing_role=next(role for role in NETWORKS if not verified[role])
            result['gate_activation']=ready_role+'_verified_'+missing_role+'_not_verified'
            result['blockers'].append(missing_role+'_gate_activation_not_verified')
    result['restore_barrier']=os.path.lexists(root/'execution'/'restored.json')
    if result['restore_barrier'] and 'restored_execution_remains_blocked' not in result['blockers']:
        result['blockers'].append('restored_execution_remains_blocked')
    return result


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    try:
        require(len(argv)==1,'invalid_arguments')
        report=inspect(Path(argv[0]))
        print(json.dumps(report));return 0
    except Exception:
        print('{"read_only":true,"live_ready":false,"live_payment_enabled":false,"error":"readiness_unavailable"}')
        return 1

if __name__=='__main__':raise SystemExit(main())
