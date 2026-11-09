import http.client
import json
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest
import server

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name)/'provider.json'
        self.info = dict(name='Example', provider='02'+'ab'*32, environment='mainnet', endpoints=['https://example.com'])
        self.config.write_text(json.dumps(self.info))
    def serve(self, context=None):
        srv=server.server(('127.0.0.1',0),self.config,context)
        thread=threading.Thread(target=srv.serve_forever);thread.start()
        def close(): srv.shutdown();srv.server_close();thread.join()
        self.addCleanup(close)
        return srv.server_port
    def request(self, port, path, method='GET', context=None):
        c=(http.client.HTTPSConnection('localhost',port,context=context,timeout=3) if context else http.client.HTTPConnection('127.0.0.1',port,timeout=3))
        try:
            c.request(method,path);r=c.getresponse();return r.status,r.read()
        finally:c.close()
    def test_allowlist_and_public_metadata_only(self):
        port=self.serve()
        status,body=self.request(port,'/v1/provider/info');self.assertEqual(status,200)
        value=json.loads(body);self.assertFalse(value['payment_available']);self.assertEqual(value['capabilities'],['discovery'])
        status,body=self.request(port,'/v1/providers');self.assertEqual(status,200);self.assertEqual(len(json.loads(body)['providers']),1)
        for path in ['/v1/merchant/accept','/../provider.json','/v1/provider/info?token=secret']:
            status,body=self.request(port,path);self.assertEqual(status,404);self.assertNotIn(b'secret',body)
        self.assertNotEqual(self.request(port,'/v1/merchant/accept','POST')[0],200)
        self.info['rune']='PRIVATE';self.config.write_text(json.dumps(self.info))
        status,body=self.request(port,'/v1/provider/info');self.assertEqual(status,503);self.assertNotIn(b'PRIVATE',body)
    def test_disabled_missing_and_invalid_origins(self):
        port=self.serve();self.config.unlink()
        self.assertEqual(self.request(port,'/v1/provider/info')[0],503)
        for endpoint in ['http://example.com','https://a:b@example.com','https://example.com?token=secret','https://example.com/path']:
            with self.assertRaises(ValueError):server.endpoint(endpoint)
        onion='http://'+'a'*56+'.onion';self.assertEqual(server.endpoint(onion),onion)
    def test_verified_https(self):
        cert=Path(self.tmp.name)/'cert';key=Path(self.tmp.name)/'key'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost'],check=True,capture_output=True)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key);port=self.serve(ctx)
        trusted=ssl.create_default_context(cafile=str(cert))
        self.assertEqual(self.request(port,'/v1/provider/info',context=trusted)[0],200)
        with self.assertRaises(ssl.SSLError):self.request(port,'/v1/provider/info',context=ssl.create_default_context())

if __name__=='__main__': unittest.main()
