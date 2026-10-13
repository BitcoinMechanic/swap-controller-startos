#!/usr/bin/env python3
"""Service lifecycle, admission and immutable recovery. Synthetic coordinator."""
import copy
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import bounded_authority as ba
import held_gate as hg
import market_quote as market
import service
import service_cli
import service_market
from test_cross_worker import Remote
from test_customer_https import nodes
from test_route_plan import NOW


def price(direction,route,markup_bps,*,clock):
    now=int(clock())
    return market.quote(dict(schema=1,source='neoxa.exchange',pair='BTCB2_BTC',direction=direction,
        markup_bps=markup_bps,fetched_at_ms=now*1000,ticker_at_ms=now*1000,expires_at=now+120,
        bid='0.49',ask='0.5',levels=[dict(price='0.5' if direction=='forward' else '0.49',quantity='1')],
        recipient_msat=route[-1]['amount_msat'],routing_fee_msat=route[0]['amount_msat']-route[-1]['amount_msat'],
        btc_msat=0,xbt_msat=0),compute=True)


class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'service';service.initialize(self.root)
        self.remote=Remote();self.now=NOW;self.j=None;self.fd=None;self.open();self.addCleanup(self.close)
        outer=self
        class Client:
            def __init__(self,c):self.kind=c['grant']['kind'];self.gid=ba.digest(c['grant'])
            def call(self,op,body=None):
                if op=='info':return copy.deepcopy(outer.remote.obs[self.kind]['info'])
                if op=='plan':return copy.deepcopy(outer.remote.c['outgoing'])
                return outer.remote(self.kind,self.gid,op,body or {})
        self.factory=Client;self.open();self.bind=dict(direction='forward',nodes=nodes(self.remote))

    def close(self):
        if self.j:self.j.close();self.j=None
        if self.fd is not None:os.close(self.fd);self.fd=None

    def open(self):
        if self.j:self.j.close()
        self.j=hg.Journal(self.root)
        self.s=service.Service(self.j,factory=getattr(self,'factory',None),clock=lambda:self.now,pricer=price)

    def live(self):
        if self.fd is None:
            self.fd=os.open(self.root/'runner.lock',os.O_RDWR|os.O_CREAT,0o600)
            fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.s.tick('11'*32)

    def prepare(self):
        self.s.pair(self.bind,confirmed=True)
        result=self.s.prepare('forward',self.remote.c['outgoing']['invoice']); self.cid=result['confirmation']
        self.remote.c=copy.deepcopy(self.s.record['jobs'][self.cid]['worker']['swaps'][self.cid]['contract'])
        return result

    def enrolled(self):
        self.prepare();self.live();self.s.confirm(self.cid,self.cid,confirmed=True)
        for _ in range(3):self.s.tick('11'*32);self.open()

    def test_parent_restore_fence_preserves_pending_history_but_blocks_every_execution(self):
        import restore_barrier
        self.enrolled(); before=copy.deepcopy(self.s.record); calls=copy.deepcopy(self.remote.calls)
        restore_barrier.fence(self.root.parent)
        status=self.s.status()
        self.assertTrue(status['restored']);self.assertTrue(all(s['blocked'] for s in status['swaps']))
        for action in (lambda:self.s.tick('11'*32),lambda:self.s.confirm(self.cid,self.cid,confirmed=True),
                       lambda:self.s.configure(paused=False,markup_bps=0),lambda:self.s.pair(self.bind,confirmed=True)):
            with self.assertRaisesRegex(ValueError,'service_restored'):action()
        self.assertEqual(self.remote.calls,calls);self.assertEqual(self.s.record,before)
        with self.assertRaisesRegex(ValueError,'service_restored'):service.initialize(self.root.parent/'new-service')

    def test_invoice_confirm_pay_and_lost_replies_reload_exact_records(self):
        self.enrolled();original=self.s.record['jobs'][self.cid]['worker']['swaps'][self.cid]['contract']
        invoice=self.s.worker(self.cid).payment_invoice(self.cid)
        self.remote.hold();self.remote.lost=('outgoing','send');self.s.tick('11'*32)
        self.open();self.s.tick('11'*32);self.remote.complete()
        self.s.tick('11'*32);self.remote.lost=('incoming','release');self.s.tick('11'*32)
        self.open()
        for _ in range(3):self.s.tick('11'*32)
        self.assertEqual(self.s.cached(self.cid)['state'],'settled')
        self.assertEqual(sum(op=='send' for _,op,_ in self.remote.calls),1)
        self.assertEqual(self.s.worker(self.cid).payment_invoice(self.cid),invoice)
        self.assertEqual(self.s.record['jobs'][self.cid]['worker']['swaps'][self.cid]['contract'],original)

    def test_unapproved_status_history_and_repeat_prepare_never_mutate_remote(self):
        review=self.prepare();before=copy.deepcopy(self.remote.calls);saved=self.s.record['jobs']
        self.live();self.assertFalse(self.remote.entries)
        self.s.factory=lambda _:self.fail('cached status constructed a client')
        self.assertEqual(self.s.status()['swaps'][0]['confirmation'],self.cid)
        self.assertEqual(self.s.prepare('forward',self.remote.c['outgoing']['invoice']),review)
        self.assertEqual(self.remote.calls,before);self.assertEqual(self.s.record['jobs'],saved)
        raw=json.dumps(self.s.status())
        for n in self.bind['nodes'].values():
            for key in ('rune','ca_pem','url'):self.assertNotIn(n[key],raw)
        self.assertNotIn(self.remote.c['incoming']['payment_secret'],raw)

    def test_exact_confirmation_requires_live_fresh_runner_and_selected_pair(self):
        self.prepare()
        for confirmation in ('0'*64,self.cid):
            with self.assertRaises(ValueError):self.s.confirm(self.cid,confirmation,confirmed=True)
        self.live();self.now+=121
        with self.assertRaisesRegex(ValueError,'worker_unavailable'):self.s.confirm(self.cid,self.cid,confirmed=True)
        self.now=NOW-1
        with self.assertRaisesRegex(ValueError,'worker_unavailable'):self.s.confirm(self.cid,self.cid,confirmed=True)
        self.now=NOW
        b=copy.deepcopy(self.bind);b['nodes']['incoming']['url']='https://new.invalid'
        self.s.pair(b,confirmed=True)
        with self.assertRaisesRegex(ValueError,'review_superseded'):self.s.confirm(self.cid,self.cid,confirmed=True)
        self.assertFalse(self.remote.entries)

    def test_renewal_pause_and_settings_preserve_pending_generation(self):
        self.enrolled();self.remote.hold();self.s.tick('11'*32)
        saved=copy.deepcopy(self.s.record['jobs'][self.cid]);oldgen=saved['generation']
        b=copy.deepcopy(self.bind);b['nodes']['incoming']['url']='https://renewed.invalid'
        self.s.pair(b,confirmed=True);self.s.configure(paused=True,markup_bps=500)
        self.assertNotEqual(self.s.record['selected']['forward'],oldgen)
        self.assertEqual(self.s.record['jobs'][self.cid],saved)
        self.remote.complete();self.open()
        for _ in range(4):self.s.tick('11'*32)
        self.assertEqual(self.s.cached(self.cid)['state'],'settled')
        self.assertEqual(self.s.record['jobs'][self.cid]['generation'],oldgen)
        self.assertEqual(self.s.cached(self.cid)['markup_bps'],0)
        self.s.confirm(self.cid,self.cid,confirmed=True) # durable approval is idempotent
        self.assertEqual(sum(op=='send' for _,op,_ in self.remote.calls),1)

    def test_cancel_only_draft_preserves_history_and_refuses_same_invoice(self):
        self.prepare();self.s.cancel(self.cid,self.cid);self.open()
        self.assertEqual(self.s.cached(self.cid)['state'],'cancelled')
        with self.assertRaises(ValueError):self.s.confirm(self.cid,self.cid,confirmed=True)
        with self.assertRaises(ValueError):self.s.prepare('forward',self.remote.c['outgoing']['invoice'])
        self.live();self.assertFalse(self.remote.entries)

    def test_approved_cancel_restore_and_disk_uncertainty_fail_closed(self):
        self.enrolled()
        with self.assertRaises(ValueError):self.s.cancel(self.cid,self.cid)
        (self.root/'restored.json').write_text('{}');before=len(self.remote.calls)
        self.assertTrue(self.s.status()['restored'])
        for call in (lambda:self.s.tick('11'*32),lambda:self.s.worker(self.cid).payment_invoice(self.cid),
                     lambda:self.s.configure(paused=False),lambda:self.s.confirm(self.cid,self.cid,confirmed=True)):
            with self.assertRaises(ValueError):call()
        self.assertEqual(len(self.remote.calls),before)
        (self.root/'restored.json').unlink()
        with patch.object(self.j,'write',side_effect=OSError('private details')):
            with self.assertRaises(hg.StorageError):self.s.configure(markup_bps=1)
        with self.assertRaises(ValueError):self.s.tick('11'*32)
        self.open();self.assertEqual(self.s.record['markup_bps'],0)

    def test_pair_checks_both_identities_expiry_and_keeps_original_on_failure(self):
        self.prepare();saved=copy.deepcopy(self.s.record)
        self.remote.obs['outgoing']['info']['grant']['nonce']='ff'*32
        with self.assertRaises(ValueError):self.s.pair(self.bind,confirmed=True)
        self.assertEqual(saved,self.s.record)
        self.remote.obs['outgoing']['info']['grant']=self.bind['nodes']['outgoing']['grant']
        self.now=self.bind['nodes']['incoming']['grant']['expires_at']
        with self.assertRaises(ValueError):self.s.pair(self.bind,confirmed=True)
        self.assertEqual(saved,self.s.record)

    def test_extra_fields_proxy_fault_flags_and_missing_state_refused(self):
        self.j.close();self.j=None
        for req in (dict(action='sendpay'),dict(action='status',fault=True),dict(action='prepare',direction='forward',invoice='x',contract={})):
            with self.assertRaises(ValueError):service_cli.execute(self.root,req)
        before=(self.root/'state.json').read_bytes()
        with self.assertRaises(FileExistsError):service.initialize(self.root)
        self.assertEqual(before,(self.root/'state.json').read_bytes())
        (self.root/'state.json').unlink()
        with self.assertRaises(FileNotFoundError):service_cli.execute(self.root,dict(action='status'))

    def test_history_capacity_reserves_space_for_enrolled_recovery(self):
        self.enrolled();saved=copy.deepcopy(self.s.record['jobs'][self.cid])
        for i in range(16):
            b=copy.deepcopy(self.bind)
            for kind in service.cw.KINDS:
                b['nodes'][kind]['url']='https://'+kind+str(i)+'.invalid'
                b['nodes'][kind]['ca_pem']='-----BEGIN CERTIFICATE-----\n'+'A'*60000+'\n-----END CERTIFICATE-----\n'
            before=copy.deepcopy(self.s.record)
            try:self.s.pair(b,confirmed=True)
            except ValueError as e:
                self.assertEqual(str(e),'service_history_full');self.assertEqual(before,self.s.record);break
        else:self.fail('capacity not bounded')
        self.assertEqual(self.s.record['jobs'][self.cid],saved)
        self.remote.hold();self.s.tick('11'*32);self.remote.complete()
        for _ in range(4):self.s.tick('11'*32)
        self.assertEqual(self.s.cached(self.cid)['state'],'settled')

    def test_independent_process_supervision_singleton_sigkill_and_restart(self):
        self.close();script=Path(service_cli.__file__)
        args=[sys.executable,str(script),'--disposable-regtest',str(self.root),'run']
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.addCleanup(lambda:proc.poll() is None and proc.kill())
        def read():
            try:return service_cli.execute(self.root,dict(action='status'))
            except BlockingIOError:return None
        def wait_fresh():
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                v=read()
                if v and v['worker_fresh']:return
                time.sleep(.02)
            self.fail('service did not start')
        wait_fresh()
        rival=subprocess.run(args,capture_output=True,timeout=5)
        self.assertNotEqual(rival.returncode,0)
        proc.kill();proc.communicate(timeout=5)
        self.assertFalse(read()['worker_fresh'])
        proc=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE);wait_fresh()
        proc.terminate();out,err=proc.communicate(timeout=5)
        self.assertEqual((proc.returncode,out,err),(0,b'',b''));self.assertFalse(read()['worker_fresh'])

    def test_market_adapter_uses_original_arithmetic_and_rejects_bad_snapshots(self):
        route=self.remote.c['outgoing']['route']
        data={'ticker':dict(success=True,pair='BTCB2_BTC',ticker=dict(computedAt=NOW*1000,bestBid='0.49',bestAsk='0.5')),
              'orderbook':dict(success=True,pair='BTCB2_BTC',asks=[dict(price='0.5',quantity='1')],bids=[dict(price='0.49',quantity='1')])}
        for d in service.DIRECTIONS:
            for markup in (0,100):
                self.assertEqual(service_market.make(d,route,markup,clock=lambda:NOW,reader=data.get),price(d,route,markup,clock=lambda:NOW))
        data['ticker']['ticker']['computedAt']-=30001
        with self.assertRaises(ValueError):service_market.make('forward',route,0,clock=lambda:NOW,reader=data.get)


if __name__=='__main__':unittest.main()
