"""Actual journal/worker integration; synthetic remote nodes, no real funds."""
import unittest
import copy
import server as api
import test_service as fixture

class Tests(unittest.TestCase):
    setUp=fixture.Tests.setUp
    open=fixture.Tests.open
    close=fixture.Tests.close
    live=fixture.Tests.live
    def test_wallet_receive_survives_lost_reply_without_reenrollment_or_send(self):
        self.remote.c['outgoing']['invoice'] += 'a'*100
        self.s.pair(self.bind,confirmed=True);self.live()
        request=dict(action='create',invoice=self.remote.c['outgoing']['invoice'],environment='regtest',provider=self.remote.c['outgoing']['chain']['node_id'])
        first=api.receive(self.s,request);cid=first['id']
        self.remote.c=copy.deepcopy(self.s.record['jobs'][cid]['worker']['swaps'][cid]['contract'])
        self.open();again=api.receive(self.s,request)
        self.assertEqual(first,again);self.assertEqual(len(self.s.record['jobs']),1)
        for _ in range(3):self.s.tick('11'*32);self.open()
        status=api.receive(self.s,dict(request,action='status'))
        self.assertEqual(status['id'],cid);self.assertIn('invoice',status)
        before=copy.deepcopy(self.remote.calls)
        self.assertEqual(api.receive(self.s,request),status)
        self.assertEqual(self.remote.calls,before)
        self.assertFalse(any(op=='send' for _,op,_ in self.remote.calls))
        self.assertEqual(len(self.s.record['jobs']),1)
