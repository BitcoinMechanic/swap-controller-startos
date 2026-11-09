#!/usr/bin/env python3
"""Finite migration service actions on private stdin; no RPC proxy or fault flags."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import signal
import sys
import threading

import held_gate as hg
import service

# Only these local admission errors cross the UI boundary. Remote errors stay opaque.
SAFE={'confirmation_required','service_pairing_required','service_previous_swap_active',
      'service_invoice_reused','service_review_superseded','service_worker_unavailable',
      'service_paused','service_restored','service_history_full','service_swap_unknown',
      'service_draft_cancelled','service_approved_swap_cannot_cancel','service_settings_invalid',
      'contract_admission_expired','bounded_route_unavailable','market_unavailable','market_data_invalid','market_amount_limit','market_depth_unavailable'}


def execute(directory,request,*,expected_environment=None,**options):
    hg.need(type(request) is dict and type(request.get('action')) is str,'service_request_invalid')
    action=request['action']; fields={'status':set(),'history':set(),'pair':{'binding','confirmed'},'pair_local':{'bindings','confirmed'},
        'prepare':{'direction','invoice'},'confirm':{'cid','confirmation','confirmed'},
        'cancel':{'cid','confirmation'},'invoice':{'cid'},'configure':{'paused','markup_bps'},
        'recovery_review':{'cid'},'recovery_confirm':{'cid','confirmation','confirmed'}}
    hg.need(action in fields and set(request)==fields[action]|{'action'},'service_request_invalid')
    j=hg.Journal(directory)
    try:
        s=service.Service(j,**options)
        hg.need(expected_environment is None or service.environment(s.record)==expected_environment,'service_environment_changed')
        args={k:v for k,v in request.items() if k!='action'}
        if action in ('status','history'): return s.status()
        if action=='invoice':
            s.guard(); return s.worker(args['cid']).payment_invoice(args['cid'])
        return getattr(s,action)(**args)
    finally: j.close()


def run(directory,*,factory=service.https.Client,clock=service.time.time,interval=1,stop=None,expected_environment=None):
    stop=stop or threading.Event(); deadline=service.time.monotonic()+120
    while True:
        try: fd=service.runner_fd(directory,create=True); break
        except BlockingIOError:
            if stop.wait(.05): return
            if service.time.monotonic()>=deadline: raise
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); run_id=secrets.token_hex(32)
        while not stop.is_set():
            j=None
            try:
                j=hg.Journal(directory); s=service.Service(j,factory=factory,clock=clock)
                hg.need(expected_environment is None or service.environment(s.record)==expected_environment,'service_environment_changed')
                s.tick(run_id)
            except BlockingIOError: pass
            finally:
                if j is not None:j.close()
            stop.wait(interval)
    finally: os.close(fd)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    mode=p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--disposable-regtest',action='store_true')
    mode.add_argument('--mainnet',action='store_true')
    p.add_argument('directory',type=Path); p.add_argument('mode',choices=('init','action','run','local-run')); p.add_argument('--local-config',type=Path); args=p.parse_args()
    os.umask(0o077)
    environment='mainnet' if args.mainnet else 'regtest'
    try:
        if args.mode=='init':service.initialize(args.directory,environment); result=dict(initialized=True,regtest_only=environment=='regtest')
        elif args.mode=='local-run':
            import local_pairing
            hg.need(args.local_config is not None,'service_request_invalid')
            stop=threading.Event()
            for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *a:stop.set())
            local_pairing.run(args.directory,args.local_config,environment,stop=stop); return 0
        elif args.mode=='run':
            stop=threading.Event()
            for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *a:stop.set())
            run(args.directory,stop=stop,expected_environment=environment); return 0
        else:
            raw=sys.stdin.read(hg.MAX_BYTES+1); hg.need(len(raw)<=hg.MAX_BYTES,'service_request_invalid')
            result=execute(args.directory,json.loads(raw,object_pairs_hook=hg.unique_object),expected_environment=environment)
        print(json.dumps(dict(result=result))); return 0
    except Exception as e:
        reason='service_busy' if isinstance(e,BlockingIOError) else str(e) if isinstance(e,ValueError) and str(e) in SAFE else 'service_request_refused'
        print(json.dumps(dict(reason=reason,records_preserved=True))); return 1


if __name__=='__main__':raise SystemExit(main())
