"""Read-only BTC/XBT pairing monitor. No swap executor or writable RPC transport."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import tempfile
import time
import uuid
from read_only_rpc import Client, ProbeError, endpoint

NETWORKS = {'btc':'bitcoin','xbt':'xbt'}

class Refused(ValueError): pass

def require(ok, reason):
    if not ok: raise Refused(reason)


def private_load(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        meta=os.fstat(f.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_size<=262144 and meta.st_mode&0o077==0,'invalid_private_record')
        return json.load(f)


def save(path, value):
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.controller-')
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,sort_keys=True);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


@contextlib.contextmanager
def locked(root):
    root.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(root/'pairing.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as f:
        require(stat.S_ISREG(os.fstat(f.fileno()).st_mode),'invalid_lock')
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield


def validate(nodes):
    require(isinstance(nodes,dict) and set(nodes)==set(NETWORKS),'both_nodes_required')
    clean={}
    for role,network in NETWORKS.items():
        n=nodes[role]
        require(isinstance(n,dict) and set(n)=={'url','node_id','rune','ca_pem'},'invalid_connection_fields')
        require(all(isinstance(v,str) for v in n.values()),'invalid_connection_fields')
        require(re.fullmatch(r'0[23][0-9a-f]{64}',n['node_id']),'invalid_node_id')
        require(re.fullmatch(r'[A-Za-z0-9_+=/-]{1,8192}',n['rune']),'invalid_rune_format')
        pem=n['ca_pem'].strip()
        require(0<len(pem)<=65536 and 'PRIVATE KEY' not in pem,'invalid_ca_certificate')
        try:ssl.create_default_context(cadata=pem)
        except Exception:raise Refused('invalid_ca_certificate') from None
        clean[role]=dict(n,url=endpoint(n['url']),ca_pem=pem)
    require(clean['btc']['node_id']!=clean['xbt']['node_id'],'distinct_node_identities_required')
    require(clean['btc']['url']!=clean['xbt']['url'],'distinct_endpoints_required')
    return clean


def probe(nodes, factory=Client):
    reports={}
    for role,network in NETWORKS.items():
        n=nodes[role]
        try:
            report=factory(n['url'],n['rune'],ca_data=n['ca_pem']).inspect(n['node_id'],network)
            reports[role]=dict(report,reachable=True)
        except ProbeError as e:
            # Only known fixed codes are copied; unexpected exceptions stay private.
            safe={'http_request_rejected','tls_or_transport_failed','tls_configuration_failed',
                  'operator_identity_mismatch','invalid_rpc_response','response_too_large',
                  'invalid_channel_response','invalid_expected_identity'}
            reports[role]=dict(reachable=False,reason=str(e) if str(e) in safe else 'probe_failed')
        except Exception:
            reports[role]=dict(reachable=False,reason='probe_failed')
    return reports


def paired_report(reports):
    return all(r.get('reachable') and r.get('identity_matches') for r in reports.values())


def load_config(root):
    path=root/'pairing.json'
    if not os.path.lexists(path):return None
    config=private_load(path)
    require(config.get('schema')==1 and re.fullmatch(r'[0-9a-f]{32}',config.get('generation','')),'invalid_pairing_record')
    config['nodes']=validate(config['nodes'])
    return config


def pair(root,nodes,confirmed=False,replace=False,factory=Client):
    require(confirmed is True,'confirmation_required')
    nodes=validate(nodes)
    with locked(root), contextlib.ExitStack() as stack:
        if os.path.lexists(root/'execution'/'forward-pilot') or os.path.lexists(root/'execution'/'forward-swaps'):
            import lifecycle
            stack.enter_context(lifecycle.locked(root/'execution'))
        old=load_config(root)
        pilot=root/'execution'/'forward-pilot'/'record.json'
        if os.path.lexists(pilot):
            current=private_load(pilot)
            require(current.get('phase') in ('review','settled','failed') or (old is not None and old['nodes']==nodes),
                    'active_pilot_pairing_change_refused')
        from forward_swaps import paths, TERMINAL
        for path in paths(root):
            require(private_load(path/'record.json')['phase'] in TERMINAL or (old is not None and old['nodes']==nodes), 'active_repeat_pairing_change_refused')
        require(old is None or old['nodes']==nodes or replace is True,'replacement_confirmation_required')
        reports=probe(nodes,factory)
        require(paired_report(reports),'pair_verification_failed')
        config=old if old and old['nodes']==nodes else dict(schema=1,generation=uuid.uuid4().hex,nodes=nodes)
        save(root/'pairing.json',config)
        # A failed pairing never changes the last working pair.
        snapshot=dict(generation=config['generation'],checked_at=int(time.time()),nodes=reports)
        save(root/'status.json',snapshot)
        return status(root)


def step(root,factory=Client):
    with locked(root):
        config=load_config(root)
        if config is None:return
        reports=probe(config['nodes'],factory)
        save(root/'status.json',dict(generation=config['generation'],checked_at=int(time.time()),nodes=reports))


def status(root,now=None):
    result=dict(read_only=True,paired=False,ready=False,live_payment_enabled=False)
    config=load_config(root)
    if config is None:return dict(result,reason='pair_nodes_first')
    result['paired']=True
    try:snapshot=private_load(root/'status.json')
    except FileNotFoundError:return dict(result,reason='waiting_for_probe')
    age=(int(time.time()) if now is None else now)-snapshot['checked_at']
    if snapshot.get('generation')!=config['generation'] or not 0<=age<=120:
        return dict(result,reason='waiting_for_fresh_probe')
    reports=snapshot['nodes']
    # Snapshot is not a source of authority; output only explicitly allowed fields.
    safe_keys=('reachable','identity_matches','network','warning_present','normal_channels','pending_htlcs','reason')
    for role in NETWORKS:
        result[role]={k:v for k,v in reports[role].items() if k in safe_keys}
    result['ready']=paired_report(reports) and not any(r.get('warning_present') for r in reports.values())
    result['age_seconds']=age
    return result


def main():
    os.umask(0o077)
    root=Path(sys.argv[1]);mode=sys.argv[2]
    if mode=='run':
        while True:
            try:step(root)
            except Exception:
                # Health ages out after failures. Never print credentials or remote errors.
                print('{"event":"probe_cycle_unavailable"}',flush=True)
            time.sleep(30)
    try:
        if mode=='pair':
            request=json.loads(sys.stdin.read(262145))
            result=pair(root,request['nodes'],request.get('confirmed'),request.get('replace'))
        elif mode=='status':result=status(root)
        else:raise Refused('unknown_operation')
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps(dict(error=str(e) if isinstance(e,(Refused,ProbeError)) else 'private_details_withheld',read_only=True)))
        raise SystemExit(1)

if __name__=='__main__':main()
