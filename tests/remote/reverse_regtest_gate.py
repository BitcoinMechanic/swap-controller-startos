"""Disposable gate network adapter, never installed in a service image."""
import json
import os
from pathlib import Path
import sys
assert os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')=='1'
sys.path[:0]=['/opt/xbt/libexec','/opt/xbt/libexec/xbt-swap']
from reverse_repeat_gate import main
from reverse_activation import ACTIVE
original=sys.stdin

def requests():
    for line in original:
        if line.strip():
            msg=json.loads(line)
            if msg.get('method')=='init':
                config=msg['params']['configuration']
                assert config['network']=='xbt-regtest'
                config['network']='xbt'
            line=json.dumps(msg)+'\n'
        yield line
sys.stdin=requests()
token=ACTIVE.set(True)
try:main(Path(sys.argv[1]),live=True)
finally:ACTIVE.reset(token)
