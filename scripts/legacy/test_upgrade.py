import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import validate_upgrade as v
import pilot_contract as forward
import reverse_contract as reverse


def save(root, path, value):
    p = root/path
    p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    p.write_text(json.dumps(value));p.chmod(0o600)


def fixture(root, *, direction='forward', phase='settled', index=1, barrier=True):
    root.mkdir(exist_ok=True,mode=0o700)
    (root/'execution/jobs').mkdir(parents=True,exist_ok=True,mode=0o700)
    (root/'execution/lifecycle.lock').touch(mode=0o600)
    nodes={'btc':'02'+'1'*64,'xbt':'03'+'2'*64}
    save(root,'pairing.json',dict(schema=1,generation='a'*32,nodes={k:dict(node_id=n,
        url='PRIVATE_URL',rune='PRIVATE_RUNE',ca_pem='PRIVATE_CA') for k,n in nodes.items()}))
    if barrier:save(root,'execution/restored.json',{'blocked':True})
    module=reverse if direction=='reverse' else forward
    pin=dict(channel_id='3'*64,short_channel_id='1x2x0',funding_txid='4'*64,funding_outnum=0,peer_id='02'+'5'*64)
    channels={'btc':pin,'xbt':dict(pin,channel_id='7'*64,short_channel_id='1x2x1')}
    incoming='btc' if direction=='forward' else 'xbt'
    c=dict(profile=forward.PROFILE if direction=='forward' else reverse.ROUTED,source_commit=module.PIN,
        nonce=f'{index:064x}',nodes=nodes,channels=channels,invoice=('lnxbt' if direction=='forward' else 'lnbc')+'PRIVATE',
        payment_hash=f'{index+100:064x}',payment_secret='9'*64,recipient=pin['peer_id'],created_at=1000,admission_until=2800)
    if direction=='reverse':c.update(incoming_channels=[channels['xbt']],route=[dict(id=c['recipient'],channel=pin['short_channel_id'],amount_msat=1500000,delay=40)])
    module.validate(c)
    cid=module.digest(c);folder='execution/'+direction+'-swaps/'+cid
    r=dict(schema=1,pilot_id=cid,contract=c,generation='a'*32,phase=phase,restore_epoch=None)
    if phase!='cancelled':
        amounts=v.mt.amounts(c)
        r.update(invoice=('lnbc' if direction=='forward' else 'lnxbt')+'PRIVATE',terms=dict(payment_hash=c['payment_hash'],
            payment_secret='b'*64,expires_at=2800,**{k+'_amount_msat':value for k,value in amounts.items()},
            **{('xbt' if direction=='forward' else 'btc')+'_invoice':c['invoice']}))
        save(root,folder+'/credentials.json',{'btc':'PRIVATE_BTC','xbt':'PRIVATE_XBT'})
    if phase in ('settled','failed'):
        r.update(binding=[channels[incoming]['short_channel_id'],1],expiry=1400)
        if module.routed(c):r['incoming_pin']=channels[incoming]
    save(root,folder+'/record.json',r)
    return folder,r


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.folder,self.record=fixture(self.root)

    def tearDown(self):self.tmp.cleanup()
    def change(self,**kwargs):save(self.root,self.folder+'/record.json',dict(self.record,**kwargs))
    def refused(self):
        with self.assertRaises(Exception):v.validate(self.root)

    def test_live_shape_preserves_bytes_and_no_network(self):
        for index in range(2,7):fixture(self.root,index=index)
        for index in range(7,11):fixture(self.root,index=index,phase='expired')
        fixture(self.root,index=11,phase='cancelled')
        for index in range(12,14):fixture(self.root,index=index,direction='reverse')
        before={str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('socket.socket',side_effect=AssertionError('network forbidden')):r=v.validate(self.root)
        self.assertTrue(r['admitted']);self.assertFalse(r['legacy_authority_imported'])
        self.assertEqual(len(r['records']),13);self.assertTrue(r['legacy_execution_barrier_preserved'])
        self.assertNotIn('PRIVATE',json.dumps(r));self.assertFalse(r['settlement_proof_reverified'])
        self.assertEqual(before,{str(p):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_pending_unknown_onchain_refused(self):
        for phase in ('send_intent','review','authorizing','onchain_recovery','UNKNOWN',None):
            self.change(phase=phase);self.refused()

    def test_restore_marker_never_waived(self):
        for path in ('swap-restored.json','node-restored.json','restored.json','execution/jobs/old/restored.json'):
            save(self.root,path,{'blocked':True});self.refused();(self.root/path).unlink()

    def test_invalid_legacy_barrier(self):
        for val in ({'blocked':False},{'blocked':1},{'blocked':True,'extra':1}):
            save(self.root,'execution/restored.json',val);self.refused()

    def test_matching_epoch_and_old_epoch(self):
        marker={'epoch':'e'*64};save(self.root,'execution/forward-pilot-restore-epoch.json',marker)
        self.refused();self.change(restore_epoch=forward.digest(marker));self.assertTrue(v.validate(self.root)['admitted'])
        self.change(restore_epoch='f'*64);self.refused()

    def test_new_state_and_regtest_refused(self):
        for name in ('mainnet-swaps-v1','migrated-swaps-v1'):
            (self.root/name).mkdir();self.refused();(self.root/name).rmdir()

    def test_history_ignores_active_new_runtime_bytes(self):
        save(self.root,'mainnet-swaps-v1/state.json',{'private':'PRIVATE_NEW_STATE'})
        r=v.validate(self.root,'history');self.assertFalse(r['admitted']);self.assertEqual(len(r['records']),1)
        self.assertNotIn('PRIVATE',json.dumps(r))

    def test_unknown_state_directory_and_record_field(self):
        (self.root/'execution/unknown').mkdir();self.refused();(self.root/'execution/unknown').rmdir()
        self.change(send=True);self.refused()

    def test_changed_contract_and_pairing(self):
        c=copy.deepcopy(self.record['contract']);c['nonce']='0'*64
        self.change(contract=c);self.refused();self.change(generation='b'*32);self.refused()

    def test_binding_and_funding(self):
        for binding in (['2x2x2',1],['1x2x0',True],['1x2x0',-1],None):
            self.change(binding=binding);self.refused()

    def test_publication_integrity(self):
        for key,value in (('payment_hash','f'*64),('btc_amount_msat',42),('xbt_invoice','PRIVATE_OTHER'),('expires_at',999)):
            terms=dict(self.record['terms'],**{key:value});self.change(terms=terms);self.refused()

    def test_cancelled_with_credentials(self):
        self.change(phase='cancelled');self.refused()

    def test_expired_with_binding_and_close(self):
        self.change(phase='expired');self.refused();self.change(close_intent=False);self.refused()

    def test_missing_record_unknown_entry(self):
        (self.root/'execution/forward-swaps/unrecognized').mkdir();self.refused()

    def test_jobs_and_backup(self):
        save(self.root,'execution/jobs/job/state.json',{});self.refused()
        (self.root/'execution/jobs/job/state.json').unlink();(self.root/'execution/jobs/job').rmdir()
        save(self.root,'execution/backup-paused.json',{'paused':True});self.refused()

    def test_symlink_hardlink_fifo(self):
        p=self.root/'unsafe'
        for make in (lambda:p.symlink_to(self.root/'pairing.json'),lambda:os.link(self.root/'pairing.json',p),lambda:os.mkfifo(p)):
            make();self.refused();p.unlink()

    def test_duplicate_json(self):
        (self.root/self.folder/'record.json').write_text('{"phase":"settled","phase":"failed"}')
        self.refused()

    def test_no_barrier_allowed_without_epoch(self):
        (self.root/'execution/restored.json').unlink()
        self.assertFalse(v.validate(self.root)['legacy_execution_barrier_preserved'])

    def test_failed_record_and_reverse_funding(self):
        self.change(phase='failed');self.assertTrue(v.validate(self.root)['admitted'])
        folder,r=fixture(self.root,index=20,direction='reverse')
        r['incoming_pin']=r['contract']['channels']['btc'];save(self.root,folder+'/record.json',r);self.refused()


if __name__=='__main__':unittest.main()
