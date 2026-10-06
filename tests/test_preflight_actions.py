import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
import preflight_actions as a
import live_preflight as p
import test_live_preflight as fixtures

class ActionTests(unittest.TestCase):
    def request(self,reverse=False):
        r=dict(invoice='SIGNED_PRIVATE_INVOICE',incomingChannel='1x1x1',outgoingChannel='2x2x2',btcRune='INSPECT_BTC',xbtRune='INSPECT_XBT')
        if reverse:r.update(xbtSats=2000,routeDelay=40)
        return r
    def test_exact_conversion_without_persistence(self):
        for reverse in (False,True):
            request=self.request(reverse);original=copy.deepcopy(request)
            with patch.object(a,'inspect',return_value={'preflight_matches':True}) as inspect:
                a.action(Path('/data'),'reverse' if reverse else 'forward',request)
            args,kw=inspect.call_args
            self.assertEqual(kw,{'derive_timing':True})
            self.assertEqual(args[1]['credentials'],{'btc':'INSPECT_BTC','xbt':'INSPECT_XBT'})
            self.assertEqual(args[1]['candidate']['btc_amount_msat'],1500000 if reverse else 1000000)
            self.assertEqual(request,original)
    def test_invalid_ui_inputs_never_reach_transport(self):
        for key,value in [('xbtSats',True),('xbtSats','2000'),('xbtSats',0),('routeDelay',577),('btcRune',None),('endpoint','https://attacker')]:
            r=self.request(True);r[key]=value
            with patch.object(a,'inspect') as inspect:
                self.assertFalse(a.action(Path('/data'),'reverse',r)['preflight_matches'])
                inspect.assert_not_called()
    def test_derived_timing_passes_real_inspector(self):
        for reverse in (False,True):
            f=fixtures.PreflightTests();f.setup_case(reverse)
            def factory(node,rune):
                role='btc' if node['node_id']==fixtures.BTC else 'xbt'
                class RPC:
                    def call(self,method,**params):return copy.deepcopy(f.responses[role][method])
                return RPC()
            r=a.action(Path('/data'),'reverse' if reverse else 'forward',self.request(reverse),
                factory=factory,clock=lambda:1000,loader=lambda root:copy.deepcopy(f.config))
            self.assertTrue(r['preflight_matches'],r)
            self.assertEqual(r['proposed_incoming_expiry'],314 if reverse else 900300)
            self.assertEqual(r['quote_expires_at'],1120)
            self.assertFalse(r['execution_authorized'])
    def test_credential_methods_match_transport(self):
        root=Path(__file__).resolve().parents[2]
        import ast
        for role,directory in [('btc','swaps'),('xbt','xbt')]:
            source=root/(role+'-cln-startos')/'assets'/directory/'inspection_credential.py'
            if not source.exists():continue  # independent repo checkout on packaging host
            tree=ast.parse(source.read_text())
            cls=next(n for n in tree.body if isinstance(n,ast.ClassDef))
            rule=next(ast.literal_eval(n.value) for n in cls.body if isinstance(n,ast.Assign) and n.targets[0].id=='restrictions')
            self.assertEqual(rule,p.restrictions())
    def test_invalid_direction(self):
        with patch.object(a,'inspect') as inspect:
            self.assertFalse(a.action(Path('/data'),'live',self.request())['preflight_matches'])
            inspect.assert_not_called()

if __name__=='__main__':unittest.main()
