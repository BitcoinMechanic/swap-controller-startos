#!/usr/bin/env python3
"""Apply reviewed timelock compatibility patches to the sealed staged runtime.

Exact old/new matching prevents applying to an unknown source revision. Existing
40-block plans retain their schema and digest; recovery records are not rewritten.
"""
import hashlib, json, sys
from pathlib import Path
runtime=Path(sys.argv[1])
patches=json.loads((Path(__file__).with_name('timelock-patches.json')).read_text())
p=runtime/'runtime-manifest.json'; manifest=json.loads(p.read_text())
for name, edits in patches.items():
    if name not in manifest['files']: continue # controller/node have different facades
    f=runtime/name
    text=f.read_text()
    for old,new in edits:
        if text.count(old)==1: text=text.replace(old,new,1)
        elif text.count(new)!=1: raise SystemExit('Unknown runtime source: '+name)
    f.write_text(text)
    manifest['files'][name]=hashlib.sha256(f.read_bytes()).hexdigest()
p.write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
