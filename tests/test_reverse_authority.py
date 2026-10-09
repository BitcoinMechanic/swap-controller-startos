"""Reverse-specific fences, actual BTC HTLC capture and durable gate replay."""
import copy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import test_reverse_routed_swaps as routed_cases
from test_reverse_routed_swaps import base,pilot,swaps,load
import reverse_session as swap_session
from pilot_node import save
from reverse_contract import canonical,digest,REPEAT,timing

class ReverseAuthorityTests(routed_cases.RoutedTests):
    # Inherit helpers only; routed regression cases run in their own file.
    def test_forward_grants_cannot_call_reverse_and_reverse_cannot_enroll_forward(self):
        import swap_session as forward
        import pilot_contract as forward_contract
        self.setup();s,h=self.draft();c=pilot.record(self.root,s)[0]['contract']
        for role,node in self.nodes.items():
            token=__import__('json').loads(self.tokens[role])
            original=(node.sessions()/(token['session_id']+'.json')).read_bytes()
            with self.assertRaises((ValueError,FileNotFoundError)):
                forward.Session(node.root,role,self.rpc[role]).call(token['session_id'],'enroll',canonical(c),'','')
            changed=copy.deepcopy(c);changed['profile']=forward_contract.ROUTED
            with self.assertRaises(ValueError):
                node.call(token['session_id'],'enroll',canonical(changed),'','')
            self.assertEqual((node.sessions()/(token['session_id']+'.json')).read_bytes(),original)
        self.assertFalse(any(m=='sendpay' for rpc in self.rpc.values() for m,p in rpc.calls))
    def test_both_direction_admission_guards_preserve_pending_records(self):
        import swap_session as forward
        import forward_swaps
        self.setup();s,h=self.draft();self.approve(s)
        with self.assertRaisesRegex(ValueError,'finish_current_swap_first'):forward_swaps.guard(self.root)
        for role,node in self.nodes.items():
            before=(node.records()/(s+'.json')).read_bytes()
            with self.assertRaises(ValueError):forward.Session(node.root,role,self.rpc[role]).prior()
            self.assertEqual((node.records()/(s+'.json')).read_bytes(),before)
    def test_actual_outgoing_htlc_expiry_and_id_survive_fresh_workers(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h);swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].ch['htlcs']=[dict(id=13,direction='out',payment_hash=h,amount_msat=1501000,
                                        expiry=1050,state='SENT_ADD_ACK_REVOCATION')]
        swaps.tick(self.root,factory=self.remote)
        original=pilot.record(self.root,s)[0]['outgoing_htlc']
        self.nodes={role:swap_session.Session(node.root,role,self.rpc[role]) for role,node in self.nodes.items()}
        swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,s)[0]['outgoing_htlc'],original)
        self.rpc['btc'].ch['htlcs'][0]['expiry']+=1
        with self.assertRaisesRegex(ValueError,'outgoing_htlc_changed'):swaps.tick(self.root,factory=self.remote)
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['btc'].calls),1)
    def test_expired_btc_htlc_never_means_failure_or_retry(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h);swaps.tick(self.root,factory=self.remote)
        self.rpc['btc'].height=2000
        for _ in range(2):swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='reverse-fail' for m,p in self.rpc['xbt'].calls))
        self.assertEqual(sum(m=='sendpay' for m,p in self.rpc['btc'].calls),1)
    def test_timing_margin_required_before_send_and_close_at_144(self):
        self.setup();s,h=self.draft();self.approve(s);self.hold(h)
        c=pilot.record(self.root,s)[0]['contract'];minimum=timing(c)['minimum']
        self.rpc['xbt'].height=1300-minimum+1
        with self.assertRaisesRegex(ValueError,'incoming_timing_refused'):swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))
        self.rpc['xbt'].height=1300-minimum
        swaps.tick(self.root,factory=self.remote)
        self.rpc['xbt'].height=1155;swaps.tick(self.root,factory=self.remote)
        self.assertFalse(any(m=='close' for m,p in self.rpc['xbt'].calls))
        self.rpc['xbt'].height=1156;swaps.tick(self.root,factory=self.remote)
        self.assertEqual(sum(m=='close' for m,p in self.rpc['xbt'].calls),1)
    def test_lost_publication_reply_reuses_original_quote_and_invoice_input(self):
        self.setup();s,h=self.draft()
        node=self.nodes['xbt'];original=node.rpc
        def lost(method,**params):
            result=original(method,**params)
            if method=='signinvoice':raise ValueError('lost_sign_reply')
            return result
        node.rpc=lost
        with self.assertRaises(ValueError):self.approve(s)
        before=load(node.records()/(s+'.json'))['publish']
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'publishing')
        node.rpc=original;swaps.tick(self.root,factory=self.remote)
        after=load(node.records()/(s+'.json'))['publish']
        for key in ('terms','unsigned'):self.assertEqual(before[key],after[key])
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'waiting_for_xbt')
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))
    def test_unpaid_quote_retirement_and_budget(self):
        self.setup(1);s,h=self.draft();self.approve(s)
        future=pilot.record(self.root,s)[0]['terms']['expires_at']+1
        for node in self.nodes.values():node.clock=lambda:future
        with patch.object(swaps.time,'time',lambda:future):swaps.tick(self.root,factory=self.remote)
        self.assertEqual(pilot.record(self.root,s)[0]['phase'],'expired')
        self.assertFalse(any(m=='sendpay' for m,p in self.rpc['btc'].calls))
        with self.assertRaisesRegex(ValueError,'session_expired_or_exhausted'):self.draft()
    def test_backup_cannot_omit_active_reverse_and_restore_blocks_authority(self):
        self.setup();s,h=self.draft()
        with self.assertRaisesRegex(ValueError,'backup_refused_unresolved_execution'):base.lifecycle.backup_begin(self.root/'execution')
        self.approve(s);self.complete(s,h)
        base.lifecycle.backup_begin(self.root/'execution');base.lifecycle.backup_end(self.root/'execution')
        self.assertEqual(base.lifecycle.snapshot(self.root/'execution')['jobs'][0]['direction'],'reverse')
        base.lifecycle.restored(self.root/'execution')
        with self.assertRaises(ValueError):self.draft()

# Do not repeat inherited suites in this focused file.
for name in dir(routed_cases.RoutedTests):
    if name.startswith('test_') and name not in ReverseAuthorityTests.__dict__:setattr(ReverseAuthorityTests,name,None)

class GateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parent.parent.parent
        sources=[root/'lightning/tools/blake2b',Path('/opt/xbt/libexec/xbt-swap'),Path('/usr/local/libexec/xbt-swap')]
        source=next((p for p in sources if (p/'reverse_gate.py').exists()),None)
        if source is None:raise RuntimeError('Pinned reverse gate source required')
        sys.path.insert(0,str(source));cls.source=source
        extension=root/'xbt-cln-startos/assets/xbt/reverse_repeat_gate.py'
        if not extension.exists():extension=Path('/opt/xbt/libexec/reverse_repeat_gate.py')
        spec=importlib.util.spec_from_file_location('tested_reverse_repeat_gate',extension)
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);cls.Gate=mod.Gate
    def setUp(self):
        helper=routed_cases.RoutedTests('test_two_routed_swaps_bind_actual_channel_and_fee_bearing_attempt_once');helper.setUp();self.addCleanup(helper.doCleanups)
        helper.setup();s,h=helper.draft();helper.approve(s)
        self.terms=copy.deepcopy(pilot.record(helper.root,s)[0]['terms']);self.preimage=helper.rpc['btc'].preimages[h]
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'gate.json'
        self.gate=self.Gate(self.path,live=True);self.gate.active=True
        self.request('reverse-repeat-register',quote=self.terms)
    def request(self,method,**params):return self.gate.handle(dict(id=7,method=method,params=params))
    def hook(self,**changes):
        t=self.terms;expiry=1000+t['min_cltv_delta']+24
        h=dict(short_channel_id=t['contract']['incoming_channels'][1]['short_channel_id'],id=9,payment_hash=t['payment_hash'],amount_msat=3000000,cltv_expiry=expiry,cltv_expiry_relative=expiry-1000)
        onion=dict(type='tlv',payment_secret=t['payment_secret'],forward_msat=3000000,total_msat=3000000,outgoing_cltv_value=expiry)
        h.update(changes.pop('htlc',{}));onion.update(changes)
        return dict(htlc=h,onion=onion)
    def test_pinned_xbt_invoice_codec_and_narrow_activation_context(self):
        import reverse_invoice
        import importlib
        # Reload only this stateless encoder helper; preserve the test's add stub.
        real=importlib.util.spec_from_file_location('real_reverse_invoice',reverse_invoice.__file__)
        module=importlib.util.module_from_spec(real);real.loader.exec_module(module)
        from reverse_activation import ACTIVE
        spec=importlib.util.spec_from_file_location('pinned_invoice_codec',self.source/'swap_invoice.py')
        codec=importlib.util.module_from_spec(spec);spec.loader.exec_module(codec)
        self.assertFalse(ACTIVE.get())
        with self.assertRaises(ValueError):codec.unsigned_invoice(self.terms['payment_hash'],'ab'*32,3000000,currency='xbt')
        with patch.dict(sys.modules,swap_invoice=codec):
            value=module.unsigned(self.terms['payment_hash'],'ab'*32,timing(self.terms['contract'])['invoice'])
        self.assertTrue(value.startswith('lnxbt'));self.assertFalse(ACTIVE.get())
    def test_gate_rejects_mpp_wrong_amount_or_unapproved_channel(self):
        for params in (self.hook(total_msat=6000000),self.hook(forward_msat=1),self.hook(htlc=dict(short_channel_id='9x9x9'))):
            self.assertEqual(self.request('htlc_accepted',**params)[0]['result']['result'],'fail')
            self.assertEqual(self.request('reverse-status',payment_hash=self.terms['payment_hash'])[0]['result']['phase'],'quoted')
    def test_restart_exact_replay_and_release_binding(self):
        params=self.hook();self.assertEqual(self.request('htlc_accepted',**params),[])
        original=self.path.read_bytes();self.gate=self.Gate(self.path,live=True);self.gate.active=True
        changed=self.hook(htlc=dict(amount_msat=1))
        self.assertEqual(self.request('htlc_accepted',**changed),[]);self.assertEqual(self.path.read_bytes(),original)
        self.assertEqual(self.request('htlc_accepted',**params),[])
        binding=[params['htlc']['short_channel_id'],9]
        with self.assertRaises(ValueError):self.request('reverse-release',payment_hash=self.terms['payment_hash'],binding=[binding[0],10],preimage=self.preimage)
        result=self.request('reverse-release',payment_hash=self.terms['payment_hash'],binding=binding,preimage=self.preimage)
        self.assertEqual(result[0]['result']['result'],'resolve')
        self.gate=self.Gate(self.path,live=True);self.gate.active=True
        self.assertEqual(self.request('htlc_accepted',**params)[0]['result']['result'],'resolve')
    def test_retirement_is_durable_even_after_clock_rollback(self):
        with patch('time.time',lambda:self.terms['expires_at']+1):self.request('reverse-retire-repeat',payment_hash=self.terms['payment_hash'])
        self.gate=self.Gate(self.path,live=True);self.gate.active=True
        with patch('time.time',lambda:self.terms['expires_at']-60):
            self.assertEqual(self.request('htlc_accepted',**self.hook())[0]['result']['result'],'fail')
    def test_registered_terms_cannot_be_changed_or_retired_after_acceptance(self):
        changed=copy.deepcopy(self.terms);changed['btc_amount_msat']=1500001
        with self.assertRaises(ValueError):self.request('reverse-repeat-register',quote=changed)
        self.request('htlc_accepted',**self.hook())
        with patch('time.time',lambda:self.terms['expires_at']+1),self.assertRaises(ValueError):self.request('reverse-retire-repeat',payment_hash=self.terms['payment_hash'])

if __name__=='__main__':unittest.main()
