"""Reproduce the live three-hop route: 200 blocks and 2,002 msat fees."""
import copy
import json
import unittest
import test_reverse_grant_timing as previous
from test_reverse_routed_swaps import pilot, swaps, swap_setup, reverse_session, load, RECIPIENT
import test_reverse_routed_swaps as cases
from reverse_contract import ROUTED_144, ROUTED_288, validate, timing
from reverse_node import RPCError
from reverse_plan import plan

ENTRY = '02'+'8'*64


class PublicPrefixRPC(cases.RoutedRPC):
    public_forwarders = 1
    def __call__(self, method, **params):
        if method == 'getroutes':
            self.calls.append((method, copy.deepcopy(params)))
            assert params['maxparts'] == 1 and params['maxfee_msat'] <= 10000
            assert params['layers'] == ['auto.localchans', 'auto.sourcefree']
            if params['destination'] == RECIPIENT:
                raise RPCError(205)
            assert params['destination'] == ENTRY
            assert params['amount_msat'] == 1501001 and params['final_cltv'] == 120
            assert params['maxfee_msat'] == 8999
            if params['maxdelay'] < 120 + 80*self.public_forwarders:
                raise RPCError(206)
            nodes = [self.id, self.ch['peer_id']]
            nodes += ['02'+str(i+3)*64 for i in range(self.public_forwarders-1)]
            nodes += [ENTRY]
            amounts = [1501001+1001*(self.public_forwarders-i) for i in range(self.public_forwarders+1)]
            delays = [120+80*(self.public_forwarders-i) for i in range(self.public_forwarders+1)]
            path = []
            for i,(a,b) in enumerate(zip(nodes,nodes[1:])):
                scid = self.ch['short_channel_id'] if i == 0 else str(i+3)+'x4x0'
                path.append(dict(short_channel_id_dir=scid+'/'+str(int(a>b)), node_id_in=a, node_id_out=b,
                    amount_in_msat=amounts[max(i-1,0)], amount_out_msat=amounts[i],
                    cltv_in=delays[max(i-1,0)], cltv_out=delays[i]))
            return dict(routes=[dict(amount_msat=1501001, final_cltv=120, path=path)])
        result = super().__call__(method, **params)
        if method == 'decode' and result.get('currency') in ('bc','bcrt'):
            result['routes'] = [[dict(pubkey=ENTRY, short_channel_id='3x3x0',
                fee_base_msat=1000, fee_proportional_millionths=1, cltv_expiry_delta=80)]]
        return result


class IncomingRPC(cases.RoutedRPC):
    def __call__(self, method, **params):
        result = super().__call__(method, **params)
        if method in ('reverse-status','btc-spend-info'): result['cltv_expiry'] = 1400
        return result


class PublicPrefixTests(unittest.TestCase):
    use_rpc = previous.TimingTests.use_rpc
    approve = previous.TimingTests.approve
    complete = previous.TimingTests.complete
    def setUp(self):
        previous.TimingTests.setUp(self)
        self.use_rpc(PublicPrefixRPC('btc'))
        self.rpc['xbt'] = IncomingRPC('xbt')
        self.nodes['xbt'].rpc = self.nodes['xbt'].node.rpc = self.rpc['xbt']

    def setup(self, maximum=288, count=3, replace=False):
        return previous.TimingTests.setup(self, maximum, count, replace)

    def draft(self):
        # Reuse invoice creation, but assert the public-prefix result separately.
        import hashlib, time
        self.counter += 1; preimage = '%064x' % self.counter
        h = hashlib.sha256(bytes.fromhex(preimage)).hexdigest()
        invoice = 'lnbc-public-prefix-'+str(self.counter)
        self.rpc['btc'].invoices[invoice] = dict(valid=True, type='bolt11 invoice', currency='bc',
            amount_msat=1500000, payment_hash=h, payment_secret='b'*64, payee=RECIPIENT,
            created_at=int(time.time()), expiry=3600, min_final_cltv_expiry=40)
        self.rpc['btc'].preimages[h] = preimage
        prepared = swaps.prepare(self.root, dict(invoice=invoice), factory=self.remote, inspector=self.inspector)
        self.assertEqual(prepared['routing_fee_msat'], 1001*(1+self.rpc['btc'].public_forwarders))
        self.assertEqual(prepared['route_delay_blocks'], 120+80*self.rpc['btc'].public_forwarders)
        self.assertEqual(prepared['max_delay_blocks'], 288)
        return prepared['pilot_id'], h

    def hold(self, h):
        previous.TimingTests.hold(self, h)
        self.rpc['xbt'].extra['htlcs'][0]['expiry'] = 1400

    def test_80_and_144_grants_keep_bytes_and_report_their_actual_limit(self):
        for maximum in (80,144):
            self.setup(maximum, replace=True)
            for role,node in self.nodes.items():
                path = node.sessions()/(json.loads(self.tokens[role])['session_id']+'.json')
                before = path.read_bytes()
                result = node.enable('',3,True,max_delay=288)
                self.assertEqual(result['max_delay_blocks'],maximum)
                self.assertEqual(result['credential'],self.tokens[role])
                self.assertEqual(path.read_bytes(),before)
            with self.assertRaisesRegex(ValueError,'^bounded_route_unavailable_'+str(maximum)+'$'):
                self.draft()
            self.assertFalse(swaps.paths(self.root))
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))

    def test_explicit_renewal_finds_200_block_route_and_preserves_full_margin(self):
        self.setup(144)
        with self.assertRaisesRegex(ValueError,'confirmation_required'):
            self.nodes['btc'].enable('',3,False,new_grant=True,max_delay=288)
        self.assertEqual(self.setup(288,replace=True)['max_delay_blocks'],288)
        s,h = self.draft(); c=pilot.record(self.root,s)[0]['contract']
        self.assertEqual(c['profile'],ROUTED_288)
        self.assertEqual([hop['delay'] for hop in c['route']],[200,120,40])
        self.assertEqual([hop['amount_msat'] for hop in c['route']],[1502002,1501001,1500000])
        self.assertEqual(timing(c),dict(minimum=350,invoice=374,maximum=2016,recovery=144))
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))

    def test_both_old_grant_profiles_refuse_v3_even_for_a_short_route(self):
        old=[]
        for maximum in (80,144):
            self.setup(maximum,replace=True)
            old.extend((role,node.read(json.loads(self.tokens[role])['session_id'])) for role,node in self.nodes.items())
        self.setup(288,replace=True);s,h=self.draft();c=pilot.record(self.root,s)[0]['contract']
        short=copy.deepcopy(c)
        for hop,delay in zip(short['route'],(80,60,40)):hop['delay']=delay
        validate(short)
        for role,state in old:
            for contract in (c,short):
                with self.assertRaisesRegex(ValueError,'contract_exceeds_grant_timing'):
                    self.nodes[role].enroll(state,contract)
                self.assertEqual(self.nodes[role].read(state['session_id'])['enrolled'],{})
                self.assertEqual(list(self.nodes[role].records().glob('*.json')),[])

    def test_144_and_288_grants_cannot_be_paired(self):
        self.setup(144);before=(self.root/swaps.FILE).read_bytes()
        token=self.nodes['btc'].enable('',3,True,new_grant=True,max_delay=288)['credential']
        with self.assertRaisesRegex(ValueError,'grant_timing_limits_differ'):
            swaps.configure(self.root,dict(xbtCredential=self.tokens['xbt'],btcCredential=token,confirmed=True),factory=self.remote)
        self.assertEqual((self.root/swaps.FILE).read_bytes(),before)

    def test_200_block_lost_reply_recovers_exact_attempt_after_expiry(self):
        previous.TimingTests.test_120_block_attempt_recovers_after_lost_reply_and_expired_grants(self)

    def test_144_pending_contract_recovers_and_blocks_288_renewal(self):
        self.use_rpc(previous.HintRPC('btc'));self.setup(144)
        s,h=previous.TimingTests.draft(self);self.approve(s)
        before=copy.deepcopy(pilot.record(self.root,s)[0]['contract'])
        self.assertEqual(before['profile'],ROUTED_144)
        for node in self.nodes.values():
            with self.assertRaises(ValueError):node.enable('',3,True,new_grant=True,max_delay=288)
        self.nodes={k:reverse_session.Session(n.root,k,self.rpc[k]) for k,n in self.nodes.items()}
        self.complete(s,h)
        self.assertEqual(pilot.record(self.root,s)[0]['contract'],before)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'settled')

    def test_200_block_route_needs_350_before_send_and_closes_at_144(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        self.rpc['xbt'].height=1051
        with self.assertRaisesRegex(ValueError,'incoming_timing_refused'):swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))
        self.rpc['xbt'].height=1050;swaps.tick(self.root,factory=self.remote)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['btc'].calls),1)
        self.rpc['xbt'].height=1255;swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='close' for m,p in self.rpc['xbt'].calls))
        self.rpc['xbt'].height=1256;swaps.tick(self.root,factory=self.remote)
        closes=[p for m,p in self.rpc['xbt'].calls if m=='close']
        self.assertEqual(len(closes),1)
        self.assertEqual(closes[0]['id'],self.rpc['xbt'].extra['channel_id'])

    def test_v3_keeps_delay_fee_hop_and_recipient_limits(self):
        self.setup();s,h=self.draft();c=pilot.record(self.root,s)[0]['contract']
        for field,value in (('delay',289),('amount_msat',1510001)):
            bad=copy.deepcopy(c);bad['route'][0][field]=value
            with self.assertRaises(ValueError):validate(bad)
        bad=copy.deepcopy(c);bad['route']*=2
        with self.assertRaises(ValueError):validate(bad)
        self.rpc['btc'].invoices[c['invoice']]['min_final_cltv_expiry']=80
        with self.assertRaisesRegex(ValueError,'invalid_recipient_invoice'):
            plan(self.rpc['btc'],c['invoice'],self.rpc['btc'].id,max_delay=288)

    def test_four_hops_with_three_80_block_deltas_fit_288(self):
        self.rpc['btc'].public_forwarders=2;self.setup();s,h=self.draft()
        c=pilot.record(self.root,s)[0]['contract']
        self.assertEqual(len(c['route']),4);self.assertEqual(c['route'][0]['delay'],280)
        self.assertEqual(timing(c)['invoice'],454)
        old=copy.deepcopy(c);old['profile']=ROUTED_144
        with self.assertRaises(ValueError):validate(old)

    def test_over_budget_route_reports_288_without_creating_a_draft(self):
        self.rpc['btc'].public_forwarders=3;self.setup()
        with self.assertRaisesRegex(ValueError,'^bounded_route_unavailable_288$'):self.draft()
        self.assertFalse(swaps.paths(self.root))
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))


if __name__=='__main__':unittest.main()
