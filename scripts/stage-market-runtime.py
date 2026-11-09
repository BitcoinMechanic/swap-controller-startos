#!/usr/bin/env python3
"""Apply the package's quote policy to an already staged controller runtime."""
import hashlib,json,shutil,sys
from pathlib import Path
runtime=Path(sys.argv[1]);source=Path(__file__).resolve().parent.parent/'migration'
p=runtime/'runtime-manifest.json';manifest=json.loads(p.read_text())
for name in ('market_quote.py','service_market.py','service_cli.py','cross_contract.py','customer_https.py'):
    if name not in manifest['files']:raise SystemExit('Missing runtime manifest entry: '+name)
    shutil.copyfile(source/name,runtime/name)
    manifest['files'][name]=hashlib.sha256((runtime/name).read_bytes()).hexdigest()
p.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')

import subprocess
subprocess.run([sys.executable,str(source.parent/"patches/stage-timelock-runtime.py"),str(runtime)],check=True)
