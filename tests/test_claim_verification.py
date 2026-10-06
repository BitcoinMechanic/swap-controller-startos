import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'assets'),'/app']
import claim_verification as v
from controller import save,private_load


def vector(b):
    assert len(b)<253
    return bytes([len(b)])+b


def rawtx(txid,index,outputs,witness=(),sequence=0xffffffff):
    body=b'\x01'+bytes.fromhex(txid)[::-1]+index.to_bytes(4,'little')+b'\x00'+sequence.to_bytes(4,'little')
    body+=bytes([len(outputs)])+b''.join((amount//1000).to_bytes(8,'little')+vector(script) for amount,script in outputs)
    version=b'\x02\x00\x00\x00';lock=b'\x00'*4
    raw=version+(b'\x00\x01' if witness else b'')+body
    if witness:raw+=bytes([len(witness)])+b''.join(vector(w) for w in witness)
    raw+=lock
    return dict(hash=v.sha(v.sha(version+body+lock))[::-1].hex(),rawtx=raw.hex())


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.preimage=b'\xab'*32
        self.spec=dict(direction='forward',node_ids=dict(btc='02'+'a'*64,xbt='03'+'b'*64),
            channel=dict(channel_id='c'*64,funding_txid='d'*64,funding_outnum=0,peer_id='02'+'e'*64,short_channel_id='1x1x1'),
            htlc_id=7,payment_hash=v.sha(self.preimage).hex(),expiry=300,incoming_amount_msat=100000000,
            outgoing_amount_msat=200000000,groupid=1,partid=0)
        # Synthetic witnesses exercise local structural checks. Signatures and
        # confirmations remain trusted node evidence, not script validation.
        self.htlc=b'\xa9\x14'+hashlib.new('ripemd160',bytes.fromhex(self.spec['payment_hash'])).digest()+b'\x87'
        self.delayed=b'\x63\x21'+b'\x02'+b'\x11'*32+b'\x67\x55\xb2\x75\x21'+b'\x03'+b'\x22'*32+b'\x68\xac'
        self.commit=rawtx('d'*64,0,[(100000000,b'\x00\x20'+v.sha(self.htlc))]);self.commit['blockheight']=100
        self.success=rawtx(self.commit['hash'],0,[(99000000,b'\x00\x20'+v.sha(self.delayed))],
            [b'',b'sig1',b'sig2',self.preimage,self.htlc]);self.success['blockheight']=101
        self.sweep=rawtx(self.success['hash'],0,[(98000000,b'\x00\x14'+b'\x33'*20)],
            [b'sig',b'',self.delayed],5);self.sweep['blockheight']=106
        self.rows=[self.commit,self.success,self.sweep]
        self.funds=[dict(txid=self.sweep['hash'],output=0,amount_msat=98000000,scriptpubkey=(b'\x00\x14'+b'\x33'*20).hex(),status='confirmed',blockheight=106)]
    def prove(self):return v.prove(self.spec,self.rows,self.funds,110)
    def test_complete_chain_and_pending_stages(self):
        self.assertTrue(self.prove()['verified'])
        self.assertEqual(self.prove()['proof']['csv_delay'],5)
        for count,phase in ((0,'awaiting_commitment'),(1,'awaiting_htlc_success'),(2,'awaiting_csv_sweep')):
            self.assertEqual(v.prove(self.spec,self.rows[:count],self.funds,110)['phase'],phase)
    def test_claim_reconfirmation_restarts_csv(self):
        self.success['blockheight']=0;self.sweep['blockheight']=0
        self.assertEqual(self.prove()['phase'],'awaiting_htlc_success')
        self.success['blockheight']=104
        self.assertEqual(self.prove()['phase'],'awaiting_csv_sweep')
        self.sweep['blockheight']=106
        with self.assertRaisesRegex(ValueError,'premature_csv_sweep'):self.prove()
        self.sweep['blockheight']=109;self.funds[0]['blockheight']=109
        result=self.prove()
        self.assertTrue(result['verified']);self.assertEqual(result['proof']['heights'],[100,104,109])

    def test_commitment_rollback_and_ordered_reconfirmation(self):
        for tx in self.rows:tx['blockheight']=0
        self.assertEqual(self.prove()['phase'],'awaiting_commitment')
        self.commit['blockheight']=103
        self.assertEqual(self.prove()['phase'],'awaiting_htlc_success')
        self.success['blockheight']=104
        self.assertEqual(self.prove()['phase'],'awaiting_csv_sweep')
        self.sweep['blockheight']=109;self.funds[0]['blockheight']=109
        result=self.prove()
        self.assertTrue(result['verified']);self.assertEqual(result['proof']['heights'],[103,104,109])
        self.assertEqual(result['proof']['commitment'],self.commit['hash'])

    def test_unconfirmed_sweep_never_verifies(self):
        self.sweep['blockheight']=0;self.assertFalse(self.prove()['verified'])
    def test_other_funding_not_claimed(self):
        self.spec['channel']['funding_txid']='e'*64;self.assertFalse(self.prove()['verified'])
    def test_wrong_hash_or_expired_claim_refused(self):
        self.spec['payment_hash']='a'*64
        with self.assertRaises(ValueError):self.prove()
        self.spec['payment_hash']=v.sha(self.preimage).hex();self.spec['expiry']=101
        with self.assertRaises(ValueError):self.prove()
    def test_early_sweep_and_wrong_wallet_evidence_refused(self):
        self.sweep['blockheight']=105
        with self.assertRaises(ValueError):self.prove()
        self.sweep['blockheight']=106;self.funds[0]['amount_msat']-=1000
        with self.assertRaises(ValueError):self.prove()
        self.funds=[]
        with self.assertRaises(ValueError):self.prove()
    def test_raw_hash_mismatch_and_duplicate_refused(self):
        self.success['hash']='f'*64
        with self.assertRaises(ValueError):self.prove()
        self.success['hash']=v.transaction(self.success['rawtx'])['txid'];self.rows.append(self.success)
        with self.assertRaises(ValueError):self.prove()
    def test_parser_truncation_trailing_and_noncanonical_refused(self):
        for raw in (self.success['rawtx'][:-2],self.success['rawtx']+'00','02000000fd0100'+'00'*60):
            with self.assertRaises(ValueError):v.transaction(raw)
    def test_csv_template_and_sequence_refused(self):
        with self.assertRaises(ValueError):v.csv_delay(self.delayed+b'\x00')
        for seq in (4,1<<31,1<<22):
            t=rawtx(self.success['hash'],0,[(98000000,b'\x00\x14'+b'\x33'*20)],[b'sig',b'',self.delayed],seq)
            t['blockheight']=106
            with self.assertRaises(ValueError):v.prove(self.spec,self.rows[:2]+[t],self.funds,110)
    def test_rpc_no_write_methods_or_live_network(self):
        client=v.ChainReader.__new__(v.ChainReader);client.node_id='02'+'a'*64;client.network='regtest'
        with patch.object(client,'_request') as request:
            for method in ('close','sendpay','pay','withdraw','xbt-release','reverse-release','xbt-fail','createrune','invalidateblock','reconsiderblock','generatetoaddress'):
                with self.assertRaises(ValueError):client.call(method)
            request.assert_not_called()
        with self.assertRaises(ValueError):v.ChainReader({'network':'bitcoin'})
    def test_inspection_both_directions_rechecks_without_mutations(self):
        for direction in ('forward','reverse'):
            self.spec['direction']=direction
            with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'BTC_XBT_DISPOSABLE_CONTAINER':'1'}):
                root=Path(tmp);save(root/'deadline.json',dict(source_commit=v.PIN,spec=self.spec))
                save(root/'claim-receipt.json',dict(spec_digest=v.digest(self.spec),stage='gate_resolved',attempt=dict(id=9,groupid=1,partid=0)))
                original={p.name:p.read_bytes() for p in root.iterdir()}
                owner=self;calls=[]
                class Fake:
                    def __init__(self,role):self.role=role
                    def call(self,method,*args):
                        calls.append(method)
                        if method=='getinfo':return dict(id=owner.spec['node_ids'][self.role],network='regtest' if self.role=='btc' else 'xbt-regtest',blockheight=110)
                        if method=='listtransactions':return dict(transactions=owner.rows)
                        if method=='listfunds':return dict(outputs=owner.funds)
                        if method=='listsendpays':return dict(payments=[dict(id=9,groupid=1,payment_hash=owner.spec['payment_hash'],amount_sent_msat=200000000,status='complete')])
                        raise AssertionError(method)
                clients={r:Fake(r) for r in ('btc','xbt')}
                self.assertTrue(v.inspect(root,self.spec,clients)['verified'])
                report=(root/'chain-verification.json').read_bytes()
                self.assertTrue(v.inspect(root,self.spec,clients)['verified'])
                self.assertEqual(report,(root/'chain-verification.json').read_bytes())
                self.assertNotIn(self.preimage.hex(),report.decode())
                self.assertEqual(original,{name:(root/name).read_bytes() for name in original})
                # Prior success is not reused after a reported confirmation loss.
                self.sweep['blockheight']=0
                self.assertFalse(v.inspect(root,self.spec,clients)['verified'])
                pending=private_load(root/'chain-verification.json')
                self.assertFalse(pending['verified']);self.assertNotIn('proof',pending)
                self.assertFalse(v.inspect(root,self.spec,clients)['verified'])
                self.sweep['blockheight']=106
                self.assertTrue(v.inspect(root,self.spec,clients)['verified'])
                self.assertEqual(report,(root/'chain-verification.json').read_bytes())
                self.assertEqual(original,{name:(root/name).read_bytes() for name in original})
                self.assertEqual(set(calls),{'getinfo','listtransactions','listfunds','listsendpays'})

if __name__=='__main__':unittest.main()
