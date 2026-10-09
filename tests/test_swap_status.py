"""Operator status stays read-only and never exports private grant/RPC fields."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'assets'))
import controller
import forward_swaps as forward
import reverse_swaps as reverse
import swap_setup
import swap_status as status


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.now = 2000000000
        (self.root/'execution').mkdir()
        self.config = dict(schema=1, generation='a'*32, nodes={role:dict(node_id='02'+n*64, role=role, url='PRIVATE_URL', rune='PRIVATE_RUNE', ca_pem='PRIVATE_CERT') for role,n in [('btc','1'),('xbt','2')]})
        for module in (controller, forward, reverse, swap_setup):
            p = patch.object(module,'load_config',lambda root:self.config);p.start();self.addCleanup(p.stop)
        controller.save(self.root/'status.json',dict(generation='a'*32,checked_at=self.now,nodes={role:dict(reachable=True,identity_matches=True) for role in ('btc','xbt')}))
        controller.save(self.root/'execution/heartbeat.json',dict(checked_at=self.now,mode='disabled'))
        self.bound = swap_setup.binding(self.root,self.config)
        controller.save(self.root/swap_setup.FILE,dict(schema=1,**self.bound,credentials=dict(btc='PRIVATE_BTC',xbt='PRIVATE_XBT')))
        self.calls=[]; self.rows={}; self.fail=None; self.on_call=None
        self.factories={}
        for direction,module in [('forward',forward),('reverse',reverse)]:
            tokens={role:json.dumps(dict(session_id=n*64,rune='PRIVATE_GRANT')) for role,n in [('btc','3'),('xbt','4')]}
            controller.save(self.root/module.FILE,dict(**self.bound,credentials=tokens))
            self.rows[direction]={role:dict(session_id=json.loads(tokens[role])['session_id'],node_id=self.config['nodes'][role]['node_id'],
                network={'btc':'bitcoin','xbt':'xbt'}[role],current=True,paused=False,gate_ready=True,routed=True,remaining=5,
                expires_at=self.now+100,profile='startos-reverse-repeat-v3',max_delay_blocks=288,
                channel='PRIVATE_CHANNEL',rune='PRIVATE_RUNE',raw_rpc='PRIVATE_RPC') for role in ('btc','xbt')}
            owner=self
            def factory(node,token,direction=direction):
                role=node['role']
                class Remote:
                    def request(self,operation,**kwargs):
                        owner.calls.append((direction,role,operation,kwargs))
                        assert operation=='info' and kwargs=={}
                        if owner.on_call:owner.on_call()
                        if owner.fail==(direction,role):raise ValueError('PRIVATE_URL PRIVATE_RUNE PRIVATE_RPC')
                        return copy.deepcopy(owner.rows[direction][role])
                return Remote()
            self.factories[direction]=factory
    def snapshot(self):
        return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
    def report(self):
        before=self.snapshot();result=status.summary(self.root,factories=self.factories,now=self.now)
        self.assertEqual(before,self.snapshot())
        self.assertNotIn('PRIVATE',json.dumps(result))
        return result
    def test_both_directions_info_only_and_no_writes(self):
        result=self.report()
        self.assertEqual(len(self.calls),4)
        for d in result['directions'].values():self.assertEqual(d['state'],'ready_to_prepare')
        self.assertEqual(result['directions']['reverse']['nodes']['btc']['max_delay_blocks'],288)
    def test_old_timing_caps_and_direct_grants_display_exact_limits(self):
        for limit,profile in [(80,'startos-reverse-repeat-v1'),(144,'startos-reverse-repeat-v2')]:
            for r in self.rows['reverse'].values():r.update(profile=profile,max_delay_blocks=limit)
            for r in self.rows['forward'].values():r['routed']=False
            report=self.report()['directions']
            self.assertEqual(report['reverse']['nodes']['btc']['max_delay_blocks'],limit)
            self.assertEqual(report['forward']['nodes']['btc']['max_delay_blocks'],40)
            self.assertEqual(report['forward']['nodes']['btc']['max_hops'],1)
            self.assertEqual(report['forward']['nodes']['btc']['max_fee_msat'],0)
    def test_expired_paused_exhausted_replaced_grants_remain_inspectable(self):
        self.rows['forward']['btc'].update(expires_at=self.now-1,paused=True,remaining=0,current=False)
        row=self.report()['directions']['forward']['nodes']['btc']
        self.assertEqual(set(row['blockers']),{'grant_expired','grant_paused','grant_exhausted','grant_replaced'})
    def test_identity_or_session_mismatch_is_unavailable(self):
        for field in ('node_id','session_id','network'):
            with self.subTest(field=field):
                old=self.rows['reverse']['btc'][field];self.rows['reverse']['btc'][field]='PRIVATE_BAD'
                self.assertEqual(self.report()['directions']['reverse']['nodes']['btc'],{'state':'unavailable'})
                self.rows['reverse']['btc'][field]=old
    def test_untrusted_numbers_bools_profiles_and_transport_are_filtered(self):
        original=copy.deepcopy(self.rows['reverse']['btc'])
        for field,value in [('remaining',True),('current','PRIVATE_TRUE'),('profile','PRIVATE_PROFILE'),('max_delay_blocks',2016),('expires_at','PRIVATE_TIME')]:
            self.rows['reverse']['btc']=dict(original,**{field:value})
            self.assertEqual(self.report()['directions']['reverse']['nodes']['btc'],{'state':'unavailable'})
        self.rows['reverse']['btc']=original;self.fail=('forward','xbt')
        self.assertEqual(self.report()['directions']['forward']['nodes']['xbt'],{'state':'unavailable'})
    def test_grant_mode_and_timing_mismatches_block_new_preparation(self):
        self.rows['forward']['btc']['routed']=False
        self.rows['reverse']['btc'].update(profile='startos-reverse-repeat-v1',max_delay_blocks=80)
        result=self.report()['directions']
        self.assertIn('grant_modes_differ',result['forward']['blockers'])
        self.assertIn('grant_timing_limits_differ',result['reverse']['blockers'])
    def test_missing_setup_and_old_pairing_do_not_contact_grants(self):
        for module in (forward,reverse):(self.root/module.FILE).unlink()
        self.assertTrue(all('grants_not_paired' in r['blockers'] for r in self.report()['directions'].values()))
        self.assertEqual(self.calls,[])
    def test_restore_epoch_invalidates_old_pairing_without_mutation(self):
        controller.save(self.root/'execution/forward-pilot-restore-epoch.json',dict(epoch='new'))
        controller.save(self.root/'execution/restored.json',dict(blocked=True))
        result=self.report();self.assertTrue(result['restore_barrier_present'])
        self.assertTrue(all('grant_pairing_changed' in r['blockers'] for r in result['directions'].values()))
        self.assertEqual(self.calls,[])
    def test_restore_marker_alone_does_not_mislabel_freshly_bound_setup(self):
        controller.save(self.root/'execution/restored.json',dict(blocked=True))
        self.assertTrue(all(d['state']=='ready_to_prepare' for d in self.report()['directions'].values()))
    def test_stale_or_future_observation_and_backup_are_reported(self):
        for age in (-1,121):
            controller.save(self.root/'status.json',dict(generation='a'*32,checked_at=self.now-age,nodes={}))
            controller.save(self.root/'execution/heartbeat.json',dict(checked_at=self.now-age,mode='disabled'))
            controller.save(self.root/'execution/backup-paused.json',dict(paused=True))
            result=self.report()
            self.assertEqual(result['pairing'],'waiting');self.assertEqual(result['worker'],'stale')
            self.assertIn('backup_in_progress',result['directions']['forward']['blockers'])
    def test_pending_and_unreadable_legacy_records_remain_visible(self):
        old=self.root/'execution/forward-pilot';old.mkdir();controller.save(old/'record.json',dict(phase='send_intent'))
        result=self.report();self.assertTrue(result['legacy_pilot_present'])
        self.assertIn('legacy_pilot_unfinished',result['directions']['forward']['blockers'])
        p=self.root/'execution/reverse-swaps'/('a'*64);p.mkdir(parents=True)
        controller.save(p/'record.json',dict(pilot_id='a'*64,phase='waiting_for_xbt',restore_epoch=None,invoice='PRIVATE_INVOICE'))
        self.assertEqual(self.report()['directions']['reverse']['active'],1)
    def test_configuration_change_during_observation_discards_grant_results(self):
        self.on_call=lambda:self.config.update(generation='b'*32)
        result=status.summary(self.root,factories=self.factories,now=self.now)
        self.assertTrue(all(d['nodes']=={} and 'configuration_changed' in d['blockers'] for d in result['directions'].values()))
    def test_empty_installation_does_not_create_directories(self):
        for module in (controller,forward,reverse,swap_setup):
            patcher=patch.object(module,'load_config',lambda root:None);patcher.start();self.addCleanup(patcher.stop)
        root=self.root/'not-created'
        result=status.summary(root,now=self.now)
        self.assertFalse(root.exists());self.assertEqual(result['pairing'],'missing')
        self.assertFalse(self.calls)

if __name__=='__main__':unittest.main()
