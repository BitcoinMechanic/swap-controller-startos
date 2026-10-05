"""Regtest adapter only: exercises executor code and controllers baked into image."""
import contextlib
import io
import subprocess
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0, '/app')
from controller import private_load, save
import executor
import lifecycle
from partition_check import isolation


def main():
    isolation()
    direction, flag, filename, *flags = sys.argv[1:]
    assert flag == '--state'
    external = Path(filename)
    manager = external.parent/'execution'
    use_worker = os.environ.get('LIFECYCLE_WORKER') == '1'
    if use_worker:
        lifecycle.setup(manager)
    root = manager/'jobs'/'swap' if use_worker else manager
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
        if use_worker and not flags:
            with contextlib.redirect_stdout(io.StringIO()):
                outcomes=lifecycle.tick(manager)
            result=outcomes.get('swap') or subprocess.CompletedProcess([], 1, '', '')
        else:
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
