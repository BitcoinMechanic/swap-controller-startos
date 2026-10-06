import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'assets'))
import live_policy as policy


class PolicyTests(unittest.TestCase):
    def candidate(self, reverse=False):
        return dict(policy_digest=policy.digest(), profile=policy.REVERSE if reverse else policy.FORWARD,
                    btc_amount_msat=1500000 if reverse else 1000000,
                    xbt_amount_msat=350000000 if reverse else 2000000,
                    route_hops=1, route_delay_blocks=40, routing_fee_msat=0,
                    recipient_min_final_cltv=18, observed_at=1000,
                    quote_expires_at=1120, recipient_expires_at=1180,
                    btc_height=900000, xbt_height=100,
                    incoming_expiry=290 if reverse else 900288)

    def check(self, candidate):
        return policy.validate(candidate, now=1000)

    def test_both_profiles_numeric_only(self):
        for reverse in (False, True):
            result = self.check(self.candidate(reverse))
            self.assertTrue(result['numeric_policy_matches'])
            self.assertFalse(result['execution_authorized'])
            self.assertFalse(result['live_payment_enabled'])

    def test_no_cross_chain_absolute_height_comparison(self):
        for reverse in (False, True):
            c = self.candidate(reverse)
            c['btc_height' if reverse else 'xbt_height'] = 499999999
            self.assertTrue(self.check(c)['numeric_policy_matches'])

    def test_cltv_minimum_and_maximum(self):
        for reverse, base, minimum in ((False, 900000, 288), (True, 100, 190)):
            for remaining, valid in ((minimum-1, False), (minimum, True), (2016, True), (2017, False)):
                c = self.candidate(reverse); c['incoming_expiry'] = base + remaining
                self.assertEqual(self.check(c)['numeric_policy_matches'], valid)

    def test_reverse_route_delay_changes_timing(self):
        c = self.candidate(True); c.update(route_hops=8, route_delay_blocks=576,
                                          routing_fee_msat=30000, incoming_expiry=826)
        result = self.check(c)
        self.assertTrue(result['numeric_policy_matches'])
        self.assertEqual(result['minimum_incoming_remaining_blocks'], 726)
        self.assertEqual(result['proposed_invoice_cltv_blocks'], 750)
        c['incoming_expiry'] -= 1
        self.assertIn('incoming_cltv_outside_policy', self.check(c)['reasons'])

    def test_reverse_amount_and_fee_bounds(self):
        for field, invalids in {'btc_amount_msat': [1499999,1500001],
                               'xbt_amount_msat': [0,999,1001,500001000],
                               'routing_fee_msat': [30001], 'route_hops': [0,9],
                               'route_delay_blocks': [0,577]}.items():
            for value in invalids:
                c = self.candidate(True); c[field] = value
                self.assertFalse(self.check(c)['numeric_policy_matches'], (field,value))
        for amount in (1000,500000000):
            c = self.candidate(True); c['xbt_amount_msat'] = amount
            self.assertTrue(self.check(c)['numeric_policy_matches'])

    def test_forward_fixed_amounts_and_direct_route(self):
        for field, value in [('btc_amount_msat',2000000),('xbt_amount_msat',4000000),
                             ('route_hops',2),('route_delay_blocks',41),('routing_fee_msat',1),
                             ('recipient_min_final_cltv',41)]:
            c=self.candidate();c[field]=value
            self.assertFalse(self.check(c)['numeric_policy_matches'])

    def test_recipient_cltv_cannot_exceed_route(self):
        for reverse in (False,True):
            for value in (0,41):
                c=self.candidate(reverse);c['recipient_min_final_cltv']=value
                self.assertFalse(self.check(c)['numeric_policy_matches'])

    def test_quote_expiry_and_recipient_headroom(self):
        for field, value in [('quote_expires_at',1000),('quote_expires_at',1121),
                             ('recipient_expires_at',1179)]:
            c=self.candidate();c[field]=value
            self.assertFalse(self.check(c)['numeric_policy_matches'])

    def test_observation_age_and_future(self):
        for observed, valid in ((879,False),(880,True),(1000,True),(1001,False)):
            c=self.candidate();c['observed_at']=observed
            self.assertEqual(self.check(c)['numeric_policy_matches'],valid)

    def test_strict_integer_types_all_fields(self):
        for key in self.candidate().keys()-{'profile','policy_digest'}:
            for value in (True,False,1.0,'1',None,-1,2**53):
                c=self.candidate();c[key]=value
                self.assertEqual(self.check(c)['reasons'],['invalid_integer'])
        for now in (True,1.5,'1000',-1):
            self.assertFalse(policy.validate(self.candidate(),now=now)['numeric_policy_matches'])

    def test_schema_profile_and_digest_refused(self):
        for c in (None,[],{},dict(self.candidate(),extra='PRIVATE')):
            self.assertEqual(self.check(c)['reasons'],['invalid_candidate_schema'])
        for field,value in [('profile','live-pilot-v2'),('policy_digest','old'),('profile',[])]:
            c=self.candidate();c[field]=value
            result=self.check(c)
            self.assertFalse(result['numeric_policy_matches'])
            self.assertNotIn('PRIVATE',json.dumps(result))

    def test_height_domain(self):
        for key in ('btc_height','xbt_height','incoming_expiry'):
            c=self.candidate();c[key]=500000000
            self.assertIn('invalid_block_height',self.check(c)['reasons'])

    def test_review_is_reproducible_and_cannot_mutate_policy(self):
        before=policy.review();changed=policy.review()
        changed['forward']['btc_amount_msat']=1
        self.assertEqual(before,policy.review())
        self.assertFalse(before['executor_enforcement'])
        self.assertFalse(before['relative_chain_progress_guaranteed'])

    def test_review_process_leaves_pairing_barrier_and_jobs_untouched(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);(root/'execution').mkdir()
            for path in ('pairing.json','execution/restored.json','execution/jobs.json'):
                (root/path).write_text('PRIVATE_SENTINEL')
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            script=Path(policy.__file__).resolve()
            process=subprocess.run([sys.executable,'-B',str(script),'review'],cwd=root,capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(process.stdout),policy.review())
            self.assertEqual(before,{str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()})
            self.assertNotIn('PRIVATE',process.stdout+process.stderr)

    def test_cli_has_no_activation_or_candidate_execution(self):
        for args in ([],['activate'],['review','PRIVATE']):
            out=io.StringIO()
            with contextlib.redirect_stdout(out):self.assertEqual(policy.main(args),1)
            self.assertNotIn('PRIVATE',out.getvalue())


if __name__=='__main__':unittest.main()
