#!/usr/bin/env python3
"""Remove policy amount ceilings; retain signed 64-bit millisatoshi validation.

Existing contracts/grants are never rewritten. Only runtime code is staged.
"""
import hashlib, json, sys
from pathlib import Path
changes = {'route_plan.py': [("10000000000 if plan['chain']['profile']==ci.PRIVKEYIO_XBT else 1000000000", '2**63-1'), ("10000000000 if chain['profile']==ci.PRIVKEYIO_XBT else 1000000000", '2**63-1'), ('integer(amount, 1, 10000010000)', 'integer(amount, 1, 2**63-1)')], 'held_gate.py': [("10000000000 if expected['profile']==ci.PRIVKEYIO_XBT else 1000000000", '2**63-1')], 'bounded_authority.py': [("hg.integer(value['max_amount_msat'], 1, 10000010000)", "hg.integer(value['max_amount_msat'], 1, 2**63-1)"), ("hg.integer(value['total_amount_msat'], value['max_amount_msat'], 100000100000)", "hg.integer(value['total_amount_msat'], value['max_amount_msat'], 2**63-1)")], 'customer_https.py': [("hg.integer(body['amount_msat'],1,10000010000)", "hg.integer(body['amount_msat'],1,2**63-1)")]}

def apply(runtime):
    p = runtime / 'runtime-manifest.json'
    manifest = json.loads(p.read_text())
    for name, edits in changes.items():
        if name not in manifest['files']: continue
        f = runtime / name
        text = f.read_text()
        for old, new in edits:
            if text.count(old) == 1: text = text.replace(old, new, 1)
            elif new not in text: raise SystemExit('Unknown amount validation source: ' + name)
        f.write_text(text)
        manifest['files'][name] = hashlib.sha256(f.read_bytes()).hexdigest()
    p.write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')

if __name__ == '__main__':
    apply(Path(sys.argv[1]))
