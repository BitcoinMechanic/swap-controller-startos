import copy
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'assets'))
from controller import pair, step, status, validate, private_load, save, locked, Refused
from read_only_rpc import Client, ProbeError

class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.certs=tempfile.TemporaryDirectory();d=Path(cls.certs.name)
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(d/'key'),'-out',str(d/'cert'),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],check=True,capture_output=True)
        cls.pem=(d/'cert').read_text()
    @classmethod
    def tearDownClass(cls):cls.certs.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.nodes={r:dict(url='https://'+r+'.invalid',node_id=prefix+'12'*32,rune='private-'+r,ca_pem=self.pem) for r,prefix in [('btc','02'),('xbt','03')]}
        self.calls=[]
        def factory(url,rune,ca_data):
            self.calls.append((url,rune))
            class Probe:
                def inspect(s,node_id,network):return dict(identity_matches=True,network=network,warning_present=False,normal_channels=0,pending_htlcs=0)
            return Probe()
        self.factory=factory
    def pair(self,**kw):return pair(self.root,self.nodes,True,factory=self.factory,**kw)
    def test_pair_repeat_and_private_filtered_status(self):
        self.assertTrue(self.pair()['ready']);before=private_load(self.root/'pairing.json')
        self.pair();self.assertEqual(before,private_load(self.root/'pairing.json'))
        self.assertEqual((self.root/'pairing.json').stat().st_mode&0o777,0o600)
        result=json.dumps(status(self.root));self.assertNotIn('private-btc',result);self.assertNotIn('invalid',result)
    def test_confirmation_and_invalid_url_before_probes(self):
        with self.assertRaises(Refused):pair(self.root,self.nodes,False,factory=self.factory)
        self.nodes['btc']['url']='http://btc.invalid'
        with self.assertRaises(ProbeError):self.pair()
        self.assertEqual(self.calls,[])
    def test_changed_pair_requires_confirmation(self):
        self.pair();self.nodes['btc']['rune']='changed'
        with self.assertRaises(Refused):self.pair()
        self.pair(replace=True)
        self.assertEqual(private_load(self.root/'pairing.json')['nodes']['btc']['rune'],'changed')
    def test_failed_replacement_preserves_original(self):
        self.pair();before=(self.root/'pairing.json').read_bytes();self.nodes['btc']['rune']='changed'
        def failure(*a,**k):raise RuntimeError('private secret')
        with self.assertRaises(Refused):pair(self.root,self.nodes,True,True,failure)
        self.assertEqual(before,(self.root/'pairing.json').read_bytes())
    def test_stale_and_future_snapshots_not_ready(self):
        self.pair();snap=private_load(self.root/'status.json')
        self.assertFalse(status(self.root,now=snap['checked_at']+121)['ready'])
        self.assertFalse(status(self.root,now=snap['checked_at']-1)['ready'])
    def test_wrong_generation_not_ready(self):
        self.pair();snap=private_load(self.root/'status.json');snap['generation']='bad';save(self.root/'status.json',snap)
        self.assertFalse(status(self.root)['ready'])
    def test_revoked_or_offline_probe_replaces_green(self):
        self.pair()
        def failure(*a,**k):raise ProbeError('http_request_rejected')
        step(self.root,failure);self.assertFalse(status(self.root)['ready'])
    def test_duplicate_identity_bad_ca_and_private_key_refused(self):
        for field,value in [('node_id',self.nodes['btc']['node_id']),('ca_pem','bad'),('ca_pem',self.pem+'PRIVATE KEY')]:
            nodes=copy.deepcopy(self.nodes);nodes['xbt'][field]=value
            with self.assertRaises(Refused):validate(nodes)
    def test_lock_blocks_concurrent_pair(self):
        with locked(self.root),self.assertRaises(BlockingIOError):self.pair()
        self.assertEqual(self.calls,[])
    def test_symlink_and_world_readable_records_refused(self):
        self.pair();(self.root/'pairing.json').chmod(0o644)
        with self.assertRaises(Refused):status(self.root)
        (self.root/'pairing.json').unlink();(self.root/'pairing.json').symlink_to(self.root/'status.json')
        with self.assertRaises(OSError):status(self.root)
    def test_unpaired(self):self.assertEqual(status(self.root)['reason'],'pair_nodes_first')
    def test_real_https_pairing_and_wrong_identity(self):
        observed=[]
        def serve(role):
            node=self.nodes[role]
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*a):pass
                def do_POST(self):
                    observed.append(self.path)
                    self.rfile.read(int(self.headers.get('Content-Length',0)))
                    if self.headers.get('Rune')!=node['rune']:
                        self.send_response(401);self.end_headers();self.wfile.write(b'{}');return
                    value={'id':node['node_id'],'network':'bitcoin' if role=='btc' else 'xbt'} if self.path=='/v1/getinfo' else {'channels':[]}
                    self.send_response(201);self.end_headers();self.wfile.write(json.dumps(value).encode())
            server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);d=Path(self.certs.name);context.load_cert_chain(d/'cert',d/'key')
            server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            node['url']='https://127.0.0.1:'+str(server.server_port)
        serve('btc');serve('xbt')
        report=pair(self.root,self.nodes,True)
        self.assertTrue(report['ready']);self.assertEqual(set(observed),{'/v1/getinfo','/v1/listpeerchannels'})
        n=self.nodes['btc']
        with self.assertRaises(ProbeError):Client(n['url'],n['rune']).call('getinfo')
        with self.assertRaises(ProbeError):Client(n['url'],'invalid',ca_data=self.pem).call('getinfo')
        count=len(observed)
        with self.assertRaises(ProbeError):Client(n['url'],n['rune'],ca_data=self.pem).inspect('02'+'ff'*32,'bitcoin')
        self.assertEqual(len(observed)-count,1)
        with self.assertRaises(ProbeError):Client(n['url'],n['rune'],ca_data=self.pem).call('pay')

if __name__=='__main__':unittest.main()
