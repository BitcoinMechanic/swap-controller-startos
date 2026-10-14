"""Migration service: private atomic history and immutable per-swap HTTPS authority.

Disposable regtest only. This entry point is deliberately separate from installed
StartOS actions and backup paths until the migrated node package is validated.
"""
import copy
import fcntl
import json
import os
from pathlib import Path
import time

import bounded_authority as ba
import cross_contract as cc
import cross_worker as cw
import customer_https as https
import held_gate as hg
import restore_barrier
import worker_pairing as wp

FORMAT = 'swap-controller-service-v1'
DIRECTIONS = ('forward', 'reverse')
MAX_JOBS = 32
MAX_PAIRINGS = 16


def empty():
    return dict(schema=1, format=FORMAT, selected={d:None for d in DIRECTIONS},
                pairings={}, jobs={}, paused=False, markup_bps=0, heartbeat=None)


def validate(r):
    hg.need(type(r) is dict and set(r)==set(empty()) and type(r['schema']) is int
            and r['schema']==1 and r['format']==FORMAT, 'service_record_invalid')
    hg.need(type(r['paused']) is bool and hg.integer(r['markup_bps'],0,500), 'service_record_invalid')
    hg.need(type(r['pairings']) is dict and len(r['pairings'])<=MAX_PAIRINGS
            and type(r['jobs']) is dict and len(r['jobs'])<=MAX_JOBS, 'service_record_invalid')
    for gid,b in r['pairings'].items():
        wp.binding(b); hg.need(gid==ba.digest(b),'service_pairing_changed')
    hg.need(type(r['selected']) is dict and set(r['selected'])==set(DIRECTIONS),'service_record_invalid')
    for d,gid in r['selected'].items():
        hg.need(gid is None or (gid in r['pairings'] and r['pairings'][gid]['direction']==d),'service_pairing_changed')
    hashes=set()
    for cid,j in r['jobs'].items():
        hg.need(type(j) is dict and set(j)=={'generation','worker','cancelled','error'}
                and j['generation'] in r['pairings'] and type(j['cancelled']) is bool
                and j['error'] in (None,'coordinator_unavailable','recovery_refused'), 'service_record_invalid')
        w=cw.validate(j['worker']); hg.need(set(w['swaps'])=={cid},'service_record_invalid')
        s=w['swaps'][cid]; c=s['contract']; b=r['pairings'][j['generation']]
        hg.need(c['direction']==b['direction'] and ba.equal(c['grants'],{k:n['grant'] for k,n in b['nodes'].items()}),
                'service_pairing_changed')
        hg.need(not j['cancelled'] or set(s)=={'contract','enroll','remote'},'service_cancelled_execution')
        ph=c['incoming']['payment_hash']; hg.need(ph not in hashes,'service_duplicate_payment'); hashes.add(ph)
    h=r['heartbeat']
    hg.need(h is None or (type(h) is dict and set(h)=={'run_id','at'} and ba.ci.hex_field(h['run_id'],64)
            and hg.integer(h['at'],1,2**35-1)), 'service_heartbeat_invalid')
    return r


def initialize(directory):
    directory=Path(directory)
    hg.need(not any(p.is_symlink() for p in (directory,*directory.parents)), 'service_path_refused')
    hg.need(not restore_barrier.blocked(directory), "service_restored")
    directory.mkdir(mode=0o700)  # Refuse existing state; never auto-initialize recovery.
    fd=os.open(directory/'state.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(empty(),f); f.flush(); os.fsync(f.fileno())
    for p in (directory,directory.parent):
        fd=os.open(p,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)


class Memory:
    def __init__(self, journal, record):
        self.directory=journal.directory; self.lock_fd=journal.lock_fd; self.record=copy.deepcopy(record)
    def read(self): return copy.deepcopy(self.record)
    def write(self, record): self.record=copy.deepcopy(record)


class Projection(Memory):
    def __init__(self, service, cid):
        self.service,self.cid=service,cid
        super().__init__(service.journal,service.record['jobs'][cid]['worker'])
    def write(self, record):
        r=copy.deepcopy(self.service.record); r['jobs'][self.cid]['worker']=copy.deepcopy(record)
        self.service.commit(r); self.record=copy.deepcopy(record)


def runner_fd(directory, *, create=False):
    # Validate the private state directory and lock path before acquiring the
    # separate lifetime lock. The per-action journal lock is never held here.
    j=hg.Journal(directory)
    try:
        j.read()
        return_fd=os.open(j.directory/'runner.lock',os.O_RDWR|os.O_NOFOLLOW|(os.O_CREAT if create else 0),0o600)
        try: hg.Journal.check_file(return_fd)
        except BaseException: os.close(return_fd); raise
        return return_fd
    finally: j.close()


def runner_alive(directory):
    # Called while the journal is held; do not open a second Journal here.
    try: fd=os.open(Path(directory)/'runner.lock',os.O_RDWR|os.O_NOFOLLOW)
    except FileNotFoundError: return False
    try:
        hg.Journal.check_file(fd)
        try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return True
        return False
    finally: os.close(fd)


class Service:
    def __init__(self,journal,*,factory=https.Client,clock=time.time,pricer=None):
        self.journal,self.factory,self.clock=journal,factory,clock
        self.record=copy.deepcopy(validate(journal.read())); self.blocked=False; self.pricer=pricer

    def guard(self, *, admission=False):
        hg.need(not self.blocked and self.journal.lock_fd is not None,'service_blocked')
        hg.need(not restore_barrier.blocked(self.journal.directory),'service_restored')
        if admission: hg.need(not self.record['paused'],'service_paused')

    def commit(self,r):
        self.guard(); validate(r)
        try: self.journal.write(r)
        except Exception:
            self.blocked=True; raise hg.StorageError('service_write_uncertain') from None
        self.record=copy.deepcopy(r)

    def capacity(self,r):
        # Reserve growth before admitting anything. History/pairing additions
        # must not consume the space an existing swap needs for recovery.
        budget=len(json.dumps(r,sort_keys=True,separators=(',',':')).encode())+4096
        for cid,j in r['jobs'].items():
            w=cw.Worker(Memory(self.journal,j['worker']),lambda *a:None,self.clock)
            if not j['cancelled'] and not w.status(cid)['terminal']:
                c=j['worker']['swaps'][cid]['contract']
                budget+=262144+8*len(json.dumps(c,sort_keys=True,separators=(',',':')).encode())
        hg.need(budget<hg.MAX_BYTES,'service_history_full')

    def pair(self,binding,*,confirmed=False):
        self.guard(admission=True); hg.need(confirmed is True,'confirmation_required')
        b=copy.deepcopy(wp.binding(binding)); gid=ba.digest(b)
        hg.need(gid in self.record['pairings'] or len(self.record['pairings'])<MAX_PAIRINGS,'service_history_full')
        # Always authenticate both endpoints before selecting even a saved pair.
        m=Memory(self.journal,dict(schema=1,format=wp.FORMAT,binding=None))
        wp.Pairing(m,factory=self.factory,clock=self.clock).save(b,confirmed=True)
        r=copy.deepcopy(self.record); r['pairings'][gid]=b; r['selected'][b['direction']]=gid; self.capacity(r); self.commit(r)
        return dict(direction=b['direction'],generation=gid,paired=True,existing_swaps_unchanged=True)

    def worker(self,cid):
        hg.need(cid in self.record['jobs'],'service_swap_unknown')
        j=self.record['jobs'][cid]; b=self.record['pairings'][j['generation']]
        p=wp.Pairing(Memory(self.journal,dict(schema=1,format=wp.FORMAT,binding=b)),factory=self.factory,clock=self.clock)
        return p.worker(Projection(self,cid))

    def cached(self,cid):
        hg.need(cid in self.record['jobs'],'service_swap_unknown')
        j=self.record['jobs'][cid]
        # No client construction or network calls on status/history.
        w=cw.Worker(Memory(self.journal,j['worker']),lambda *a: hg.need(False,'cached_status_rpc_refused'),self.clock)
        v=w.status(cid); v.update(cc.review(j['worker']['swaps'][cid]['contract']))
        v.update(generation=j['generation'],selected=self.record['selected'][v['direction']]==j['generation'],
                 cancelled=j['cancelled'],error=j['error'])
        if j['cancelled']: v.update(state='cancelled',terminal=True)
        v['blocked'] |= self.blocked or restore_barrier.blocked(self.journal.directory)
        if 'recovery_retirement' in j['worker']['swaps'][cid] and j['error'] is not None:
            v['blocked'] = True
            v['recovery_recheck_required'] = True
        return v

    def status(self):
        h=self.record['heartbeat']; now=int(self.clock())
        fresh=h is not None and 0<=now-h['at']<=120 and runner_alive(self.journal.directory)
        return dict(format=FORMAT,read_only=True,regtest_only=True,paused=self.record['paused'],
                    restored=restore_barrier.blocked(self.journal.directory),worker_fresh=fresh,
                    markup_bps=self.record['markup_bps'],selected=copy.deepcopy(self.record['selected']),
                    grants={direction: None if gid is None else {kind:dict(
                        expires_at=node['grant']['expires_at'],expired=now>=node['grant']['expires_at'],
                        limits=copy.deepcopy(node['grant']['limits']),remaining_slots=None,
                        current_authority='Checked during pairing and new admission')
                        for kind,node in self.record['pairings'][gid]['nodes'].items()}
                        for direction,gid in self.record['selected'].items()},
                    swaps=[self.cached(cid) for cid in self.record['jobs']])

    def prepare(self,direction,invoice):
        self.guard(admission=True); hg.need(direction in DIRECTIONS,'service_direction_invalid')
        hg.need(type(invoice) is str and 1<=len(invoice)<=20000,'service_invoice_invalid')
        for cid,j in self.record['jobs'].items():
            saved=j['worker']['swaps'][cid]
            if 'recovery_retirement' in saved: self.worker(cid).check_recovery(cid)
            c=saved['contract']
            if c['direction']==direction and c['outgoing']['invoice']==invoice:
                hg.need(not j['cancelled'],'service_invoice_reused'); return self.cached(cid)
            hg.need(c['direction']!=direction or self.cached(cid)['terminal'],'service_previous_swap_active')
        gid=self.record['selected'][direction]; hg.need(gid is not None,'service_pairing_required')
        hg.need(len(self.record['jobs'])<MAX_JOBS,'service_history_full')
        b=self.record['pairings'][gid]; plan=self.factory(b['nodes']['outgoing']).call('plan',dict(invoice=invoice))
        if self.pricer is None:
            from service_market import make
            pricer=make
        else: pricer=self.pricer
        q=pricer(direction,plan['route'],self.record['markup_bps'],clock=self.clock)
        c=cc.create({k:n['grant'] for k,n in b['nodes'].items()},plan,q,now=int(self.clock()))
        hg.need(all(c['incoming']['payment_hash']!=j['worker']['swaps'][cid]['contract']['incoming']['payment_hash']
                    for cid,j in self.record['jobs'].items()),'service_invoice_reused')
        m=Memory(self.journal,dict(schema=1,format=cw.FORMAT,swaps={}))
        p=wp.Pairing(Memory(self.journal,dict(schema=1,format=wp.FORMAT,binding=b)),factory=self.factory,clock=self.clock)
        cid=p.worker(m).prepare(c)  # inspections only, staged entirely in memory
        r=copy.deepcopy(self.record); r['jobs'][cid]=dict(generation=gid,worker=m.read(),cancelled=False,error=None)
        self.capacity(r); self.commit(r); return self.cached(cid)

    def confirm(self,cid,confirmation,*,confirmed=False):
        self.guard(); hg.need(confirmed is True and cid==confirmation,'confirmation_required')
        hg.need(cid in self.record['jobs'],'service_swap_unknown'); j=self.record['jobs'][cid]
        hg.need(not j['cancelled'],'service_draft_cancelled')
        if 'approval' not in j['worker']['swaps'][cid]:
            self.guard(admission=True)
            d=j['worker']['swaps'][cid]['contract']['direction']
            hg.need(self.record['selected'][d]==j['generation'],'service_review_superseded')
            hg.need(self.status()['worker_fresh'],'service_worker_unavailable')
        self.worker(cid).confirm(cid,confirmation); return self.cached(cid)

    def cancel(self,cid,confirmation):
        self.guard(); hg.need(cid==confirmation and cid in self.record['jobs'],'confirmation_required')
        j=self.record['jobs'][cid]
        hg.need('approval' not in j['worker']['swaps'][cid],'service_approved_swap_cannot_cancel')
        r=copy.deepcopy(self.record); r['jobs'][cid]['cancelled']=True; self.commit(r); return self.cached(cid)

    def configure(self,*,paused=None,markup_bps=None):
        self.guard(); r=copy.deepcopy(self.record)
        if paused is not None:
            hg.need(type(paused) is bool,'service_settings_invalid'); r['paused']=paused
        if markup_bps is not None:
            hg.need(hg.integer(markup_bps,0,500),'service_settings_invalid'); r['markup_bps']=markup_bps
        self.commit(r); return dict(paused=r['paused'],markup_bps=r['markup_bps'],new_swaps_only=True)

    def recovery_review(self,cid):
        self.guard()
        return self.worker(cid).recovery_review(cid)

    def recovery_confirm(self,cid,confirmation,confirmed):
        self.guard()
        return self.worker(cid).recovery_confirm(cid,confirmation,confirmed)

    def tick(self,run_id):
        self.guard(); r=copy.deepcopy(self.record); r['heartbeat']=dict(run_id=run_id,at=int(self.clock())); self.commit(r)
        for cid in list(self.record['jobs']):
            j=self.record['jobs'][cid]
            saved=j['worker']['swaps'][cid]
            if (self.cached(cid)['terminal'] and 'recovery_retirement' not in saved) or 'approval' not in saved: continue
            error=None
            try: self.worker(cid).step(cid)
            except hg.StorageError: raise  # Fresh process must reread uncertain disk state.
            except https.HttpsError: error='coordinator_unavailable'
            except (ValueError,TimeoutError,OSError): error='recovery_refused'
            r=copy.deepcopy(self.record); r['jobs'][cid]['error']=error
            r['heartbeat']=dict(run_id=run_id,at=int(self.clock())); self.commit(r)
        return self.status()
