"""Quote isolation, durable request ownership, lost replies and strict binding."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import held_gate as hg
import provider_api as api
import provider_quote as protocol
import service
from test_service import Tests as ServiceTests
from test_customer_https import nodes
from test_cross_worker import Remote


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=ServiceTests('test_invoice_confirm_pay_and_lost_replies_reload_exact_records')
        self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        f=self.fixture; f.remote=Remote('reverse'); f.remote.c['outgoing']['invoice']='lnbcrt1'+'a'*150; f.open()
        f.s.pair(dict(direction='reverse',nodes=nodes(f.remote)),confirmed=True); f.live()
        self.invoice=f.remote.c['outgoing']['invoice']; self.origin='https://provider.invalid'
        self.path=Path(f.tmp.name)/'requests'; api.initialize(self.path)
        self.j=hg.Journal(self.path); self.addCleanup(lambda:self.j.close())
        self.signed=[]
        def quote(invoice,rid):
            return api.preview(f.s,invoice,self.origin,rid)
        def sign(terms): self.signed.append(copy.deepcopy(terms)); return 'y'*104
        self.options=dict(quote=quote,sign=sign,clock=lambda:f.now)
        self.gateway=api.Gateway(self.j,**self.options)
        self.body=dict(request_id='11'*32,token='22'*32,invoice=self.invoice)

    def request(self): return self.gateway.execute('quote',self.body)

    def test_quote_is_read_only_and_does_not_leak_operator_authority(self):
        f=self.fixture; before=(f.root/'state.json').read_bytes(); calls=len(f.remote.calls)
        reply=self.request(); self.assertEqual(reply['state'],'quoted')
        self.assertEqual((f.root/'state.json').read_bytes(),before)
        self.assertEqual(f.s.record['jobs'],{})
        self.assertTrue(all(op in ('inspect','info','plan') for _,op,_ in f.remote.calls[calls:]))
        raw=json.dumps(reply)
        for secret in ('ca_pem','rune','approved_channels','payment_secret',self.invoice,self.body['token']):
            self.assertNotIn(secret,raw)
        terms=reply['quote']['terms']; self.assertFalse(terms['reserved']); self.assertFalse(terms['payment_available'])
        self.assertFalse(terms['buyer_routing_fee_included'])
        self.assertEqual(terms['btc_recipient_msat'],f.remote.c['outgoing']['amount_msat'])

    def test_lost_reply_restart_same_response_no_second_signature(self):
        first=self.request(); self.j.close(); self.j=hg.Journal(self.path)
        self.gateway=api.Gateway(self.j,**self.options)
        self.assertEqual(self.request(),first)
        self.assertEqual(self.gateway.execute('status',{k:self.body[k] for k in ('request_id','token')}),first)
        self.assertEqual(len(self.signed),1)
        self.fixture.now+=1000
        self.assertEqual(self.request(),first)  # Never refresh an expired quote under the same ID.

    def test_token_scope_conflict_and_unknown_do_not_reveal_quote(self):
        self.request(); before=(self.path/'state.json').read_bytes()
        for body in (dict(self.body,token='33'*32),dict(self.body,invoice=self.invoice+'x')):
            with self.assertRaises(ValueError): self.gateway.execute('quote',body)
        with self.assertRaises(ValueError):self.gateway.execute('status',dict(request_id='44'*32,token='22'*32))
        self.assertEqual((self.path/'state.json').read_bytes(),before)

    def test_pending_intent_never_reprices_after_crash(self):
        with patch.object(self.gateway,'quote',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.request()
        self.gateway=api.Gateway(self.j,**self.options)
        self.assertEqual(self.request()['state'],'pending'); self.assertEqual(self.signed,[])

    def test_uncertain_write_blocks_instance_and_preserves_durable_reply(self):
        original=self.j.write; count=0
        def write(record):
            nonlocal count
            count+=1; original(record)
            if count==2: raise OSError('lost fsync reply')
        with patch.object(self.j,'write',side_effect=write):
            with self.assertRaises(hg.StorageError): self.request()
        with self.assertRaises(ValueError):self.request()
        self.gateway=api.Gateway(self.j,**self.options)
        self.assertEqual(self.request()['state'],'quoted');self.assertEqual(len(self.signed),1)

    def test_pause_restore_failure_are_closed_and_preserve_operator_history(self):
        self.fixture.s.configure(paused=True)
        self.assertEqual(self.request()['state'],'unavailable')
        self.assertEqual(self.signed,[])
        with patch.object(api.restore_barrier,'blocked',return_value=True):
            with self.assertRaises(ValueError):self.gateway.execute('status',{k:self.body[k] for k in ('request_id','token')})

    def test_capacity_and_rate_preserve_old_status(self):
        first=self.request()
        for i in range(1,8):self.gateway.execute('quote',dict(self.body,request_id=f'{i+100:064x}'))
        with self.assertRaisesRegex(ValueError,'rate_limit'):
            self.gateway.execute('quote',dict(self.body,request_id='ff'*32))
        self.assertEqual(self.request(),first)

    def test_preview_denies_any_remote_mutation(self):
        f=self.fixture
        original=service.Service.prepare
        def malicious(s,direction,invoice):s.factory(nodes(f.remote)['incoming']).call('enroll',{})
        before=len(f.remote.calls)
        with patch.object(service.Service,'prepare',malicious):self.assertEqual(self.request()['state'],'unavailable')
        self.assertEqual(len(f.remote.calls),before)

    def test_buyer_checks_identity_origin_invoice_nonce_expiry_and_signature(self):
        reply=self.request()['quote']; t=reply['terms']; f=self.fixture
        from test_route_plan import Fake
        decoded=Fake().decoded
        # Use the controller plan fixture's decoded BTC invoice.
        decoded=copy.deepcopy(decoded)
        decoded.update(amount_msat=t['btc_recipient_msat'],payment_hash=t['payment_hash'],created_at=f.now-1,expiry=1000)
        def rpc(method,**params):
            if method=='decode':return decoded
            self.assertEqual(params['message'],protocol.message(t))
            return dict(verified=True,pubkey=t['provider'])
        args=dict(rpc=rpc,provider=t['provider'],origin=self.origin,environment='regtest',request_id=self.body['request_id'],invoice=self.invoice,now=f.now)
        self.assertTrue(protocol.verify(reply,**args)['verified'])
        for key,value in (('provider','02'+'aa'*32),('origin','https://other.invalid'),('invoice',self.invoice+'x'),('request_id','ff'*32),('now',t['expires_at'])):
            with self.assertRaises(ValueError):protocol.verify(reply,**dict(args,**{key:value}))
        with self.assertRaises(ValueError):
            protocol.verify(reply,**dict(args,rpc=lambda method,**kw:decoded if method=='decode' else dict(verified=False,pubkey=t['provider'])))
        for key,value in (('xbt_invoice_amount_msat',True),('reserved',True),('payment_available',True),('buyer_routing_fee_included',True)):
            with self.assertRaises(ValueError):protocol.message(dict(t,**{key:value}))


if __name__=='__main__':unittest.main()
