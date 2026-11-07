"""Durable quote ownership and exact acceptance; operator authority stays private."""
import copy
import hashlib
import hmac
import json
import time

import bounded_authority as ba
import cross_contract as cc
import held_gate as hg
import provider_quote as protocol
import restore_barrier
import service

LEGACY_FORMAT='merchant-provider-requests-v1'
FORMAT='merchant-provider-requests-v2'
MAX_REQUESTS=128


def initialize(directory):
    service.initialize(directory)
    journal=hg.Journal(directory)
    try: journal.write(dict(format=FORMAT,requests={}))
    finally: journal.close()


def validate_offer(offer):
    hg.need(type(offer) is dict and set(offer)=={'terms','contract','generation'},'provider_offer_invalid')
    t=protocol.terms(offer['terms']);c=cc.validate(offer['contract'])
    hg.need(t['format']==protocol.ACCEPT_FORMAT and c['direction']=='reverse'
            and ba.ci.hex_field(offer['generation'],64),'provider_offer_invalid')
    expected=dict(format=protocol.ACCEPT_FORMAT,acceptance_available=True,request_id=t['request_id'],
        provider=c['incoming']['chain']['node_id'],origin=t['origin'],environment=c['incoming']['chain']['environment'],
        invoice_sha256=protocol.digest_invoice(c['outgoing']['invoice']),payment_hash=c['incoming']['payment_hash'],
        btc_recipient_msat=c['outgoing']['amount_msat'],xbt_invoice_amount_msat=c['incoming']['amount_msat'],
        btc_routing_fee_msat=c['pricing']['routing_fee_msat'],markup_bps=c['pricing']['markup_bps'],
        created_at=c['created_at'],expires_at=c['pay_until'],contract_digest=cc.digest(c),
        buyer_routing_fee_included=False,payment_available=False,reserved=False)
    hg.need(ba.equal(t,expected),'provider_offer_changed')
    return offer


def validate(record):
    hg.need(type(record) is dict and set(record)=={'format','requests'} and record['format'] in (FORMAT,LEGACY_FORMAT)
            and type(record['requests']) is dict and len(record['requests'])<=MAX_REQUESTS,'provider_record_invalid')
    legacy=record['format']==LEGACY_FORMAT
    for rid,row in record['requests'].items():
        hg.need(ba.ci.hex_field(rid,64) and type(row) is dict and set(row)=={
            'token_hash','invoice_hash','created_at','reply','state'} | (set() if legacy else {'offer','acceptance'}),
            'provider_record_invalid')
        states=('pending','quoted','unavailable') if legacy else ('pending','quoted','unavailable','accepting','accepted')
        hg.need(all(ba.ci.hex_field(row[k],64) for k in ('token_hash','invoice_hash'))
                and hg.integer(row['created_at'],1,2**35-1) and row['state'] in states,'provider_record_invalid')
        has_quote=row['state'] in ('quoted','accepting','accepted')
        hg.need((row['reply'] is not None)==has_quote,'provider_record_invalid')
        if has_quote:
            protocol.envelope(row['reply']);t=row['reply']['terms']
            hg.need(t['request_id']==rid and t['invoice_sha256']==row['invoice_hash'], 'provider_record_invalid')
            if legacy: hg.need(t['format']==protocol.FORMAT,'provider_record_invalid')
        if not legacy:
            hg.need((row['offer'] is not None)==has_quote,'provider_record_invalid')
            if has_quote:
                validate_offer(row['offer']);hg.need(ba.equal(row['offer']['terms'],t),'provider_offer_changed')
            accepted=row['state'] in ('accepting','accepted')
            hg.need((row['acceptance'] is not None)==accepted,'provider_record_invalid')
            if accepted:
                a=row['acceptance']
                hg.need(type(a) is dict and set(a)=={'confirmation','at'}
                        and a['confirmation']==protocol.confirmation(row['reply'])
                        and hg.integer(a['at'],t['created_at'],t['expires_at']-1),'provider_acceptance_changed')
    return record


def preview(controller, invoice, origin, request_id):
    """All Service.prepare writes land in Memory; enforce a read-only RPC facade."""
    controller.guard(admission=True)
    hg.need(not any(j['worker']['swaps'][cid]['contract']['outgoing']['invoice']==invoice
                    for cid,j in controller.record['jobs'].items()),'provider_invoice_reused')
    hg.need(controller.status()['worker_fresh'],'service_worker_unavailable')
    class ReadOnly:
        def __init__(self,config): self.client=controller.factory(config)
        def call(self,op,body=None):
            hg.need(op in ('info','plan','inspect'),'provider_mutation_refused')
            return self.client.call(op,body)
    staged=service.Service(service.Memory(controller.journal,controller.record),factory=ReadOnly,
                           clock=controller.clock,pricer=controller.pricer)
    reply=staged.prepare('reverse',invoice)
    c=staged.record['jobs'][reply['confirmation']]['worker']['swaps'][reply['confirmation']]['contract']
    hg.need(c['outgoing']['invoice']==invoice,'provider_invoice_changed')
    terms=protocol.terms(dict(format=protocol.ACCEPT_FORMAT,acceptance_available=True,request_id=request_id,
        provider=c['incoming']['chain']['node_id'],origin=origin,environment=service.environment(controller.record),
        invoice_sha256=protocol.digest_invoice(invoice),payment_hash=c['incoming']['payment_hash'],
        btc_recipient_msat=c['outgoing']['amount_msat'],xbt_invoice_amount_msat=c['incoming']['amount_msat'],
        btc_routing_fee_msat=c['pricing']['routing_fee_msat'],markup_bps=c['pricing']['markup_bps'],
        created_at=c['created_at'],expires_at=c['pay_until'],contract_digest=reply['confirmation'],
        buyer_routing_fee_included=False,payment_available=False,reserved=False))

    return dict(terms=terms,contract=copy.deepcopy(c),generation=staged.record['jobs'][reply['confirmation']]['generation'])


class Gateway:
    def __init__(self,journal,*,quote,sign,accept=None,observe=None,clock=time.time):
        self.journal,self.quote,self.sign,self.clock=journal,quote,sign,clock
        self.accept,self.observe=accept,observe
        self.record=validate(journal.read());self.blocked=False

    def commit(self,record):
        hg.need(not self.blocked,'provider_storage_uncertain');validate(record)
        try:self.journal.write(record)
        except Exception:
            self.blocked=True
            raise hg.StorageError('provider_storage_uncertain') from None
        self.record=copy.deepcopy(record)

    def save(self,rid,row):
        r=copy.deepcopy(self.record);r['requests'][rid]=row;self.commit(r)

    def execute(self,operation,body):
        hg.need(not self.blocked and not restore_barrier.blocked(self.journal.directory),'provider_blocked')
        extra={'invoice'} if operation=='quote' else {'confirmation','confirmed'} if operation=='accept' else set()
        hg.need(operation in ('quote','status','accept') and type(body) is dict
                and set(body)=={'request_id','token'}|extra,'provider_request_invalid')
        rid,token=body['request_id'],body['token']
        hg.need(ba.ci.hex_field(rid,64) and ba.ci.hex_field(token,64),'provider_request_invalid')
        token_hash=hashlib.sha256(token.encode()).hexdigest()
        invoice_hash=protocol.digest_invoice(body['invoice']) if operation=='quote' else None
        row=self.record['requests'].get(rid)
        if row is not None:
            hg.need(hmac.compare_digest(row['token_hash'],token_hash),'provider_request_unknown')
            hg.need(invoice_hash is None or invoice_hash==row['invoice_hash'],'provider_request_conflict')
        else:
            hg.need(operation=='quote','provider_request_unknown')
            hg.need(self.record['format']==FORMAT,'provider_legacy_read_only')
            now=int(self.clock())
            hg.need(len(self.record['requests'])<MAX_REQUESTS
                    and len(json.dumps(self.record))+65536<hg.MAX_BYTES,'provider_capacity')
            hg.need(sum(now-r['created_at']<60 for r in self.record['requests'].values())<8,'provider_rate_limit')
            row=dict(token_hash=token_hash,invoice_hash=invoice_hash,created_at=now,reply=None,state='pending',
                     offer=None,acceptance=None)
            self.save(rid,row)
            try:
                offer=validate_offer(self.quote(body['invoice'],rid));terms=offer['terms']
                hg.need(terms['request_id']==rid and terms['invoice_sha256']==invoice_hash,'provider_quote_changed')
                signed=protocol.envelope(dict(terms=terms,signature=self.sign(terms)))
            except Exception: row=dict(row,state='unavailable')
            else: row=dict(row,state='quoted',reply=signed,offer=offer)
            self.save(rid,row)
        if operation=='accept':
            hg.need(self.record['format']==FORMAT and row['offer'] is not None,'provider_estimate_only')
            hg.need(body['confirmed'] is True and body['confirmation']==protocol.confirmation(row['reply']),
                    'provider_confirmation_required')
            hg.need(self.accept is not None,'provider_acceptance_unavailable')
            if row['acceptance'] is None:
                now=int(self.clock());t=row['reply']['terms']
                hg.need(t['created_at']<=now<t['expires_at'],'provider_quote_expired')
                row=dict(row,state='accepting',acceptance=dict(confirmation=body['confirmation'],at=now))
                self.save(rid,row)  # durable exact intent before the controller write
            if row['state']=='accepting':
                try:self.accept(row['offer'])
                except Exception:pass  # Outcome may be committed. Status/retry uses the same contract.
                else:
                    row=dict(row,state='accepted');self.save(rid,row)
        result=dict(request_id=rid,state=row['state'],quote=copy.deepcopy(row['reply']))
        if self.record['format']==FORMAT:
            observed=None
            if row['state'] in ('accepting','accepted') and self.observe is not None:
                try:observed=self.observe(row['offer'])
                except Exception:pass
                if observed is not None and row['state']=='accepting':
                    row=dict(row,state='accepted');self.save(rid,row);result['state']='accepted'
            result['swap']=observed
        return result
