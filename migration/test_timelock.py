"""Recipient timelock compatibility and unchanged authority/recovery boundaries."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import route_plan as rp
import bounded_authority as ba
import held_gate as hg
import cross_contract, cross_worker, customer_https, customer_remote, worker_pairing
from test_route_plan import Fake, NOW, pin, BOUNDS
from test_bounded_authority import Node, bounds


def set_delay(node, final):
    node.decoded['min_final_cltv_expiry']=final
    for hop in node.route: hop['delay']+=final-40


class TimelockTests(unittest.TestCase):
    def test_80_block_invoice_both_chains_and_stable_legacy_digest(self):
        for profile in (ba.ci.BTC,ba.ci.PRIVKEYIO_XBT):
            node=Fake(profile); old=node.plan(); digest=rp.digest(old)
            set_delay(node,80); result=node.plan()
            self.assertEqual(result['route'][-1]['delay'],80)
            self.assertEqual(result['route'][0]['delay'],240)
            self.assertEqual(rp.digest(old),digest)
            self.assertEqual([p for m,p in node.calls if m=='getroutes'][-1]['final_cltv'],80)

    def test_304_and_576_block_routes_preserve_incoming_margin_and_old_bounds(self):
        import cross_contract as cc
        from test_cross_contract import example
        for profile in (ba.ci.BTC,ba.ci.PRIVKEYIO_XBT):
            node=Fake(profile);set_delay(node,80);node.route[0]['delay']=304
            with self.assertRaises(ValueError):node.plan() # old 288-block grants stay bounded
            result=node.plan(bounds=dict(BOUNDS,max_delay_blocks=576))
            self.assertEqual(result['route'][0]['delay'],304)
            node.route[0]['delay']=577
            with self.assertRaises(ValueError):node.plan(bounds=dict(BOUNDS,max_delay_blocks=576))
        for direction in ('forward','reverse'):
            for delay in (304,576):
                c,_=example(direction);plan=copy.deepcopy(c['outgoing'])
                plan['limits']['max_delay_blocks']=576
                plan['route'][0]['delay']=delay
                plan['route'][-1]['delay']=80
                c['grants']['outgoing']['limits']['max_delay_blocks']=576
                updated=cc.create(c['grants'],plan,c['pricing'],now=NOW)
                self.assertEqual(updated['incoming']['min_cltv_delta'],delay+150)
                updated['incoming']['min_cltv_delta']=delay+149
                with self.assertRaises(ValueError):cc.validate(updated)
        with self.assertRaises(ValueError):rp.limits(dict(BOUNDS,max_delay_blocks=577))

    def test_unknown_destination_requires_hints_but_does_not_block_valid_hints(self):
        node=Fake();node.answers=[rp.RPCError(205,unknown_destination=True)]
        with self.assertRaisesRegex(ValueError,'recipient_route_unknown'):node.plan()
        node=Fake();node.hinted();node.answers[0]=rp.RPCError(205,unknown_destination=True)
        self.assertEqual(node.plan()['route'],node.route)

    def test_bounds_and_bad_timelocks_rejected_before_route(self):
        for final in (True,0,-1,289,None):
            node=Fake();node.decoded['min_final_cltv_expiry']=final
            with self.assertRaisesRegex(ValueError,'recipient_invoice_cltv_limit'):node.plan()
            self.assertFalse(any(m=='getroutes' for m,p in node.calls))
        node=Fake();set_delay(node,80)
        with self.assertRaises(ValueError):node.plan(bounds=dict(BOUNDS,max_delay_blocks=239))
        node=Fake();node.route[-1]['delay']=39
        with self.assertRaises(ValueError):node.plan()

    def test_enroll_send_and_recovery_80_blocks_with_invoice_recheck(self):
        for profile in (ba.ci.BTC,ba.ci.PRIVKEYIO_XBT):
            with tempfile.TemporaryDirectory() as directory:
                node=Node(profile);set_delay(node,80)
                path=Path(directory)/'journal';ba.initialize_fixture(path,node.chain)
                with __import__("contextlib").closing(hg.Journal(path)) as journal:
                    auth=ba.Authority(journal,node.chain,node,clock=lambda:NOW)
                    gid=auth.create_grant('outgoing',[pin(profile)],bounds(),NOW+3600,confirmed=True)
                    plan=node.plan();node.plan_value=plan
                    eid=auth.enroll(gid,plan,confirmed=True)
                    # A larger requirement or a changed saved final hop cannot slip through.
                    node.decoded['min_final_cltv_expiry']=81
                    with self.assertRaises(ValueError):auth.send(eid)
                    self.assertFalse(any(m=='sendpay' for m,p in node.calls))
                    node.decoded['min_final_cltv_expiry']=80
                    auth.send(eid);node.finish();auth.reconcile(eid)
                    self.assertTrue(auth.status(eid)['terminal'])
                    auth.send(eid)
                    self.assertEqual(sum(m=='sendpay' for m,p in node.calls),1)

    def test_cross_contract_increases_incoming_margin(self):
        import cross_contract as cc
        from test_cross_contract import example
        for direction in ('forward','reverse'):
            c,_=example(direction);plan=copy.deepcopy(c['outgoing'])
            for hop in plan['route']:hop['delay']+=40
            updated=cc.create(c['grants'],plan,c['pricing'],now=NOW)
            self.assertEqual(updated['incoming']['min_cltv_delta'],390)
            updated['incoming']['min_cltv_delta']=389
            with self.assertRaises(ValueError):cc.validate(updated)


class ErrorBoundaryTests(unittest.TestCase):
    def test_client_only_promotes_allowlisted_plan_codes(self):
        import customer_https as https
        from test_customer_https import Response, nodes
        from test_cross_worker import Remote
        remote=Remote()
        with patch.object(https,'context',return_value=object()), patch.object(https.http.client,'HTTPSConnection') as conn:
            client=https.Client(nodes(remote)['outgoing'])
            for code in ('recipient_route_unknown','recipient_invoice_cltv_limit','bounded_route_unavailable','grant_closed','channel_requires_recovery','previous_enrollment_requires_recovery'):
                conn.return_value.getresponse.return_value=Response({'admission_error':code})
                with self.assertRaisesRegex(https.HttpsError,'^'+code+'$'):client._request('plan',{})
            for value, code in (({'error':'PRIVATE secret'},'node_rpc_rejected'),(b'invalid PRIVATE secret','node_reply_json_invalid')):
                conn.return_value.getresponse.return_value=Response(value)
                with self.assertRaisesRegex(https.HttpsError,'^'+code+'$'):client._request('plan',{})
            conn.return_value.getresponse.return_value=Response({'admission_error':'PRIVATE'})
            with self.assertRaisesRegex(https.HttpsError,'^node_admission_code_unknown$'):client._request('plan',{})
            conn.return_value.getresponse.return_value=Response({'admission_error':'recipient_invoice_cltv_limit'})
            with self.assertRaisesRegex(https.HttpsError,'^node_admission_code_unknown$'):client._request('send',{})

if __name__=='__main__':unittest.main()
