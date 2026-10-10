"""Real gate journals with market contracts; no daemon or external service required."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import test_market_swaps as fixtures
import market_terms as mt
import bound_release
SOURCE=Path(os.environ.get('SWAP_FIXTURE_PINNED_SOURCE','/opt/swap'))
sys.path.insert(0,str(SOURCE))
sys.path.insert(0,str(Path(__file__).resolve().parents[1].parent/'xbt-cln-startos/assets/xbt'))
import reverse_repeat_gate

class Gates(unittest.TestCase):
    def fixture(self,reverse=False):
        if hasattr(self,"active_fixture"):self.active_fixture.doCleanups()
        f=(fixtures.Reverse if reverse else fixtures.Forward)();f.setUp();self.active_fixture=f;self.addCleanup(f.doCleanups)
        f.setup();s,h=f.draft();f.approve(s)
        q=copy.deepcopy(f.rpc[f.incoming].quotes[h]['terms'])
        preimage=f.rpc[f.outgoing].preimages[h]
        script=Path(f.tmp.name)/'gate.py'
        def run(requests):
            output=io.StringIO()
            init=dict(id=1,method='init',params={'configuration':{'network':'xbt' if reverse else 'bitcoin'},'options':{'xbt-live-pilot':mt.GATE}})
            with patch.object(sys,'stdin',io.StringIO('\n'.join(map(json.dumps,[init,*requests])))),contextlib.redirect_stdout(output):
                if reverse:
                    from reverse_activation import ACTIVE
                    token=ACTIVE.set(True)
                    try:reverse_repeat_gate.main(script.with_suffix('.quotes.json'),live=True)
                    finally:ACTIVE.reset(token)
                else:bound_release.run(os.environ['BTC_GATE_TEST_SOURCE'],script)
            return [json.loads(x) for x in output.getvalue().splitlines() if x.strip()]
        htlc=dict(short_channel_id='2x2x0',id=1,payment_hash=h,amount_msat=q[f.incoming+'_amount_msat'],cltv_expiry=1300,cltv_expiry_relative=300)
        hook=dict(id=3,method='htlc_accepted',params=dict(htlc=htlc,onion=dict(payment_secret=q['payment_secret'],forward_msat=htlc['amount_msat'],total_msat=htlc['amount_msat'],type='tlv',outgoing_cltv_value=1300)))
        register=dict(id=2,method='reverse-repeat-register' if reverse else 'xbt-register',params={'quote':q})
        return f,q,preimage,script,run,register,hook
    def test_both_gates_replay_exact_held_market_terms_after_expiry(self):
        for rev in (False,True):
            with self.subTest(reverse=rev):
                f,q,p,script,run,register,hook=self.fixture(rev)
                self.assertTrue(run([register])[-1]['result']['registered'])
                run([hook]);path=script.with_suffix('.quotes.json');before=path.read_bytes()
                with patch.object(time,'time',return_value=q['expires_at']+500):run([hook])
                self.assertEqual(path.read_bytes(),before)
                release=dict(id=4,method='reverse-release' if rev else 'xbt-release-bound',params=dict(payment_hash=q['payment_hash'],preimage=p))
                if rev:release['params']['binding']=['2x2x0',1]
                replies=run([hook,release]);self.assertTrue(any(x.get('result',{}).get('result')=='resolve' for x in replies))
                row=json.loads(path.read_text())[q['payment_hash']];self.assertEqual(row['terms'],q)
                self.assertIn(row['phase'],('resolved','released'))
    def test_amount_split_channel_and_tampered_price_are_refused(self):
        for rev in (False,True):
            with self.subTest(reverse=rev):
                f,q,p,script,run,register,hook=self.fixture(rev)
                bad=copy.deepcopy(register);bad['params']['quote']['contract']['pricing']['markup_bps']=200
                self.assertIn('error',run([bad])[-1]);self.assertTrue(run([register])[-1]['result']['registered'])
                for field,value in [('amount_msat',hook['params']['htlc']['amount_msat']//2),('short_channel_id','9x9x9')]:
                    denied=copy.deepcopy(hook);denied['params']['htlc'][field]=value
                    rows=run([denied]);self.assertEqual(rows[-1]['result']['result'],'fail')
                self.assertEqual(json.loads(script.with_suffix('.quotes.json').read_text())[q['payment_hash']]['phase'],'quoted')

if __name__=='__main__':unittest.main()
