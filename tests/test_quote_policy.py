"""Exercise the real pinned preparation/approval adapters with recorded RPCs."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import quote_workflow as q
import reverse_quote_workflow as r
import quote_policy as policy
from controller import private_load,save
import test_quote_workflow as forward
import test_reverse_quote_workflow as reverse


class AdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        forward.QuoteTests.setUpClass();reverse.ReverseQuoteTests.setUpClass()

    def fixture(self, direction):
        cls=forward.QuoteTests if direction=='forward' else reverse.ReverseQuoteTests
        f=cls();f.setUp();self.addCleanup(f.doCleanups)
        return f

    def root(self,f):return f.root() if callable(f.root) else f.root
    def writes(self,f):return f.mutations() if hasattr(f,'mutations') else f.writes()

    def rewrite(self,f,change):
        root=self.root(f);data=private_load(root/'proposal/quote.json');change(data)
        save(root/'proposal/quote.json',data)
        record=private_load(root/'review.json');record['quote']=copy.deepcopy(data);save(root/'review.json',record)
        return q.review(root)[2]

    def test_new_review_binds_direction_and_policy_digest(self):
        for direction in ('forward','reverse'):
            f=self.fixture(direction);report=f.prepare();root=self.root(f)
            self.assertEqual(report['quote_policy'],policy.commitment(direction))
            record=private_load(root/'review.json')
            record['quote_policy']['digest']='0'*64;save(root/'review.json',record)
            # Even approval of the changed review cannot accept a foreign policy.
            with self.assertRaisesRegex(ValueError,'quote_policy_changed'):f.approve(q.review(root)[2])
            self.assertEqual(self.writes(f),[])

    def test_coherently_changed_amount_fee_route_and_cltv_refused(self):
        changes=[lambda d:d['terms'].__setitem__('btc_amount_msat',True),
                 lambda d:d['controller']['route'][0].__setitem__('amount_msat',d['controller']['route'][0]['amount_msat']+1),
                 lambda d:d['controller']['route'][0].__setitem__('delay',41),
                 lambda d:d['controller']['route'].append(copy.deepcopy(d['controller']['route'][0])),
                 lambda d:d['terms'].__setitem__('min_cltv_delta',99),
                 lambda d:d['terms'].__setitem__('max_cltv_delta',2001),
                 lambda d:d['controller'].__setitem__('profile','live-pilot-v1')]
        for direction in ('forward','reverse'):
            for change in changes:
                with self.subTest(direction=direction,change=changes.index(change)):
                    f=self.fixture(direction);f.prepare();token=self.rewrite(f,change)
                    with self.assertRaises(ValueError):f.approve(token)
                    self.assertEqual(self.writes(f),[])
                    self.assertFalse((self.root(f)/'approval.json').exists())

    def test_policy_refusal_during_preparation_saves_no_review(self):
        for direction,module in (('forward',q),('reverse',r)):
            f=self.fixture(direction);original=module.preflight
            def changed(data,rpc):
                data['controller']['route'][0]['amount_msat']+=1
                return original(data,rpc)
            with patch.object(module,'preflight',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'quote_policy_fee'):f.prepare()
            self.assertEqual(self.writes(f),[])
            self.assertFalse((self.root(f)/'review.json').exists())

    def test_expired_invoice_and_quote_cannot_register(self):
        for direction in ('forward','reverse'):
            for which in ('quote','invoice'):
                f=self.fixture(direction);result=f.prepare()
                if which=='quote':
                    token=self.rewrite(f,lambda d:d['terms'].__setitem__('expires_at',f.now))
                else:
                    token=result['review_digest'];f.decoded['expiry']=1
                with self.assertRaises(ValueError):f.approve(token)
                self.assertEqual(self.writes(f),[])

    def test_slow_or_backwards_preflight_clock_refuses_publication(self):
        for direction in ('forward','reverse'):
            for jump in (-1,121):
                f=self.fixture(direction);result=f.prepare();old_rpc=f.rpc;clock=[f.now]
                def rpc(network,method,params):
                    result=old_rpc(network,method,params)
                    if method=='listsendpays':clock[0]+=jump
                    return result
                f.rpc=rpc
                with patch.object(q.time,'time',side_effect=lambda:clock[0]):
                    with self.assertRaisesRegex(ValueError,'quote_policy_(expiry_or_observation|invoice_time)'):
                        f.approve(result['review_digest'])
                self.assertEqual(self.writes(f),[])

    def test_held_margin_rechecked_before_executor_import(self):
        for direction in ('forward','reverse'):
            f=self.fixture(direction);result=f.prepare();f.approve(result['review_digest'])
            if direction=='forward':f.phase='held'
            else:f.held()
            old_rpc=f.rpc
            def rpc(network,method,params):
                result=old_rpc(network,method,params)
                if method=='getinfo':result['blockheight']=131  # held expiry 230: only 99 blocks
                return result
            f.rpc=rpc;before=self.writes(f)
            with self.assertRaisesRegex(ValueError,'quote_policy_held_margin'):
                q.advance(self.root(f),f.core,f.factory)
            self.assertEqual(self.writes(f),before)
            self.assertFalse((self.root(f)/'intent.json').exists())

    def test_legacy_unpublished_review_requires_new_preparation(self):
        for direction in ('forward','reverse'):
            f=self.fixture(direction);f.prepare();root=self.root(f)
            record=private_load(root/'review.json');record.pop('quote_policy');save(root/'review.json',record)
            with self.assertRaisesRegex(ValueError,'quote_policy_changed'):f.approve(q.review(root)[2])
            self.assertEqual(self.writes(f),[])

    def test_already_imported_attempt_recovery_does_not_reapply_admission(self):
        for direction in ('forward','reverse'):
            f=self.fixture(direction);result=f.prepare();f.approve(result['review_digest'])
            if direction=='forward':f.phase='held'
            else:f.held()
            with patch.object(q.executor,'step',return_value={'phase':'outgoing_started'}):
                q.advance(self.root(f),f.core,f.factory)
            state=private_load(self.root(f)/'state.json');state['phase']='outgoing_started'
            save(self.root(f)/'state.json',state)
            before=list(f.calls)
            with patch.object(q.time,'time',return_value=f.now+5000), patch.object(q.executor,'step',return_value={'phase':'reconciled'}) as step:
                self.assertEqual(q.advance(self.root(f),f.core,f.factory),{'phase':'reconciled'})
                step.assert_called_once()
            self.assertEqual(f.calls,before)

    def test_legacy_published_quote_can_continue_existing_approval(self):
        for direction in ('forward','reverse'):
            f=self.fixture(direction);result=f.prepare();f.approve(result['review_digest']);root=self.root(f)
            record=private_load(root/'review.json');record.pop('quote_policy');save(root/'review.json',record)
            _,data,old_token=q.review(root)
            save(root/'approval.json',dict(digest=old_token,expires_at=data['terms']['expires_at']))
            before=self.writes(f)
            self.assertIn('invoice',str(f.approve(old_token)))
            self.assertEqual(self.writes(f),before)
            if direction=='forward':f.phase='held'
            else:f.held()
            with patch.object(q.executor,'step',return_value={'phase':'outgoing_started'}) as step:
                q.advance(root,f.core,f.factory);step.assert_called_once()


if __name__=='__main__':unittest.main()
