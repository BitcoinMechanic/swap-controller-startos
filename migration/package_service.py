#!/usr/bin/python3 -I
"""Packaged service boundary. Missing journals are never recreated by run/action."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import package_verify
    package_verify.verify(Path(__file__).parent, 'controller')
    import service_cli
    raise SystemExit(service_cli.main())
except Exception:
    print('{"reason":"packaged_service_refused","records_preserved":true}')
    raise SystemExit(1) from None
