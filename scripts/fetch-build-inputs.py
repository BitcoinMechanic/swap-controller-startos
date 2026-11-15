#!/usr/bin/env python3
"""Fetch published runtime images, verifying source pins, sizes and SHA-256.

No Docker daemon, wallet credentials, or server access is required.
"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent.parent

def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def fetch(root=ROOT):
    lock = json.loads((root / 'build-inputs.json').read_text())
    if lock['schema'] != 1 or lock['architecture'] != 'x86_64':
        raise ValueError('Unsupported build-input schema or architecture')
    for name, expected in lock['runtime_source_hashes'].items():
        if digest(root / name) != expected:
            raise ValueError('Runtime source changed; publish matching build inputs: ' + name)
    cache = root / '.build-inputs'
    cache.mkdir(exist_ok=True)
    for name, entry in lock['images'].items():
        target = cache / (name + '-x86_64.tar')
        def valid(path):
            return path.stat().st_size == entry['size'] and digest(path) == entry['sha256']
        if target.exists():
            if not valid(target):
                raise ValueError('Cached image failed integrity verification: ' + str(target))
            print('Verified cached ' + target.name, flush=True)
            continue
        temporary = target.with_suffix('.part')
        try:
            request = urllib.request.Request(entry['url'], headers={'User-Agent': 'StartOS-pinned-image-build'})
            with urllib.request.urlopen(request, timeout=120) as response, temporary.open('wb') as stream:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > entry['size']: raise ValueError('Published image exceeds pinned size: ' + name)
                    stream.write(chunk)
            if not valid(temporary): raise ValueError('Published image failed integrity verification: ' + name)
            temporary.replace(target)
            print('Downloaded and verified ' + target.name, flush=True)
        finally:
            temporary.unlink(missing_ok=True)

if __name__ == '__main__':
    try: fetch()
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
