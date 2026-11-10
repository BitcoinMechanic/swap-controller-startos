import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
import service
import server as api

KEY='a'*64
PROVIDER='02'+'b'*64
INVOICE='lnbc'+'c'*150
CID='d'*64

class Fake:
    def __init__(self):
        self.record=dict(schema=2,environment='mainnet',selected={'forward':'generation'},pairings={'generation':{'nodes':{'outgoing':{'grant':{'chain':{'node_id':PROVIDER}}}}}},jobs={})
        self.calls=[]
    def guard(self):pass
    def clock(self):return 1800000000
    def prepare(self,direction,invoice):
        self.calls.append(('prepare',direction,invoice))
        c=dict(direction='forward',outgoing=dict(invoice=invoice,chain=dict(node_id=PROVIDER),payment_hash='e'*64,amount_msat=500000000),incoming=dict(amount_msat=2013000),pay_until=1900000000)
        self.record['jobs'][CID]={'worker':{'swaps':{CID:{'contract':c}}}}
        return {'confirmation':CID}
    def confirm(self,cid,confirmation,confirmed):
        self.calls.append(('confirm',cid));self.record['jobs'][cid]['worker']['swaps'][cid]['approval']=True
    def cached(self,cid):return dict(state='setting_up',terminal=False,blocked=False)

class Tests(unittest.TestCase):
    def request(self,action='create',**updates):return dict(action=action,invoice=INVOICE,environment='mainnet',provider=PROVIDER,**updates)
    def test_retries_preserve_original_swap_and_never_pay(self):
        s=Fake();first=api.receive(s,self.request());second=api.receive(s,self.request())
        self.assertEqual(first,second);self.assertEqual([x[0] for x in s.calls],['prepare','confirm'])
        self.assertEqual(first['payment_hash'],'e'*64);self.assertNotIn('invoice',first)
        s.record['jobs'][CID]['worker']['swaps'][CID]['invoice']={'invoice':'BTC_INVOICE','preimage':'NEVER_EXPORT'}
        result=api.receive(s,self.request('status'))
        self.assertEqual(result['invoice'],'BTC_INVOICE');self.assertNotIn('NEVER_EXPORT',json.dumps(result))
    def test_lost_prepare_reply_resumes_confirmation_only(self):
        s=Fake();s.prepare('forward',INVOICE);api.receive(s,self.request())
        self.assertEqual([x[0] for x in s.calls],['prepare','confirm'])
    def test_status_does_not_create_or_confirm(self):
        s=Fake()
        with self.assertRaisesRegex(ValueError,'wallet_receive_not_found'):api.receive(s,self.request('status'))
        self.assertEqual(s.calls,[])
        s.prepare('forward',INVOICE);api.receive(s,self.request('status'))
        self.assertEqual([x[0] for x in s.calls],['prepare'])
    def test_wrong_provider_network_and_admin_calls_rejected_before_write(self):
        for key,value in [('provider','02'+'f'*64),('environment','regtest'),('action','configure')]:
            s=Fake();r=self.request();r[key]=value
            with self.assertRaises(ValueError):api.receive(s,r)
            self.assertEqual(s.calls,[])
        with self.assertRaises(ValueError):api.receive(Fake(),self.request(token='secret'))
    def test_auth_and_http_boundary(self):
        class Journal:
            def close(self):pass
        with tempfile.TemporaryDirectory() as root:
            config=Path(root)/'access.json';config.write_text(json.dumps(dict(enabled=True,token=KEY)))
            instance=Fake();http=api.server(('127.0.0.1',0),root,config,lambda j:instance)
            thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            url='http://127.0.0.1:'+str(http.server_port)+'/v1/receive'
            try:
                def req(token):return urllib.request.Request(url,json.dumps(self.request()).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req('bad'))
                self.assertEqual(caught.exception.code,401);self.assertEqual(instance.calls,[])
                with patch('service_cli.action_journal',return_value=Journal()):
                    result=json.load(urllib.request.urlopen(req(KEY)))
                self.assertEqual(result['result']['id'],CID)
                config.write_text(json.dumps(dict(enabled=False,token=KEY)))
                with self.assertRaises(urllib.error.HTTPError):urllib.request.urlopen(req(KEY))
            finally:http.shutdown();http.server_close();thread.join()

if __name__=='__main__':unittest.main()
