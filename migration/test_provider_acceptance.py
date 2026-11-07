"""Exact acceptance under crash, concurrency, expiry and operator changes."""
import copy
import json
import unittest
from unittest.mock import patch

import provider_api as api
import provider_quote as protocol
from test_provider_api import Tests as QuoteTests


class Tests(unittest.TestCase):
    def setUp(self):
        self.fixture=QuoteTests('test_quote_is_read_only_and_does_not_leak_operator_authority')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture.fixture;self.g=self.fixture.gateway
        self.g.accept=self.accept;self.g.observe=self.observe
        self.request=self.fixture.body;self.reply=self.g.execute('quote',self.request)
        self.body={k:self.request[k] for k in ('request_id','token')}
        self.body.update(confirmation=protocol.confirmation(self.reply['quote']),confirmed=True)
    def accept(self,offer):
        self.f.remote.c=copy.deepcopy(offer['contract'])
        return self.f.s.accept_quote(offer['contract'],offer['generation'],confirmation=offer['terms']['contract_digest'])
    def observe(self,offer):
        cid=offer['terms']['contract_digest']
        if cid not in self.f.s.record['jobs']:return None
        s=self.f.s.cached(cid)
        return dict(contract_digest=cid,state=s['state'],terminal=s['terminal'],blocked=s['blocked'],
                    observed_at=self.f.now,payment_invoice=None)
    def reload(self):
        self.f.open()
        self.g=api.Gateway(self.fixture.j,**self.fixture.options,accept=self.accept,observe=self.observe)
    def test_exact_acceptance_is_atomic_and_does_not_enroll_or_pay(self):
        before=len(self.f.remote.calls);answer=self.g.execute('accept',self.body)
        self.assertEqual(answer['state'],'accepted');self.assertEqual(len(self.f.s.record['jobs']),1)
        job=next(iter(self.f.s.record['jobs'].values()))
        swap=next(iter(job['worker']['swaps'].values()))
        self.assertIn('approval',swap);self.assertEqual(swap['enroll'],{})
        self.assertTrue(all(op in ('info','inspect') for _,op,_ in self.f.remote.calls[before:]))
        saved=(self.f.root/'state.json').read_bytes();calls=len(self.f.remote.calls)
        self.g.execute('accept',self.body)
        self.assertEqual((self.f.root/'state.json').read_bytes(),saved);self.assertEqual(len(self.f.remote.calls),calls)
    def test_crash_after_approval_status_recovers_without_another_acceptance(self):
        def crash(offer):self.accept(offer);raise KeyboardInterrupt()
        self.g.accept=crash
        with self.assertRaises(KeyboardInterrupt):self.g.execute('accept',self.body)
        self.reload();self.f.now+=1000
        self.assertEqual(self.g.execute('status',{k:self.body[k] for k in ('request_id','token')})['state'],'accepted')
        self.assertEqual(len(self.f.s.record['jobs']),1)
        self.assertEqual(self.g.execute('accept',self.body)['state'],'accepted')
    def test_uncertain_controller_write_recovers_exact_durable_approval(self):
        write=self.f.j.write
        def uncertain(record):write(record);raise OSError('uncertain reply')
        with patch.object(self.f.j,'write',side_effect=uncertain):
            self.assertEqual(self.g.execute('accept',self.body)['state'],'accepting')
        self.reload();self.assertEqual(self.g.execute('accept',self.body)['state'],'accepted')
        self.assertEqual(len(self.f.s.record['jobs']),1)
    def test_crash_before_admission_can_retry_same_contract_only(self):
        self.g.accept=lambda _:(_ for _ in ()).throw(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):self.g.execute('accept',self.body)
        self.assertEqual(self.f.s.record['jobs'],{});self.reload()
        self.assertEqual(self.g.execute('status',{k:self.body[k] for k in ('request_id','token')})['state'],'accepting')
        self.assertEqual(self.f.s.record['jobs'],{})
        self.assertEqual(self.g.execute('accept',self.body)['state'],'accepted')
    def test_wrong_confirmation_foreign_token_and_changed_contract_refused(self):
        before=(self.f.root/'state.json').read_bytes()
        for fields in (dict(confirmation='ff'*32),dict(confirmed=False),dict(token='ff'*32)):
            with self.assertRaises(ValueError):self.g.execute('accept',dict(self.body,**fields))
        self.assertEqual((self.f.root/'state.json').read_bytes(),before)
        row=copy.deepcopy(self.g.record);row['requests'][self.body['request_id']]['offer']['contract']['pricing']['xbt_msat']+=1000
        with self.assertRaises(ValueError):api.validate(row)
    def test_old_estimates_cannot_be_upgraded_to_acceptable_offers(self):
        row=copy.deepcopy(self.g.record['requests'][self.body['request_id']]);row.pop('offer');row.pop('acceptance')
        t=row['reply']['terms'];t['format']=protocol.FORMAT;t.pop('acceptance_available')
        legacy=dict(format=api.LEGACY_FORMAT,requests={self.body['request_id']:row})
        self.fixture.j.write(legacy);self.reload()
        with self.assertRaisesRegex(ValueError,'estimate_only'):self.g.execute('accept',self.body)
        self.assertEqual(self.f.s.record['jobs'],{})
        self.assertEqual(self.g.execute('status',{k:self.body[k] for k in ('request_id','token')})['quote'],row['reply'])
    def test_expiry_pause_and_pairing_change_do_not_admit(self):
        self.f.s.configure(paused=True)
        self.assertEqual(self.g.execute('accept',self.body)['state'],'accepting')
        self.assertEqual(self.f.s.record['jobs'],{})
        self.f.s.configure(paused=False)
        r=copy.deepcopy(self.f.s.record);r['selected']['reverse']=None;self.f.s.commit(r)
        self.g.execute('accept',self.body);self.assertEqual(self.f.s.record['jobs'],{})
        self.f.now+=1000
        self.g.execute('accept',self.body);self.assertEqual(self.f.s.record['jobs'],{})
    def test_first_accept_after_expiry_has_no_intent_or_job(self):
        self.f.now=self.reply['quote']['terms']['expires_at']
        with self.assertRaisesRegex(ValueError,'expired'):self.g.execute('accept',self.body)
        self.assertIsNone(self.g.record['requests'][self.body['request_id']]['acceptance'])
        self.assertEqual(self.f.s.record['jobs'],{})
    def test_competing_quotes_cannot_create_duplicate_invoice_jobs(self):
        second=self.g.execute('quote',dict(self.request,request_id='ff'*32))
        self.g.execute('accept',self.body)
        request=dict(self.body,request_id='ff'*32,confirmation=protocol.confirmation(second['quote']))
        self.assertEqual(self.g.execute('accept',request)['state'],'accepting')
        self.assertEqual(len(self.f.s.record['jobs']),1)


if __name__=='__main__':unittest.main()
