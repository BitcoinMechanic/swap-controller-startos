"""Private opt-in customer quote gateway; no execution, pairing or payment methods.

The separate journal keeps bearer-capability requests durable without consuming
operator jobs. An uncertain request is never silently repriced. The HTTP server
is single-threaded, bounded, TLS-only, and must be explicitly provisioned.
"""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import time

import bounded_authority as ba
import held_gate as hg
import provider_quote as protocol
import restore_barrier
import service

FORMAT='merchant-provider-requests-v1'
MAX_REQUESTS=128


def initialize(directory):
    # Reuse the proven exclusive/private/fsync initialization, then replace its
    # empty record before this new, separate directory can serve requests.
    service.initialize(directory)
    journal=hg.Journal(directory)
    try: journal.write(dict(format=FORMAT,requests={}))
    finally: journal.close()


def validate(record):
    hg.need(type(record) is dict and set(record)=={'format','requests'} and record['format']==FORMAT
            and type(record['requests']) is dict and len(record['requests'])<=MAX_REQUESTS,'provider_record_invalid')
    for rid,row in record['requests'].items():
        hg.need(ba.ci.hex_field(rid,64) and type(row) is dict and set(row)=={
            'token_hash','invoice_hash','created_at','reply','state'},'provider_record_invalid')
        hg.need(all(ba.ci.hex_field(row[k],64) for k in ('token_hash','invoice_hash'))
                and hg.integer(row['created_at'],1,2**35-1) and row['state'] in ('pending','quoted','unavailable'),
                'provider_record_invalid')
        hg.need((row['reply'] is not None)==(row['state']=='quoted'),'provider_record_invalid')
        if row['reply'] is not None:
            protocol.envelope(row['reply']); t=row['reply']['terms']
            hg.need(t['request_id']==rid and t['invoice_sha256']==row['invoice_hash'], 'provider_record_invalid')
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
    return protocol.terms(dict(format=protocol.FORMAT,request_id=request_id,
        provider=c['incoming']['chain']['node_id'],origin=origin,environment=service.environment(controller.record),
        invoice_sha256=protocol.digest_invoice(invoice),payment_hash=c['incoming']['payment_hash'],
        btc_recipient_msat=c['outgoing']['amount_msat'],xbt_invoice_amount_msat=c['incoming']['amount_msat'],
        btc_routing_fee_msat=c['pricing']['routing_fee_msat'],markup_bps=c['pricing']['markup_bps'],
        created_at=c['created_at'],expires_at=c['pay_until'],contract_digest=reply['confirmation'],
        buyer_routing_fee_included=False,payment_available=False,reserved=False))


class Gateway:
    def __init__(self,journal,*,quote,sign,clock=time.time):
        self.journal,self.quote,self.sign,self.clock=journal,quote,sign,clock
        self.record=validate(journal.read()); self.blocked=False

    def commit(self,record):
        hg.need(not self.blocked,'provider_storage_uncertain')
        validate(record)
        try: self.journal.write(record)
        except Exception:
            self.blocked=True
            raise hg.StorageError('provider_storage_uncertain') from None
        self.record=copy.deepcopy(record)

    def execute(self,operation,body):
        hg.need(not self.blocked and not restore_barrier.blocked(self.journal.directory),'provider_blocked')
        fields={'request_id','token'}|({'invoice'} if operation=='quote' else set())
        hg.need(operation in ('quote','status') and type(body) is dict and set(body)==fields,'provider_request_invalid')
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
            now=int(self.clock())
            hg.need(len(self.record['requests'])<MAX_REQUESTS,'provider_capacity')
            hg.need(sum(now-r['created_at']<60 for r in self.record['requests'].values())<8,'provider_rate_limit')
            row=dict(token_hash=token_hash,invoice_hash=invoice_hash,created_at=now,reply=None,state='pending')
            r=copy.deepcopy(self.record); r['requests'][rid]=row; self.commit(r)
            # Intent is durable before external reads/signing. A crash leaves
            # pending; repeat requests report it and never generate new terms.
            try:
                terms=self.quote(body['invoice'],rid)
                hg.need(terms['request_id']==rid and terms['invoice_sha256']==invoice_hash,'provider_quote_changed')
                signed=protocol.envelope(dict(terms=terms,signature=self.sign(terms)))
            except Exception:
                row=dict(row,state='unavailable')
            else: row=dict(row,state='quoted',reply=signed)
            r=copy.deepcopy(self.record); r['requests'][rid]=row; self.commit(r)
        return dict(request_id=rid,state=row['state'],quote=copy.deepcopy(row['reply']))
