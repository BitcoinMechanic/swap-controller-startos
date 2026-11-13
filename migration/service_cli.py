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
import traceback
import time

import held_gate as hg
import service

# Export actionable codes and structural evidence, never raw remote messages.
SAFE={'grant_closed','channel_requires_recovery','previous_enrollment_requires_recovery','grant_amount_limit','recipient_route_unknown','recipient_invoice_timing','recipient_invoice_limits','recipient_invoice_cltv_limit','outgoing_channel_not_ready','route_outside_approved_channels','route_fee_or_delay_limit','approved_channel_changed','worker_invoice_unavailable','contract_reserve_insufficient','service_lock_timeout','confirmation_required','service_pairing_required','service_previous_swap_active',
      'service_invoice_reused','service_review_superseded','service_worker_unavailable',
      'service_paused','service_restored','service_history_full','service_swap_unknown',
      'service_draft_cancelled','service_approved_swap_cannot_cancel','service_settings_invalid',
      'contract_admission_expired','bounded_route_unavailable','market_unavailable','market_data_invalid','market_amount_limit','market_depth_unavailable'}

SAFE.update({'service_manual_pairing_preserved', 'local_configuration_invalid'})

SAFE.update({'node_reply_too_large', 'node_connection_refused', 'node_reply_shape_invalid', 'node_reply_json_invalid', 'node_timeout', 'node_rpc_rejected', 'node_connection_error', 'node_content_type_invalid', 'node_admission_code_unknown', 'node_http_error', 'node_dns_error', 'node_client_error', 'node_tls_error'})
SAFE.update({'node_not_synced', 'incoming_liquidity_insufficient', 'incoming_hint_unavailable', 'channel_alias_metadata_invalid', 'incoming_htlc_policy_limit', 'incoming_amount_below_safe_minimum', 'channel_fee_metadata_invalid', 'incoming_channel_busy', 'incoming_channel_disconnected', 'incoming_channels_unavailable', 'outside_grant'})

def failure_report(error, directory):
    reason = ('service_busy' if isinstance(error, BlockingIOError) else
              str(error) if isinstance(error, ValueError) and str(error) in SAFE else
              'service_request_refused')
    diagnostic = dict(getattr(error, 'diagnostic', {})) if isinstance(error, (service.https.HttpsError, service.cc.AmountLimitError)) else {}
    diagnostic['reference'] = secrets.token_hex(8)
    # Source positions only: exception messages, source lines and locals can contain secrets.
    frames = traceback.extract_tb(error.__traceback__)
    diagnostic['locations'] = [f'{Path(f.filename).name}:{f.lineno}' for f in frames
                              if Path(f.filename).parent == Path(__file__).parent][-6:]
    report = dict(reason=reason, records_preserved=True, diagnostic=diagnostic)
    try:
        folder = Path(directory)/'action-diagnostics'
        folder.mkdir(mode=0o700, exist_ok=True)
        target = folder/(str(time.time_ns())+'-'+diagnostic['reference']+'.json')
        with target.open('x') as output:
            os.chmod(target, 0o600)
            json.dump(report, output, sort_keys=True)
        for old in sorted(folder.glob('*.json'))[:-64]: old.unlink()
        diagnostic['saved'] = True
    except OSError:
        diagnostic['saved'] = False
    return report


def action_journal(directory, timeout=5.0):
    """Wait only before dispatch; never replay an action after it has begun."""
    deadline=time.monotonic()+timeout
    while True:
        try:return hg.Journal(directory)
        except BlockingIOError:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise ValueError('service_lock_timeout') from None
            time.sleep(min(.05,remaining))


def execute(directory,request,*,expected_environment=None,**options):
    hg.need(type(request) is dict and type(request.get('action')) is str,'service_request_invalid')
    action=request['action']; fields={'status':set(),'history':set(),'pair':{'binding','confirmed'},'pair_local':{'bindings','confirmed'},
        'prepare':{'direction','invoice'},'confirm':{'cid','confirmation','confirmed'},
        'cancel':{'cid','confirmation'},'invoice':{'cid'},'configure':{'paused','markup_bps'},
        'recovery_review':{'cid'},'recovery_confirm':{'cid','confirmation','confirmed'}}
    hg.need(action in fields and set(request)==fields[action]|{'action'},'service_request_invalid')
    j=action_journal(directory)
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
        print(json.dumps(failure_report(e, args.directory))); return 1


if __name__=='__main__':raise SystemExit(main())
