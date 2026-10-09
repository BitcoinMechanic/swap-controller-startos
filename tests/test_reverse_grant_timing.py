"""Regression for the live 80-block hint plus 40-block final hop.

Exercise immutable legacy authority, explicitly renewed grants, controller
recovery, and the plugin/HTTPS error boundary without exposing private data.
"""
import copy
import hashlib
import io
import json
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import test_reverse_routed_swaps as cases
from test_reverse_routed_swaps import pilot, swaps, swap_setup, reverse_session, load, RECIPIENT
from reverse_contract import ROUTED, ROUTED_144, canonical, validate, timing
from reverse_node import RPCError
from reverse_plan import plan
import pilot_node


class HintRPC(cases.RoutedRPC):
    def __call__(self, method, **params):
        if method == 'getroutes':
            self.calls.append((method, copy.deepcopy(params)))
            assert params['maxparts'] == 1 and params['maxfee_msat'] <= 10000
            if params['destination'] == RECIPIENT:
                raise RPCError(205)
            assert params['destination'] == self.ch['peer_id']
            assert params['amount_msat'] == 1501001 and params['final_cltv'] == 120
            assert params['maxdelay'] == 144 and params['maxfee_msat'] == 8999
            hop = dict(short_channel_id_dir=self.ch['short_channel_id']+'/'+str(int(self.id > self.ch['peer_id'])),
                       node_id_in=self.id, node_id_out=self.ch['peer_id'], amount_in_msat=1501001,
                       amount_out_msat=1501001, cltv_in=120, cltv_out=120)
            return dict(routes=[dict(amount_msat=1501001, final_cltv=120, path=[hop])])
        result = super().__call__(method, **params)
        if method == 'decode' and result.get('currency') in ('bc', 'bcrt'):
            result['routes'] = [[dict(pubkey=self.ch['peer_id'], short_channel_id='3x3x0',
                fee_base_msat=1000, fee_proportional_millionths=1, cltv_expiry_delta=80)]]
        return result


class TimingTests(unittest.TestCase):
    def setUp(self):
        cases.RoutedTests.setUp(self)
        self.original_btc = self.rpc['btc']
        self.use_rpc(HintRPC('btc'))

    def use_rpc(self, rpc):
        self.rpc['btc'] = rpc
        self.nodes['btc'].rpc = self.nodes['btc'].node.rpc = rpc

    def setup(self, maximum=144, count=3, replace=False):
        for role, node in self.nodes.items():
            self.tokens[role] = node.enable('', count, True, new_grant=replace,
                routed_grant=True, max_delay=maximum)['credential']
        swap_setup.configure(self.root, dict(xbtRune='read-xbt', btcRune='read-btc', confirmed=True), factory=self.inspector)
        return swaps.configure(self.root, dict(xbtCredential=self.tokens['xbt'],
            btcCredential=self.tokens['btc'], confirmed=True), factory=self.remote)

    approve = cases.RoutedTests.approve
    hold = cases.RoutedTests.hold
    complete = cases.RoutedTests.complete

    def draft(self):
        self.counter += 1
        preimage = '%064x' % self.counter
        h = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice = 'lnbc-timing-' + str(self.counter)
        self.rpc['btc'].invoices[invoice] = dict(valid=True, type='bolt11 invoice', currency='bc',
            amount_msat=1500000, payment_hash=h, payment_secret='b'*64, payee=RECIPIENT,
            created_at=int(time.time()), expiry=3600, min_final_cltv_expiry=40)
        self.rpc['btc'].preimages[h] = preimage
        prepared = swaps.prepare(self.root, dict(invoice=invoice), factory=self.remote, inspector=self.inspector)
        self.assertEqual(prepared['routing_fee_msat'], 1001)
        self.assertEqual(prepared['route_delay_blocks'], 120)
        return prepared['pilot_id'], h

    def test_old_grant_stays_at_80_when_retrieved_and_refuses_live_hint(self):
        self.setup(80)
        before = {role: (n.sessions()/ (json.loads(self.tokens[role])['session_id']+'.json')).read_bytes()
                  for role, n in self.nodes.items()}
        for role, node in self.nodes.items():
            result = node.enable('', 3, True, max_delay=144)
            self.assertEqual(result['max_delay_blocks'], 80)
            self.assertEqual(result['credential'], self.tokens[role])
            self.assertEqual((node.sessions()/(json.loads(self.tokens[role])['session_id']+'.json')).read_bytes(), before[role])
        with self.assertRaisesRegex(ValueError, 'bounded_route_unavailable'):
            self.draft()
        self.assertFalse(swaps.paths(self.root))
        calls = [p for m,p in self.rpc['btc'].calls if m == 'getroutes']
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['maxdelay'], 80)
        self.assertFalse(any(m == 'sendpay' for m,p in self.rpc['btc'].calls))

    def test_explicit_renewal_plans_120_and_old_tokens_cannot_plan(self):
        self.setup(80)
        old = dict(self.tokens)
        with self.assertRaisesRegex(ValueError, 'confirmation_required'):
            self.nodes['btc'].enable('', 3, False, new_grant=True, max_delay=144)
        self.assertEqual(self.setup(144, replace=True)['max_delay_blocks'], 144)
        s, h = self.draft()
        c = pilot.record(self.root, s)[0]['contract']
        self.assertEqual(c['profile'], ROUTED_144)
        self.assertEqual(c['route'][0]['delay'], 120)
        self.assertEqual(timing(c), dict(minimum=270, invoice=294, maximum=2016, recovery=144))
        with self.assertRaisesRegex(ValueError, 'route_planning_refused'):
            self.nodes['btc'].call(json.loads(old['btc'])['session_id'], 'plan', c['invoice'], '', '')

    def test_both_old_grants_refuse_v2_before_enrollment_even_for_short_route(self):
        self.setup(80)
        old_states = {k: n.read(json.loads(self.tokens[k])['session_id']) for k,n in self.nodes.items()}
        self.setup(144, replace=True)
        s, h = self.draft()
        c = pilot.record(self.root, s)[0]['contract']
        for delay in (120, 46):
            changed = copy.deepcopy(c); changed['route'][0]['delay'] = delay
            validate(changed)
            for role, node in self.nodes.items():
                with self.assertRaisesRegex(ValueError, 'contract_exceeds_grant_timing'):
                    node.enroll(old_states[role], changed)
                self.assertEqual(list(node.records().glob('*.json')), [])
                self.assertEqual(node.read(old_states[role]['session_id'])['enrolled'], {})
        legacy = copy.deepcopy(c); legacy['profile'] = ROUTED
        with self.assertRaisesRegex(ValueError, 'route_fee_or_delay_limit'): validate(legacy)

    def test_different_grant_limits_refuse_pairing(self):
        self.setup(80)
        pairing = (self.root/swaps.FILE).read_bytes()
        token = self.nodes['btc'].enable('',3,True,new_grant=True,max_delay=144)['credential']
        with self.assertRaisesRegex(ValueError, 'grant_timing_limits_differ'):
            swaps.configure(self.root,dict(xbtCredential=self.tokens['xbt'],btcCredential=token,confirmed=True),factory=self.remote)
        self.assertEqual((self.root/swaps.FILE).read_bytes(), pairing)

    def test_120_block_attempt_recovers_after_lost_reply_and_expired_grants(self):
        self.setup()
        s,h = self.draft(); self.approve(s); self.hold(h)
        self.rpc['btc'].drop = 'sendpay'
        with self.assertRaises(ValueError): swaps.tick(self.root, factory=self.remote)
        original = copy.deepcopy(pilot.record(self.root,s)[0])
        plans = sum(m=='getroutes' for m,p in self.rpc['btc'].calls)
        self.rpc['btc'].drop = None
        for role,node in self.nodes.items():
            state=node.read(json.loads(self.tokens[role])['session_id'])
            state.update(paused=True, expires_at=0); node.write(state)
        self.nodes={k:reverse_session.Session(n.root,k,self.rpc[k]) for k,n in self.nodes.items()}
        self.rpc['btc'].payments[h][0].update(status='complete',payment_preimage=self.rpc['btc'].preimages[h])
        swaps.tick(self.root,factory=self.remote)
        swaps.tick(self.root,factory=self.remote)
        r=pilot.record(self.root,s)[0]
        self.assertEqual(r['phase'],'settled')
        for key in ('contract','binding','expiry','incoming_pin'): self.assertEqual(r[key], original[key])
        self.assertEqual(sum(m=='getroutes' for m,p in self.rpc['btc'].calls),plans)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['btc'].calls),1)
        self.assertEqual(sum(m=='reverse-release' for m,p in self.rpc['xbt'].calls),1)

    def test_legacy_pending_swap_recovers_and_blocks_renewal(self):
        self.use_rpc(self.original_btc)
        self.setup(80)
        s,h=cases.RoutedTests.draft(self); self.approve(s)
        before=copy.deepcopy(pilot.record(self.root,s)[0]['contract'])
        self.assertEqual(before['profile'],ROUTED)
        for node in self.nodes.values():
            with self.assertRaises(ValueError): node.enable('',3,True,new_grant=True,max_delay=144)
        self.nodes={k:reverse_session.Session(n.root,k,self.rpc[k]) for k,n in self.nodes.items()}
        self.complete(s,h)
        self.assertEqual(pilot.record(self.root,s)[0]['contract'],before)

    def test_v2_still_rejects_excess_delay_fee_hops_and_final_cltv(self):
        self.setup(); s,h=self.draft(); c=pilot.record(self.root,s)[0]['contract']
        for field,value in (('delay',145),('amount_msat',1510001)):
            bad=copy.deepcopy(c);bad['route'][0][field]=value
            with self.assertRaises(ValueError): validate(bad)
        bad=copy.deepcopy(c);bad['route']*=3
        with self.assertRaises(ValueError): validate(bad)
        self.rpc['btc'].invoices[c['invoice']]['min_final_cltv_expiry']=80
        with self.assertRaisesRegex(ValueError,'invalid_recipient_invoice'):
            plan(self.rpc['btc'],c['invoice'],self.rpc['btc'].id,max_delay=144)

    def test_120_block_route_requires_full_incoming_recovery_margin(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['xbt'].height=1031  # 269 remaining: one short of 120+6+144.
        with self.assertRaisesRegex(ValueError,'incoming_timing_refused'): swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))
        self.rpc['xbt'].height=1030
        swaps.tick(self.root,factory=self.remote)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['btc'].calls),1)
        self.rpc['xbt'].height=1155
        swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='close' for m,p in self.rpc['xbt'].calls))
        self.rpc['xbt'].height=1156
        swaps.tick(self.root,factory=self.remote)
        closes=[p for m,p in self.rpc['xbt'].calls if m=='close']
        self.assertEqual(len(closes),1)
        self.assertEqual(closes[0]['id'],self.rpc['xbt'].extra['channel_id'])


class ErrorBoundaryTests(unittest.TestCase):
    def test_plugin_exposes_only_reviewed_plan_errors(self):
        import tempfile
        from pathlib import Path
        params=dict(session_id='a'*64,operation='plan',contract='PRIVATE_INVOICE',pilot_id='',preimage='')
        with tempfile.TemporaryDirectory() as tmp:
            for operation,reason,exposed in [('plan','bounded_route_unavailable',True),
                    ('plan','PRIVATE_URL_AND_RUNE',False),('send','bounded_route_unavailable',False)]:
                msg=dict(jsonrpc='2.0',id=1,method='swap-reverse-call',params=dict(params,operation=operation))
                out=io.StringIO()
                with patch('sys.stdin',io.StringIO(json.dumps(msg)+'\n')),patch('sys.stdout',out), \
                        patch.object(reverse_session.Session,'call',side_effect=ValueError(reason)):
                    pilot_node.plugin(Path(tmp),'btc')
                reply=json.loads(out.getvalue())
                self.assertEqual('result' in reply,exposed)
                if exposed: self.assertEqual(reply['result'],{'route_error':reason})
                self.assertNotIn('PRIVATE',out.getvalue())

    def test_https_client_preserves_only_fixed_plan_reason(self):
        for operation,value,expected in [('plan',{'route_error':'bounded_route_unavailable'},'bounded_route_unavailable'),
                ('plan',{'route_error':'PRIVATE_URL_AND_RUNE'},'session_rpc_refused_or_uncertain'),
                ('plan',{'route_error':'bounded_route_unavailable','private':'PRIVATE'},'session_rpc_refused_or_uncertain'),
                ('send',{'route_error':'bounded_route_unavailable'},'session_rpc_refused_or_uncertain')]:
            remote=object.__new__(swaps.SessionRemote);remote.session_id='a'*64
            remote.client=SimpleNamespace(url='https://coordinator.invalid',rune='PRIVATE',
                opener=SimpleNamespace(open=lambda *a,**k:io.BytesIO(json.dumps(value).encode())))
            with self.assertRaisesRegex(ValueError,'^'+expected+'$'):
                remote.request(operation)


if __name__=='__main__': unittest.main()
