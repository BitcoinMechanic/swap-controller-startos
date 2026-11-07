"""Real TLS framing, credential privacy and malformed HTTP request rejection."""
import http.client
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest

import provider_quote as protocol
import provider_server


class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); p=Path(cls.tmp.name)
        cls.cert=p/'cert.pem';cls.key=p/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(cls.key),
                        '-out',str(cls.cert),'-days','1','-subj','/CN=localhost',
                        '-addext','subjectAltName=DNS:localhost'],check=True,capture_output=True)
    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()
    def setUp(self):
        self.calls=[]
        def dispatch(op,body): self.calls.append((op,body));return dict(state='pending')
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.minimum_version=ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(self.cert,self.key)
        self.server=provider_server.server(('127.0.0.1',0),dispatch,ctx)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.context=ssl.create_default_context(cafile=str(self.cert))
        self.origin='https://localhost:'+str(self.server.server_port)
        self.addCleanup(self.close)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join()
    def raw(self,path,body,headers=None,method='POST'):
        c=http.client.HTTPSConnection('localhost',self.server.server_port,context=self.context,timeout=3)
        try:
            c.request(method,path,body=body,headers=headers or {'Content-Type':'application/json'})
            r=c.getresponse();return r.status,r.read(),dict(r.headers)
        finally:c.close()
    def test_tls_transport_and_no_store(self):
        self.assertEqual(protocol.request(self.origin,'status',{'token':'private'},context=self.context),{'state':'pending'})
        self.assertEqual(self.calls,[('status',{'token':'private'})])
        status,_,headers=self.raw('/v1/merchant/status','{}')
        self.assertEqual(status,200);self.assertEqual(headers['Cache-Control'],'no-store')
        with self.assertRaises(ValueError):protocol.request(self.origin,'status',{})
        with self.assertRaises(ValueError):protocol.request(self.origin,'status',{},context=ssl._create_unverified_context())
    def test_unknown_methods_paths_duplicate_keys_nan_and_oversized_requests_refused(self):
        for path,body in [('/v1/merchant/confirm','{}'),('/v1/merchant/quote?token=secret','{}'),
                          ('/v1/merchant/quote','{"token":1,"token":2}'),('/v1/merchant/quote','{"x":NaN}'),
                          ('/v1/merchant/quote','x'*(protocol.LIMIT+1))]:
            status,data,_=self.raw(path,body)
            self.assertNotEqual(status,200); self.assertNotIn(b'secret',data)
        self.assertEqual(self.calls,[])
        self.assertNotEqual(self.raw('/v1/merchant/status','',method='GET')[0],200)
    def test_chunked_and_duplicate_content_lengths_refused(self):
        c=http.client.HTTPSConnection('localhost',self.server.server_port,context=self.context,timeout=3)
        try:
            c.putrequest('POST','/v1/merchant/quote');c.putheader('Content-Type','application/json')
            c.putheader('Content-Length','2');c.putheader('Content-Length','2');c.endheaders(b'{}')
            r=c.getresponse();self.assertEqual(r.status,400);r.read()
        finally:c.close()
        self.assertEqual(self.raw('/v1/merchant/quote','{}',{'Content-Type':'application/json','Transfer-Encoding':'chunked'})[0],400)
        self.assertEqual(self.calls,[])


if __name__=='__main__':unittest.main()
