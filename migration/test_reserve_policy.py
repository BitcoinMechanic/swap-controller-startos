import copy,unittest
import cross_contract as cc
import customer_remote as remote
from test_cross_contract import example,NOW
class Tests(unittest.TestCase):
    def test_exact_minimum_and_shortfalls_in_both_directions(self):
        self.assertEqual(cc.RESERVE_MSAT,25000000)
        for direction in ('forward','reverse'):
            c,obs=example(direction)
            for item in obs.values():item['reserve_msat']=25000000
            cc.admission(c,obs,now=NOW)
            for kind in ('incoming','outgoing'):
                low=copy.deepcopy(obs);low[kind]['reserve_msat']=24999999
                with self.assertRaisesRegex(ValueError,'^contract_reserve_insufficient$'):cc.admission(c,low,now=NOW)
                for invalid in (None,True,-1,'25000000'):
                    bad=copy.deepcopy(obs);bad[kind]['reserve_msat']=invalid
                    with self.assertRaisesRegex(ValueError,'^contract_inspection_unavailable$'):cc.admission(c,bad,now=NOW)
    def test_only_confirmed_unreserved_outputs_count(self):
        def output(i,amount,status='confirmed',reserved=False):
            return dict(txid=('%02x'%i)*32,output=0,amount_msat=amount,status=status,reserved=reserved)
        rows=[output(1,25000000),output(2,90000000,'unconfirmed'),output(3,90000000,reserved=True)]
        self.assertEqual(remote.reserve({'outputs':rows}),25000000)
    def test_new_error_is_safe_to_display(self):
        import service_cli
        self.assertIn('contract_reserve_insufficient',service_cli.SAFE)
if __name__=='__main__':unittest.main()
