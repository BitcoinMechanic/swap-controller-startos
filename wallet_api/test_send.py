import copy
import unittest
import server as api
import test_server
import test_service

class Fake(test_server.Fake):
    def __init__(self):
        super().__init__()
        self.record['selected']['reverse']='generation'
        self.record['pairings']['generation']['nodes']['incoming']=self.record['pairings']['generation']['nodes']['outgoing']
    def prepare(self,direction,invoice):
        result=super().prepare(direction,invoice)
        c=self.record['jobs'][test_server.CID]['worker']['swaps'][test_server.CID]['contract']
        q=test_service.price('reverse',[dict(amount_msat=1000000)],100,clock=lambda:1800000000)
        c.update(direction='reverse',pricing=q)
        c['incoming'].update(chain=dict(node_id=test_server.PROVIDER),amount_msat=q['xbt_msat'])
        c['outgoing']['amount_msat']=q['btc_msat']
        return result
    def cancel(self,cid,confirmation):
        self.calls.append(('cancel',cid))

class Tests(unittest.TestCase):
    def request(self,action='quote',**kw):
        return dict(action=action,invoice=test_server.INVOICE,environment='mainnet',provider=test_server.PROVIDER,**kw)
    def test_quote_does_not_approve_or_pay_and_lost_reply_reuses_quote(self):
        s=Fake();a=api.send(s,self.request());b=api.send(s,self.request())
        self.assertEqual(a,b);self.assertEqual([c[0] for c in s.calls],['prepare'])
        self.assertFalse(a['approved']);self.assertEqual(a['fees']['markup_bps'],100)
        self.assertGreater(a['fees']['provider_fee_msat'],0)
        s.record['markup_bps']=500
        self.assertEqual(api.send(s,self.request()),a)
    def test_accept_binds_exact_saved_quote(self):
        s=Fake();q=api.send(s,self.request())
        with self.assertRaisesRegex(ValueError,'wallet_quote_changed'):
            api.send(s,self.request('accept',id='f'*64))
        self.assertEqual(len(s.calls),1)
        result=api.send(s,self.request('accept',id=q['id']))
        self.assertTrue(result['approved']);self.assertEqual(result['fees'],q['fees'])
        saved=s.record['jobs'][q['id']]['worker']['swaps'][q['id']]
        saved['invoice']={'invoice':'XBT_INVOICE','preimage':'PRIVATE'}
        status=api.send(s,self.request('status'))
        self.assertEqual(status['invoice'],'XBT_INVOICE');self.assertNotIn('PRIVATE',str(status))
    def test_status_never_creates_and_wrong_provider_refuses(self):
        s=Fake()
        with self.assertRaisesRegex(ValueError,'wallet_send_not_found'):api.send(s,self.request('status'))
        req=self.request();req['provider']='02'+'f'*64
        with self.assertRaisesRegex(ValueError,'wallet_provider_changed'):api.send(s,req)
        self.assertEqual(s.calls,[])
    def test_fee_breakdown_zero_markup(self):
        s=Fake();api.send(s,self.request())
        c=s.record['jobs'][test_server.CID]['worker']['swaps'][test_server.CID]['contract']
        c['pricing']=test_service.price('reverse',[dict(amount_msat=1000000)],0,clock=lambda:1800000000)
        self.assertEqual(api.fees(c)['provider_fee_msat'],0)

if __name__=='__main__':unittest.main()
