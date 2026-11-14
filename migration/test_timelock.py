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
    def test_invoice_case_boundary_and_planner(self):
        for profile in (ba.ci.BTC,ba.ci.PRIVKEYIO_XBT):
            node=Fake(profile)
            lower='lnbcrt1synthetic-decoded-by-fake'
            upper=lower.upper()
            result=rp.plan(node,node.chain,upper,[pin(profile)],BOUNDS,clock=lambda:NOW)
            self.assertEqual(result['invoice'],upper)
            rp.validate_plan(result)
            with self.assertRaisesRegex(ValueError,'^recipient_invoice_invalid$'):
                rp.plan(node,node.chain,'LN'+lower[2:],[pin(profile)],BOUNDS,clock=lambda:NOW)
            mainnet=ba.ci.binding(profile,'mainnet',node.chain['node_id'])
            invoice='lnbc10u1'+'q'*100
            self.assertTrue(ba.ci.invoice_text(invoice,mainnet))
            self.assertTrue(ba.ci.invoice_text(invoice.upper(),mainnet))
            self.assertFalse(ba.ci.invoice_text('LN'+invoice[2:],mainnet))

    def test_uppercase_settlement_keeps_attempt_binding(self):
        from test_route_plan import PREIMAGE
        for profile in (ba.ci.BTC,ba.ci.PRIVKEYIO_XBT):
            node=Fake(profile)
            plan=rp.plan(node,node.chain,'LNBCRT1SYNTHETIC-DECODED-BY-FAKE',[pin(profile)],BOUNDS,clock=lambda:NOW)
            original=copy.deepcopy(plan);digest=rp.digest(plan)
            row=node.attempt(plan);row.update(bolt11=plan['invoice'].lower(),status='complete',payment_preimage=PREIMAGE)
            node.payments=[row]
            observed=rp.observe_attempt(node,plan)
            self.assertEqual(observed['state'],'complete')
            self.assertEqual(observed['preimage'],PREIMAGE)
            self.assertEqual(rp.observe_attempt(node,plan,observed['identity']),observed)
            self.assertEqual(plan,original);self.assertEqual(rp.digest(plan),digest)
            for change in ({'bolt11':'LN'+row['bolt11'][2:]},{'bolt11':row['bolt11']+'q'},
                           {'bolt11':None},{'payment_hash':'11'*32},{'destination':'02'+'11'*32},
                           {'amount_msat':row['amount_msat']+1},{'amount_sent_msat':row['amount_sent_msat']+1},
                           {'groupid':2},{'partid':1},{'created_index':8}):
                node.payments=[dict(row,**change)]
                with self.assertRaisesRegex(ValueError,'^attempt_changed$'):
                    rp.observe_attempt(node,plan,observed['identity'])

    def test_ten_million_xbt_invoice_and_existing_btc_schema_limit(self):
        for profile,cap in ((ba.ci.PRIVKEYIO_XBT,10000000000),(ba.ci.BTC,1000000000)):
            node=Fake(profile);delta=cap-node.decoded['amount_msat']
            node.decoded['amount_msat']=cap
            for hop in node.route:hop['amount_msat']+=delta
            node.rows[0]['spendable_msat']=cap+100000
            plan=node.plan();self.assertEqual(plan['amount_msat'],cap)
            rp.validate_plan(plan)
            node.decoded['amount_msat']=cap+1
            with self.assertRaisesRegex(ValueError,'^recipient_invoice_limits$'):node.plan()
        from test_cross_contract import example
        contract,_=example('reverse');terms=copy.deepcopy(contract['incoming'])
        terms['amount_msat']=10000000000
        hg.terms(terms,terms['chain'])
        with self.assertRaisesRegex(ValueError,'^quote_limits_invalid$'):
            hg.terms(dict(terms,amount_msat=10000000001),terms['chain'])
        limit=dict(max_swaps=4,max_amount_msat=10000000000,total_amount_msat=30000000000,**BOUNDS)
        ba.limits(limit,'outgoing')
        with self.assertRaises(ValueError):ba.limits(dict(limit,total_amount_msat=100000100001),'outgoing')

    def test_recipient_expiry_margin_reports_specific_reason(self):
        from test_cross_contract import example
        import service_cli
        for direction in ('forward','reverse'):
            c,_=example(direction);plan=copy.deepcopy(c['outgoing']);price=copy.deepcopy(c['pricing'])
            price.update(schema=2,expires_at=price['fetched_at_ms']//1000+600)
            plan['expires_at']=price['expires_at']+179
            with self.assertRaisesRegex(ValueError,'^recipient_invoice_expiring$'):
                cross_contract.create(c['grants'],plan,price,now=NOW)
            plan['expires_at']+=1
            cross_contract.create(c['grants'],plan,price,now=NOW)
        self.assertIn('recipient_invoice_expiring',service_cli.SAFE)

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

    def test_route_hint_rejections_report_observed_cause_without_widening_limits(self):
        cases=[('route_hint_entry_unknown',rp.RPCError(205,unknown_destination=True)),
               ('route_hint_path_unavailable',rp.RPCError(206))]
        for reason,error in cases:
            node=Fake();node.hinted();node.answers=[rp.RPCError(205,unknown_destination=True),error]
            with self.assertRaises(rp.RouteAdmissionError) as caught:node.plan()
            self.assertEqual(str(caught.exception),reason)
            self.assertEqual(caught.exception.details[reason],1)
            self.assertEqual(caught.exception.details['route_hint_count'],1)
            self.assertEqual(caught.exception.details['max_fee_msat'],10000)
        for field,value,reason in [('fee_base_msat',10001,'route_hint_fee_limit'),
                                   ('cltv_expiry_delta',300,'route_hint_delay_limit')]:
            node=Fake();node.hinted();node.decoded['routes'][0][0][field]=value
            with self.assertRaisesRegex(rp.RouteAdmissionError,reason):node.plan()
            self.assertEqual(sum(m=='getroutes' for m,p in node.calls),1)
        node=Fake();node.hinted()
        with self.assertRaisesRegex(rp.RouteAdmissionError,'route_hint_hop_limit'):
            node.plan(bounds=dict(BOUNDS,max_hops=1))
        node=Fake();node.hinted();node.decoded['routes']*=2
        node.answers=[rp.RPCError(205,unknown_destination=True),rp.RPCError(205,unknown_destination=True),node.answers[1]]
        self.assertEqual(node.plan()['route'],node.route)

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
