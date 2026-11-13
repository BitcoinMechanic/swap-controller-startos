"""Local StartOS wiring for already-approved bounded grants; never mint authority."""
import json
import os
import time

import held_gate as hg
import service_cli


def bindings(config):
    hg.need(type(config) is dict and set(config)=={'btc','xbt'},'local_configuration_invalid')
    result={}
    for name,profile in [('btc','btc-cln-v1'),('xbt','xbt-privkeyio-v1')]:
        node=config[name]
        hg.need(type(node) is dict and set(node)=={'url','ca_pem','credentials'},'local_configuration_invalid')
        hg.need(type(node['credentials']) is dict and set(node['credentials'])=={'incoming','outgoing'},'local_configuration_invalid')
        for kind,c in node['credentials'].items():
            hg.need(type(c) is dict and set(c)=={'grant','rune'} and c['grant']['kind']==kind
                    and c['grant']['chain']['profile']==profile,'local_configuration_invalid')
    for direction,inc,out in [('forward','btc','xbt'),('reverse','xbt','btc')]:
        result[direction]=dict(direction=direction,nodes={kind:dict(url=config[name]['url'],
            ca_pem=config[name]['ca_pem'],**config[name]['credentials'][kind])
            for kind,name in [('incoming',inc),('outgoing',out)]})
    return result


def run(directory,config_path,environment,*,stop,interval=30):
    # Configuration is a generated private rootfs file, never a backup or log.
    raw=config_path.read_text(); hg.need(len(raw.encode())<=262144,'local_configuration_invalid')
    value=bindings(json.loads(raw,object_pairs_hook=hg.unique_object))
    while not stop.is_set():
        paired=False; failure=None
        try:
            service_cli.execute(directory,dict(action='pair_local',bindings=value,confirmed=True),
                                expected_environment=environment)
            paired=True
        except Exception as error:
            failure=service_cli.failure_report(error, directory)
        status=config_path.parent/'local-node-status.json'
        tmp=status.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(paired=paired,checked_at=int(time.time()),
                                      reason=None if failure is None else failure["reason"])))
        os.replace(tmp,status)
        stop.wait(interval)
