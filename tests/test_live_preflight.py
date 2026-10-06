import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'assets'))
import live_preflight as p
from read_only_rpc import Client

BTC = '02' + 'a'*64
XBT = '03' + 'b'*64
PAYEE = '02' + 'c'*64


class PreflightTests(unittest.TestCase):
    def setup_case(self, reverse=False):
        self.config = dict(generation='a'*32, nodes={
            'btc':dict(node_id=BTC, url='https://private-btc', ca_pem='PRIVATE_CA', rune='MONITOR'),
            'xbt':dict(node_id=XBT, url='https://private-xbt', ca_pem='PRIVATE_CA', rune='MONITOR')})
        self.request = dict(credentials=dict(btc='INSPECT_BTC', xbt='INSPECT_XBT'), candidate=dict(
            policy_digest=p.policy.digest(), profile=p.policy.REVERSE if reverse else p.policy.FORWARD,
            btc_amount_msat=1500000 if reverse else 1000000, xbt_amount_msat=2000000,
            invoice='SIGNED_PRIVATE_INVOICE', incoming_channel='1x1x1', outgoing_channel='2x2x2',
            route_delay_blocks=40, quote_expires_at=1120, incoming_expiry=290 if reverse else 900300))
        self.responses = {}
        self.calls = []
        for role in ('btc','xbt'):
            outgoing = role == ('btc' if reverse else 'xbt')
            self.responses[role] = dict(
                getinfo=dict(id=BTC if role=='btc' else XBT,network='bitcoin' if role=='btc' else 'xbt',blockheight=900000 if role=='btc' else 100),
                decode=dict(valid=True,type='bolt11 invoice',currency='bc' if reverse else 'xbt',
                    amount_msat=1500000 if reverse else 2000000, payment_hash='a'*64,payment_secret='b'*64,
                    payee=PAYEE,created_at=900,expiry=600,min_final_cltv_expiry=18),
                listfunds=dict(outputs=[dict(status='confirmed',reserved=False,amount_msat=50000000)]),
                listsendpays=dict(payments=[]),
                listpeerchannels=dict(channels=[dict(short_channel_id='2x2x2' if outgoing else '1x1x1',
                    peer_id=PAYEE,state='CHANNELD_NORMAL',peer_connected=True,htlcs=[],
                    receivable_msat=100000000,spendable_msat=100000000,feerate=dict(perkw=100),dust_limit_msat=546000)]))

    def run_case(self, clock=None, loader=None):
        def factory(node, rune):
            role='btc' if node['node_id']==BTC else 'xbt'
            self.assertEqual(rune, self.request['credentials'][role])
            outer=self
            class Fake:
                def call(self, method, **params):
                    outer.calls.append((role,method,params))
                    response=outer.responses[role][method]
                    if isinstance(response,Exception): raise response
                    return copy.deepcopy(response)
            return Fake()
        return p.inspect(Path('/nonexistent'),self.request,factory=factory,clock=clock or (lambda:1000),
                         loader=loader or (lambda root:copy.deepcopy(self.config)))

    def test_both_directions_never_authorize_or_leak(self):
        for reverse in (False,True):
            self.setup_case(reverse); r=self.run_case()
            self.assertTrue(r['preflight_matches'],r)
            for flag in ('execution_authorized','live_payment_enabled','payment_started','held_htlc_verified','deadline_protection_verified'):
                self.assertIs(r[flag],False)
            text=json.dumps(r)
            for secret in ('PRIVATE','MONITOR','INSPECT',PAYEE,'a'*64,'https://'):self.assertNotIn(secret,text)
            self.assertEqual([(a,b) for a,b,_ in self.calls[:2]],[('btc','getinfo'),('xbt','getinfo')])
            self.assertTrue(set(m for _,m,_ in self.calls)<=set(p.READS))

    def test_both_identities_before_sensitive_reads(self):
        self.setup_case(); self.responses['xbt']['getinfo']['id']=BTC
        self.assertFalse(self.run_case()['preflight_matches'])
        self.assertEqual([m for _,m,_ in self.calls],['getinfo','getinfo'])

    def test_invoice_failures(self):
        for key,value in [('valid',False),('currency','xbtrt'),('amount_msat',2000001),('amount_msat',True),
                          ('payment_hash','no'),('payment_secret',None),('payee',BTC),('expiry',1),('created_at',1001),('min_final_cltv_expiry',41)]:
            with self.subTest(key=key,value=value):
                self.setup_case(); self.responses['xbt']['decode'][key]=value
                self.assertFalse(self.run_case()['preflight_matches'])

    def test_liquidity_trim_and_channel_binding(self):
        for key,value in [('receivable_msat',999999),('feerate',{'perkw':10000}),('dust_limit_msat',1000000),
                          ('peer_connected',1),('state','ONCHAIN'),('htlcs',[{}]),('short_channel_id','9x9x9')]:
            with self.subTest(key=key):
                self.setup_case();self.responses['btc']['listpeerchannels']['channels'][0][key]=value
                self.assertFalse(self.run_case()['preflight_matches'])
        self.setup_case();self.responses['xbt']['listpeerchannels']['channels'][0]['spendable_msat']=1999999
        self.assertFalse(self.run_case()['preflight_matches'])

    def test_reserves_confirmed_unreserved_and_strict(self):
        for key,value in [('reserved',True),('reserved',0),('status','unconfirmed'),('amount_msat',49999999),('amount_msat','50000000')]:
            self.setup_case();self.responses['btc']['listfunds']['outputs'][0][key]=value
            self.assertFalse(self.run_case()['preflight_matches'])

    def test_existing_attempt_on_either_node_refused(self):
        for role in ('btc','xbt'):
            self.setup_case();self.responses[role]['listsendpays']['payments']=[{'status':'failed'}]
            self.assertFalse(self.run_case()['preflight_matches'])

    def test_freshness_clock_and_expiry(self):
        for end in (999,1120,1121):
            self.setup_case(); times=iter((1000,end))
            self.assertFalse(self.run_case(clock=lambda:next(times))['preflight_matches'])

    def test_pairing_change_discards_report(self):
        self.setup_case();changed=copy.deepcopy(self.config);changed['generation']='b'*32
        configs=iter((self.config,changed))
        self.assertEqual(self.run_case(loader=lambda root:next(configs))['reasons'],['pairing_changed_during_inspection'])

    def test_numeric_and_schema_failures(self):
        for key,value in [('btc_amount_msat',True),('btc_amount_msat',1000001),('incoming_expiry',900287),
                          ('route_delay_blocks',True),('policy_digest','bad'),('route_hops',2)]:
            self.setup_case();self.request['candidate'][key]=value
            self.assertFalse(self.run_case()['preflight_matches'])

    def test_node_warning_and_failure_privacy(self):
        self.setup_case();self.responses['btc']['getinfo']['warning_bitcoind_sync']='secret'
        self.assertFalse(self.run_case()['preflight_matches'])
        for exception in (RuntimeError('https://private RUNE'),p.Refused('SECRET')):
            self.setup_case();self.responses['btc']['listfunds']=exception
            self.assertEqual(self.run_case()['reasons'],['preflight_unavailable'])

    def test_readonly_transport_rejects_before_open(self):
        transport=p.Inspector.__new__(p.Inspector)
        for method,params in [('sendpay',{}),('xbt-register',{}),('close',{}),('decode',{'string':'x','extra':1}),('listsendpays',{'payment_hash':'invalid'})]:
            with self.assertRaises(p.Refused):transport.call(method,**params)
        self.assertEqual(Client.methods,('getinfo','listpeerchannels'))

    def test_transport_body_bound_and_errors_private(self):
        transport=p.Inspector.__new__(p.Inspector)
        class Reply(io.BytesIO):
            def __enter__(self):return self
            def __exit__(self,*args):self.close()
        class Opener:
            def open(self,request,timeout):
                self.request=request;self.timeout=timeout
                return Reply(self.raw)
        class C:pass
        transport.client=C();transport.client.url='https://private';transport.client.rune='RUNE';opener=Opener();transport.client.opener=opener
        opener.raw=b'{"valid":true}'
        self.assertEqual(transport.call('decode',string='invoice'),{'valid':True})
        self.assertEqual(json.loads(opener.request.data),{'string':'invoice'})
        self.assertEqual(opener.timeout,15)
        for raw in (b'x'*(p.LIMIT+1),b'{"error":"SECRET"}',b'[]',b'not json'):
            opener.raw=raw
            with self.assertRaisesRegex(p.Refused,'^preflight_rpc_unavailable$'):transport.call('getinfo')

    def test_height_changes_use_later_incoming_height(self):
        self.setup_case()
        original=p.identity
        counts={}
        def advancing(client,node,network):
            value=original(client,node,network)
            counts[network]=counts.get(network,0)+1
            if counts[network]==2 and network=='bitcoin':value['blockheight']+=13
            return value
        with patch.object(p,'identity',side_effect=advancing):
            self.assertEqual(self.run_case()['reasons'],['numeric_policy_rejected'])
        self.setup_case();counts.clear()
        def regressing(client,node,network):
            value=original(client,node,network)
            counts[network]=counts.get(network,0)+1
            if counts[network]==2:value['blockheight']-=1
            return value
        with patch.object(p,'identity',side_effect=regressing):
            self.assertEqual(self.run_case()['reasons'],['chain_height_regressed'])

    def test_cli_leaves_restore_barrier_and_jobs_untouched(self):
        self.setup_case()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'execution').mkdir()
            (root/'execution'/'restored.json').write_text('{"blocked":true}')
            before={str(f.relative_to(root)):f.read_bytes() for f in root.rglob('*') if f.is_file()}
            result=subprocess.run([sys.executable,str(Path(p.__file__)),str(root),'inspect'],
                input=json.dumps(self.request),text=True,capture_output=True)
            self.assertEqual(json.loads(result.stdout)['reasons'],['pair_nodes_first'])
            self.assertEqual(result.stderr,'')
            self.assertEqual(before,{str(f.relative_to(root)):f.read_bytes() for f in root.rglob('*') if f.is_file()})

    def test_cli_bad_input_private(self):
        result=subprocess.run([sys.executable,str(Path(p.__file__)),'/nonexistent','inspect'],input='SECRET',text=True,capture_output=True)
        self.assertEqual(result.returncode,1);self.assertEqual(result.stderr,'');self.assertNotIn('SECRET',result.stdout)


if __name__=='__main__':unittest.main()
