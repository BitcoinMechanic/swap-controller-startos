"""Local wiring never expands authority, overwrites manual setup or partially pairs."""
import copy
import json
import tempfile
from pathlib import Path
import unittest

import bounded_authority as ba
import held_gate as hg
import local_pairing
import service
from test_cross_worker import Remote
from test_customer_https import nodes
from test_route_plan import NOW

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'controller';service.initialize(self.root)
        self.j=hg.Journal(self.root);self.addCleanup(self.j.close)
        self.bindings={d:dict(direction=d,nodes=nodes(Remote(d))) for d in service.DIRECTIONS}
        for kind,other in [('incoming','outgoing'),('outgoing','incoming')]:
            self.bindings['reverse']['nodes'][other]['grant']['chain']=copy.deepcopy(self.bindings['forward']['nodes'][kind]['grant']['chain'])
        self.calls=[];self.fail=None
        outer=self
        class Client:
            def __init__(self,config):self.config=config
            def call(self,op,body=None):
                outer.calls.append(op)
                if outer.fail is not None and len(outer.calls)>=outer.fail:raise OSError('PRIVATE credential unavailable')
                g=self.config['grant']
                return dict(format='swap-customer-remote-v1',grant=g,paused=False,current=True,remaining=3,reserved_msat=0,regtest_only=True)
        self.s=service.Service(self.j,factory=Client,clock=lambda:NOW)
    def pair(self):return self.s.pair_local(self.bindings,confirmed=True)
    def test_atomic_both_directions_and_noop_retry(self):
        self.pair();self.assertEqual(set(self.calls),{'info'})
        self.assertEqual(len(self.s.record['pairings']),2)
        saved=(self.root/'state.json').read_bytes();calls=len(self.calls)
        self.pair();self.assertEqual(saved,(self.root/'state.json').read_bytes());self.assertEqual(len(self.calls),calls)
    def test_second_node_failure_leaves_everything_unpaired(self):
        saved=(self.root/'state.json').read_bytes();self.fail=3
        with self.assertRaises(OSError):self.pair()
        self.assertEqual(saved,(self.root/'state.json').read_bytes())
    def test_manual_pairing_never_adopted_even_if_matching(self):
        self.s.pair(self.bindings['forward'],confirmed=True);saved=(self.root/'state.json').read_bytes()
        with self.assertRaises(ValueError):self.pair()
        self.assertEqual(saved,(self.root/'state.json').read_bytes())
    def test_explicit_manual_reselection_ends_automatic_ownership(self):
        self.pair()
        self.s.pair(self.bindings['forward'],confirmed=True)
        saved=(self.root/'state.json').read_bytes()
        self.assertIsNone(self.s.record['local_selected']['forward'])
        with self.assertRaises(ValueError):self.pair()
        self.assertEqual(saved,(self.root/'state.json').read_bytes())
    def test_renewal_keeps_old_generations(self):
        self.pair();old=copy.deepcopy(self.s.record['pairings'])
        for b in self.bindings.values():
            for n in b['nodes'].values():n['grant']['nonce']='aa'*32
        self.pair();self.assertEqual(len(self.s.record['pairings']),4)
        for k,v in old.items():self.assertEqual(self.s.record['pairings'][k],v)
    def test_unrelated_node_in_opposite_direction_refused(self):
        self.bindings['reverse']['nodes']['outgoing']['grant']['chain']['node_id']='02'+'99'*32
        with self.assertRaises(ValueError):self.pair()
        self.assertEqual(self.s.record['pairings'],{})
    def test_restore_and_pause_do_not_reenable_authority(self):
        self.s.record['paused']=True
        with self.assertRaises(ValueError):self.pair()
        self.s.record['paused']=False;(self.root.parent/'swap-restored.json').write_text('{}')
        with self.assertRaises(ValueError):self.pair()
        self.assertEqual(self.calls,[])
    def test_private_bundle_maps_roles_without_changing_grants(self):
        config={}
        for name,direction in [('btc','forward'),('xbt','reverse')]:
            inc=self.bindings[direction]['nodes']['incoming'];other='reverse' if direction=='forward' else 'forward'
            out=self.bindings[other]['nodes']['outgoing']
            config[name]=dict(url=inc['url'],ca_pem=inc['ca_pem'],credentials={k:{f:n[f] for f in ('grant','rune')}
                for k,n in [('incoming',inc),('outgoing',out)]})
        got=local_pairing.bindings(config)
        for d in service.DIRECTIONS:
            for k in ('incoming','outgoing'):self.assertEqual(got[d]['nodes'][k]['grant'],self.bindings[d]['nodes'][k]['grant'])
        config['btc']['credentials']['incoming']['grant']['kind']='outgoing'
        with self.assertRaises(ValueError):local_pairing.bindings(config)

if __name__=='__main__':unittest.main()
