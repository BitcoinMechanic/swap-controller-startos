"""Regtest adapter only: exercises executor code and controllers baked into image."""
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, '/app')
from controller import private_load, save
import executor
from partition_check import isolation


def main():
    isolation()
    direction, flag, filename, *flags = sys.argv[1:]
    assert flag == '--state'
    external = Path(filename)
    root = external.parent/'execution'
    root.mkdir(mode=0o700, exist_ok=True)
    if not (root/'intent.json').exists():
        digest = executor.prepare(root, direction, private_load(external), private_load(external.parent/'remote.json'))
        # The automatic recovery path must not start a merely prepared swap.
        try: executor.step(root, recover_only=True)
        except ValueError: pass
        else: raise AssertionError('recovery originated an unauthorized payment')
        assert not (root/'launched.json').exists()
        executor.authorize(root, digest, int(time.time())+600, confirmed=True)
    else:
        assert private_load(external) == private_load(root/'state.json'), 'fixture cannot replace durable executor state'
    try:
        result = executor.step(root, flags=flags)
    finally:
        # These mirrors are solely for the original node-fixture assertions.
        # The executable journal and credential binding stay in execution/.
        save(external, private_load(root/'state.json'))
        audit = root/'remote-audit.jsonl'
        if audit.exists():
            (external.parent/'remote-audit.jsonl').write_bytes(audit.read_bytes())
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


if __name__ == '__main__': raise SystemExit(main())
