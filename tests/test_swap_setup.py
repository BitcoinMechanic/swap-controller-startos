import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'assets'))
import swap_setup as setup
import forward_pilot as pilot
import lifecycle
from controller import private_load, save

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); lifecycle.setup(self.root / 'execution')
        self.config = dict(generation='g', nodes={k:dict(node_id=k,role=k) for k in ('btc','xbt')})
        self.rows = {k:[dict(short_channel_id='1x1x0', peer_id='recipient', state='CHANNELD_NORMAL',
                       peer_connected=True, htlcs=[], receivable_msat=10000000, spendable_msat=10000000,
                       feerate={'perkw':253}, dust_limit_msat=546000)] for k in ('btc','xbt')}
        self.calls=[]; self.broken=None
        owner=self
        class Fake:
            def __init__(self,node,rune): self.role=node['role']
            def call(self,method,**params):
                owner.calls.append(method)
                if owner.broken == self.role: raise ValueError('private-rune-and-url')
                if method=='getinfo':return dict(id=self.role,network='bitcoin' if self.role=='btc' else 'xbt',blockheight=10)
                if method=='listpeerchannels':return dict(channels=copy.deepcopy(owner.rows[self.role]))
                if method=='listfunds':return dict(outputs=[])
                if method=='decode':return dict(valid=True,type='bolt11 invoice',currency='xbt',amount_msat=2000000,payee='recipient')
                raise AssertionError(method)
        self.factory=Fake
        for module in (setup,pilot):
            p=patch.object(module,'load_config',lambda root:self.config);p.start();self.addCleanup(p.stop)
        self.request=dict(btcRune='btc-private',xbtRune='xbt-private',confirmed=True)
    def configure(self):return setup.configure(self.root,self.request,factory=self.factory)
    def inspect(self):return setup.inspect(self.root,{'invoice':'lnxbt-fixture'},factory=self.factory)
    def test_private_save_and_read_only_discovery(self):
        self.configure(); result=self.inspect()
        self.assertTrue(result['automatic_selection'])
        self.assertNotIn('private',json.dumps(result))
        self.assertEqual((self.root/setup.FILE).stat().st_mode & 0o777,0o600)
        self.assertFalse(pilot.directory(self.root).exists())
        self.assertLessEqual(set(self.calls),{'getinfo','listpeerchannels','listfunds','decode'})
    def test_failed_replacement_preserves_previous_credentials(self):
        self.configure(); before=(self.root/setup.FILE).read_bytes();self.broken='xbt'
        with self.assertRaises(ValueError):self.configure()
        self.assertEqual(before,(self.root/setup.FILE).read_bytes())
    def test_pairing_change_and_restore_invalidate(self):
        self.configure();self.config['generation']='new'
        with self.assertRaisesRegex(ValueError,'inspection_setup_changed'):self.inspect()
        self.configure();save(self.root/'execution/forward-pilot-restore-epoch.json',{'epoch':'new'})
        with self.assertRaisesRegex(ValueError,'inspection_setup_changed'):self.inspect()
    def test_backup_blocks_setup(self):
        save(self.root/'execution/backup-paused.json',{})
        with self.assertRaisesRegex(ValueError,'backup_in_progress'):self.configure()
        self.assertFalse((self.root/setup.FILE).exists())
    def test_only_invoice_recipient_channel_selected(self):
        self.configure();wrong=copy.deepcopy(self.rows['xbt'][0]);wrong.update(short_channel_id='2x2x0',peer_id='other')
        self.rows['xbt'].append(wrong)
        self.assertEqual(self.inspect()['outgoing_channels'],['1x1x0'])
    def test_disconnected_or_pending_channels_excluded(self):
        self.configure();self.rows['btc'][0]['peer_connected']=False
        self.assertEqual(self.inspect()['incoming_channels'],[])
        self.rows['xbt'][0]['htlcs']=[{}]
        self.assertEqual(self.inspect()['outgoing_channels'],[])
    def test_ambiguous_incoming_never_guessed(self):
        self.configure();other=copy.deepcopy(self.rows['btc'][0]);other['short_channel_id']='2x2x0';self.rows['btc'].append(other)
        req=dict(invoice='lnxbt',incomingChannel=None,outgoingChannel=None)
        with self.assertRaisesRegex(ValueError,'choose_incoming_channels'):
            setup.prepare(self.root,req,factory=self.factory)
        self.assertFalse(pilot.directory(self.root).exists())
    def test_auto_selection_delegates_to_existing_contract_checks(self):
        self.configure()
        with patch.object(pilot,'prepare',return_value={'approval_required':True}) as prepare:
            setup.prepare(self.root,dict(invoice='lnxbt',incomingChannel=None,outgoingChannel=''),factory=self.factory)
            args=prepare.call_args.args[1]
            self.assertEqual(args['incomingChannel'],'1x1x0');self.assertEqual(args['outgoingChannel'],'1x1x0')
            self.assertEqual(args['btcRune'],'btc-private')
            bound=prepare.call_args.kwargs['factory']
            self.config['generation']='changed'
            with self.assertRaises(ValueError):bound(self.config['nodes']['btc'],'btc-private')
    def test_settled_pilot_is_never_replaced(self):
        pilot.directory(self.root).mkdir();save(pilot.directory(self.root)/'record.json',{'phase':'settled'})
        before=(pilot.directory(self.root)/'record.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'one_pilot_only'):
            setup.prepare(self.root,dict(invoice='lnxbt',incomingChannel=None,outgoingChannel=None),factory=self.factory)
        self.assertEqual(self.calls,[])
        self.assertEqual(before,(pilot.directory(self.root)/'record.json').read_bytes())

if __name__=='__main__':unittest.main()
