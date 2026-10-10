"""Exercise the real fixture bridge, workers and plugin subprocesses.

CLN chain/RPC responses and HTTP/Unix transports are simulated by private files.
This environment forbids sockets. Real bridge, adapter, worker, node authority,
invoice encoder and gate code run across fresh processes. This does not validate
TLS, funded Lightning, native daemon restart, or on-chain recovery.
"""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import io
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
REMOTE = ROOT/'tests/remote'
PINNED = Path(os.environ.get('SWAP_FIXTURE_PINNED_SOURCE',str(ROOT.parent/'lightning/tools/blake2b')))
sys.path[:0] = [str(ROOT/'assets'), str(REMOTE), str(ROOT.parent/'btc-cln-startos/assets/swaps'), str(PINNED)]
from controller import save, private_load
from test_reverse_routed_swaps import RoutedRPC, RECIPIENT
from test_reverse_public_prefix import PublicPrefixRPC
from reverse_node import RPCError
from quote_protocol import REVERSE_STAGES, REPEAT_STAGES, validate_job


def launcher():
    spec = importlib.util.spec_from_file_location('fixture_launcher', ROOT/'scripts/test-forward-pilot.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.quote.STAGES = (*module.quote.STAGES, *REPEAT_STAGES, *REVERSE_STAGES)
    return module


def transport(kind, **request):
    folder=Path(os.environ['FIXTURE_TRANSPORT'])
    path=folder/(uuid.uuid4().hex+'.request')
    save(path,dict(kind=kind,**request));reply=path.with_suffix('.response')
    end=time.monotonic()+20
    while not reply.exists():
        if time.monotonic()>end:raise RuntimeError('test_transport_timeout')
        time.sleep(.005)
    result=private_load(reply)
    if 'error' in result:
        if type(result.get('code')) is int:raise RPCError(result['code'])
        raise ValueError(result['error'])
    return result['result']


def adapt_backend():
    import pilot_node
    class Backend:
        def __init__(self,root,network):
            self.role=root.name;self.network=network
        def __call__(self,method,**params):
            assert self.network==('regtest' if self.role=='btc' else 'xbt-regtest')
            return transport('rpc',role=self.role,method=method,params=params)
    pilot_node.LocalRPC=Backend


def subprocess_entry(mode,args):
    if mode in ('--node','--enable'):
        adapt_backend()
        if mode=='--enable':
            from reverse_regtest_node import FixtureSession
            from market_fixture import LIMITS
            limits=LIMITS if os.environ.get('MARKET_FIXTURE')=='1' else None
            print(json.dumps(FixtureSession(Path(args[0]),args[1]).enable('',2,True,routed_grant=True,max_delay=288,market_limits=limits)))
        else:
            import runpy
            sys.argv=[str(REMOTE/'reverse_regtest_node.py'),*args,'plugin']
            runpy.run_path(sys.argv[0],run_name='__main__')
    else:
        import read_only_rpc
        original=read_only_rpc.Client.__init__
        class Opener:
            def open(self,request,timeout):
                value=transport('http',url=request.full_url,rune=request.get_header('Rune'),params=json.loads(request.data))
                return io.BytesIO(json.dumps(value).encode())
        def initialize(self,*args,**kwargs):
            original(self,*args,**kwargs);self.opener=Opener()
        read_only_rpc.Client.__init__=initialize
        from pilot_step import run
        run(args[1],Path(args[0]))


class Plugin:
    def __init__(self, args, env, log):
        self.errors = log.open('a')
        self.proc = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self.errors, text=True, bufsize=1)
        self.replies = queue.Queue(); self.serial = 0
        def read():
            for line in self.proc.stdout:
                if line.strip():self.replies.put(json.loads(line))
        self.reader = threading.Thread(target=read, daemon=True); self.reader.start()
    def call(self, method, params=None, wait=True):
        self.serial += 1; serial = self.serial
        self.proc.stdin.write(json.dumps(dict(jsonrpc='2.0', id=serial, method=method, params=params or {}))+'\n\n')
        self.proc.stdin.flush()
        if not wait:return
        while True:
            try:reply = self.replies.get(timeout=10)
            except queue.Empty:raise AssertionError(Path(self.errors.name).read_text() or 'plugin reply timed out') from None
            if reply.get('id') == serial:
                if 'error' in reply:raise ValueError(reply['error'])
                return reply['result']
    def close(self):
        self.proc.stdin.close()
        try:self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:self.proc.kill(); self.proc.wait()
        self.reader.join(timeout=5); self.proc.stdout.close(); self.errors.close()


class SimulatedCLN(PublicPrefixRPC):
    def __init__(self, role, owner):
        super().__init__(role); self.owner = owner; self.signed = {}; self.last = None
        # One real-looking approved channel per coordinator, with anchor fees
        # and a private remote alias as used by the funded fixture.
        self.ch.update(features=['option_anchors','option_scid_alias'], feerate={'perkw':1255},
                       private=True, alias={'remote':'7x7x7','local':'8x8x8'})
    def __call__(self, method, **params):
        if method == 'getinfo':return dict(id=self.id, network='regtest' if self.role=='btc' else 'xbt-regtest', blockheight=self.height)
        if method == 'listpeerchannels':return dict(channels=[copy.deepcopy(self.ch)])
        if method == 'decode' and params['string'] in self.signed:return copy.deepcopy(self.signed[params['string']])
        if method.startswith('reverse-'):
            self.calls.append((method,copy.deepcopy(params)))
            result = self.owner.gate.call(method,params)
            if method == 'reverse-repeat-register':self.last = params['quote']
            if method in ('reverse-release','reverse-fail'):self.ch['htlcs'] = []
            return result
        if method == 'signinvoice':
            from swap_invoice import CHARSET
            value = params['invstring']; hrp, encoded = value.rsplit('1',1)
            amount=self.last['xbt_amount_msat']
            assert hrp == ('lnxbtrt1m' if amount==100000000 else 'lnxbtrt'+str(amount*10)+'p'), hrp
            words = [CHARSET.index(c) for c in encoded][7:-110]; tags = {}
            while words:
                letter=CHARSET[words[0]]; size=words[1]*32+words[2]
                tags.setdefault(letter,[]).append(words[3:3+size]); words=words[3+size:]
            def integer(words):
                value=0
                for word in words:value=(value<<5)|word
                return value
            def hexbytes(words):return (integer(words) >> (len(words)*5%8)).to_bytes(len(words)*5//8,'big').hex()
            assert hexbytes(tags['p'][0]) == self.last['payment_hash']
            assert hexbytes(tags['s'][0]) == self.last['payment_secret']
            assert tags['r'], 'actual incoming encoder must include route hints'
            self.signed[value] = dict(valid=True,currency='xbtrt',payee=self.id,
                payment_hash=self.last['payment_hash'],payment_secret=self.last['payment_secret'],
                amount_msat=amount,min_final_cltv_expiry=integer(tags['c'][0]))
            return {'bolt11':value}  # Signature verification belongs to funded CLN tests.
        if method in ('reverse-release','reverse-fail'):raise AssertionError('unreachable')
        result = super().__call__(method,**params)
        if method == 'sendpay':
            p=self.payments[params['payment_hash']][0];p.update(id=len(self.payments),amount_msat=1500000)
            self.ch['htlcs']=[dict(id=len(self.payments),direction='out',payment_hash=params['payment_hash'],
                amount_msat=params['route'][0]['amount_msat'],expiry=self.height+params['route'][0]['delay'],
                state='SENT_ADD_ACK_REVOCATION')]
        return result


class FlowTests(unittest.TestCase):
    market=False
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='fixture-flow-');self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old_umask=os.umask(0o077);self.addCleanup(os.umask,self.old_umask)
        self.exchange=self.root/'exchange';self.control=self.exchange/'control';self.jobs=self.exchange/'jobs'
        self.control.mkdir(parents=True);self.jobs.mkdir()
        paths=[ROOT/'assets',REMOTE,ROOT.parent/'btc-cln-startos/assets/swaps',
               ROOT.parent/'xbt-cln-startos/assets/xbt',PINNED]
        self.env=dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1',PYTHONDONTWRITEBYTECODE='1',
                      PYTHONPATH=os.pathsep.join(map(str,paths)),PILOT_SCENARIO='reverse-routed-normal')
        if self.market:
            self.env['MARKET_FIXTURE']='1'
            save(self.control/'market-fixture.json',{'enabled':True})
        self.plugins={};self.errors=[];self.seen=set();self.commands=[]
        self.transport_dir=self.root/'transport';self.transport_dir.mkdir();self.env['FIXTURE_TRANSPORT']=str(self.transport_dir)
        self.transport_stop=threading.Event();self.transport_threads=[]
        self.transport_host=threading.Thread(target=self.dispatch_transport,daemon=True);self.transport_host.start()
        self.rpc={role:SimulatedCLN(role,self) for role in ('btc','xbt')}
        self.node_roots={role:self.root/role for role in self.rpc}
        for role,path in self.node_roots.items():(path/('regtest' if role=='btc' else 'xbt-regtest')).mkdir(parents=True)
        self.addCleanup(self.close)
        self.start_gate()
        cert=self.root/'cert.pem';key=self.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN=localhost','-addext','subjectAltName=IP:127.0.0.1',
            '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        self.config={};self.tokens={}
        for role in self.rpc:
            self.start_node(role)
            env=dict(self.env)
            result=subprocess.run([sys.executable,__file__,'--enable',str(self.node_roots[role]),role],env=env,check=True,capture_output=True,text=True)
            self.tokens[role]=json.loads(result.stdout)['credential']
            self.config[role]=dict(url='https://127.0.0.1:'+('19401' if role=='btc' else '19402'),node_id=self.rpc[role].id,rune='read-'+role,ca_pem=cert.read_text())
        save(self.control/'pairing.json',dict(schema=1,generation='a'*32,nodes=self.config))
        self.launch=launcher()
        # Avoid loading compiled node binaries just to import the actual bridge.
        dummy=types.ModuleType('image_pair');dummy.PIN='81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe';dummy.PairLab=object;dummy.check_bundle=lambda *_:None
        with patch.dict(sys.modules,image_pair=dummy):
            import image_quote
            self.bridge=image_quote.bridge
        self.stop=threading.Event()
        self.host=threading.Thread(target=self.dispatch,daemon=True);self.host.start()
    def close(self):
        if hasattr(self,'stop'):self.stop.set();self.host.join(timeout=10)
        for process in self.plugins.values():process.close()
        if hasattr(self,'gate'):self.gate.close()
        self.transport_stop.set();self.transport_host.join(timeout=5)
        for thread in self.transport_threads:thread.join(timeout=5)
    def start_gate(self):
        self.gate=Plugin([sys.executable,str(REMOTE/'reverse_regtest_gate.py'),str(self.root/'gate.json')],self.env,self.root/'gate.log')
        manifest=self.gate.call('getmanifest');self.assertIn('reverse-repeat-register',[r['name'] for r in manifest['rpcmethods']])
        self.gate.call('init',dict(configuration={'network':'xbt-regtest'}))
    def start_node(self,role):
        self.plugins[role]=Plugin([sys.executable,__file__,'--node',role,str(self.node_roots[role])],self.env,self.root/(role+'.log'))
        self.plugins[role].call('getmanifest')
        self.plugins[role].call('init',dict(configuration={'network':'regtest' if role=='btc' else 'xbt-regtest'}))
    def dispatch_transport(self):
        seen=set()
        def handle(path):
            try:
                msg=private_load(path)
                if msg['kind']=='rpc':result=self.rpc[msg['role']](msg['method'],**msg['params'])
                else:
                    role='btc' if msg['url'].startswith('https://127.0.0.1:19401/') else 'xbt'
                    method=msg['url'].split('/v1/')[1]
                    if method=='swap-reverse-call':
                        assert msg['rune']==json.loads(self.tokens[role])['rune']
                        result=self.plugins[role].call(method,msg['params'])
                    else:
                        assert msg['rune']=='read-'+role and method in ('getinfo','listpeerchannels','listfunds','decode','listsendpays')
                        result=self.rpc[role](method,**msg['params'])
                reply=dict(result=result)
            except Exception as error:
                reply=dict(error=repr(error))
                if isinstance(error,RPCError):reply['code']=error.code
            save(path.with_suffix('.response'),reply)
        while not self.transport_stop.wait(.005):
            for path in self.transport_dir.glob('*.request'):
                if path.name in seen:continue
                seen.add(path.name);thread=threading.Thread(target=handle,args=(path,),daemon=True)
                self.transport_threads.append(thread);thread.start()
    def dispatch(self):
        try:
            while not self.stop.wait(.01):
                for path in sorted(self.jobs.glob('*.request')):
                    if path.name in self.seen:continue
                    job=json.loads(path.read_text());stage=validate_job(job,self.launch.quote.STAGES);self.seen.add(path.name)
                    cmd=self.launch.command(ROOT,self.exchange,'controller-image','isolated-net','fresh-worker',stage)
                    assert cmd[-2:]==['/remote-tests/pilot_step.py',stage]
                    assert '--read-only' in cmd and '--cap-drop=ALL' in cmd
                    assert not any('/controller-assets' in part or 'docker.sock' in part for part in cmd)
                    self.commands.append(stage)
                    result=subprocess.run([sys.executable,__file__,'--worker',str(self.control),stage],env=self.env,text=True,capture_output=True,timeout=20)
                    if result.returncode:self.errors.append(result.stderr)
                    self.launch.base.respond(path.with_suffix('.response'),result)
        except Exception as error:self.errors.append(repr(error))
    def step(self,stage,request=None):
        try:return self.bridge(stage,request,exchange=self.exchange)
        except Exception:raise AssertionError('\n'.join(self.errors)) from None
    def setup_grants(self):
        result=self.step('reverse-setup',dict(inspection=dict(btcRune='read-btc',xbtRune='read-xbt',confirmed=True),
            grants=dict(btcCredential=self.tokens['btc'],xbtCredential=self.tokens['xbt'],confirmed=True)))
        self.assertEqual(result['remaining'],2)
    def flow(self,mode):
        self.setup_grants()
        if mode=='lost-reply':
            self.env['PILOT_SCENARIO']='reverse-routed-lost-reply'
            for role in self.plugins:self.plugins[role].close();self.start_node(role)
        for index in range(2):
            preimage=f'{index+1:064x}';h=hashlib.sha256(bytes.fromhex(preimage)).hexdigest();invoice='lnbcrt-fixture-'+str(index)
            self.rpc['btc'].invoices[invoice]=dict(valid=True,type='bolt11 invoice',currency='bcrt',amount_msat=1500000,
                payment_hash=h,payment_secret='b'*64,payee=RECIPIENT,created_at=int(time.time()),expiry=3600,min_final_cltv_expiry=18)
            self.rpc['btc'].preimages[h]=preimage
            prepared=self.step('reverse-prepare',dict(invoice=invoice));swap=prepared['pilot_id']
            self.assertTrue(prepared['routed']);self.assertEqual(prepared['routing_fee_msat'],2002)
            self.assertEqual(prepared['max_delay_blocks'],288);self.assertEqual(prepared['route_delay_blocks'],200)
            approved=self.step('reverse-approve',dict(pilotId=swap,confirmed=True));self.assertEqual(approved['phase'],'waiting_for_xbt')
            term=self.gate.call('reverse-status',{'payment_hash':h})['terms'];expiry=1000+term['min_cltv_delta']+24
            amount=term['xbt_amount_msat']
            if self.market:
                self.assertNotEqual(amount,3000000)
                self.assertEqual(term['contract']['pricing']['markup_bps'],0)
                self.assertEqual(term['contract']['pricing']['pair'],'BTCB2_BTC')
            hook=dict(htlc=dict(short_channel_id=self.rpc['xbt'].ch['short_channel_id'],id=index+1,payment_hash=h,
                amount_msat=amount,cltv_expiry=expiry,cltv_expiry_relative=expiry-1000),
                onion=dict(type='tlv',payment_secret=term['payment_secret'],forward_msat=amount,total_msat=amount,outgoing_cltv_value=expiry))
            self.gate.call('htlc_accepted',hook,wait=False)
            self.assertEqual(self.gate.call('reverse-status',{'payment_hash':h})['phase'],'held')
            self.rpc['xbt'].ch['htlcs']=[dict(id=index+1,direction='in',payment_hash=h,amount_msat=amount,expiry=expiry,state='RCVD_ADD_ACK_REVOCATION',local_trimmed=False)]
            self.step('reverse-worker');self.step('reverse-worker')
            record=self.control/'execution/reverse-swaps'/swap/'record.json';before=private_load(record)
            self.assertEqual(before['phase'],'send_intent');self.assertIn('outgoing_htlc',before)
            if mode=='restart':
                node_records={role:(self.node_roots[role]/'reverse-swaps'/(swap+'.json')).read_bytes() for role in self.rpc}
                for role in self.plugins:self.plugins[role].close();self.start_node(role)
                self.gate.close();self.start_gate();self.gate.call('htlc_accepted',hook,wait=False)
                self.step('reverse-worker');self.step('reverse-worker')
                for key in ('contract','binding','expiry','incoming_pin','outgoing_htlc'):self.assertEqual(private_load(record)[key],before[key])
                for role in self.rpc:self.assertEqual((self.node_roots[role]/'reverse-swaps'/(swap+'.json')).read_bytes(),node_records[role])
            failed=mode=='failure' and index==1
            p=self.rpc['btc'].payments[h][0];p['status']='failed' if failed else 'complete'
            if not failed:p['payment_preimage']=preimage
            self.rpc['btc'].ch['htlcs']=[]
            self.step('reverse-worker');self.step('reverse-worker')
            self.assertEqual(private_load(record)['phase'],'failed' if failed else 'settled')
            self.assertEqual(len(self.rpc['btc'].payments[h]),1)
            self.assertEqual(self.gate.call('reverse-status',{'payment_hash':h})['phase'],'failed' if failed else 'resolved')
        self.assertEqual(sum(m=='sendpay' for m,_ in self.rpc['btc'].calls),2)
        self.assertEqual(sum(m in ('reverse-release','reverse-fail') for m,_ in self.rpc['xbt'].calls),2)
        for role in self.rpc:
            token=json.loads(self.tokens[role])
            status=self.plugins[role].call('swap-reverse-call',dict(session_id=token['session_id'],operation='info',contract='',pilot_id='',preimage=''))
            self.assertEqual(status['remaining'],0)
        self.assertTrue(set(REVERSE_STAGES)<=set(self.commands));self.assertFalse(self.errors,self.errors)
    def test_normal(self):self.flow('normal')
    def test_lost_reply(self):self.flow('lost-reply')
    def test_failure(self):self.flow('failure')
    def test_restart(self):self.flow('restart')


class MarketFlowTests(FlowTests):
    market=True


class AdmissionTests(unittest.TestCase):
    def test_every_routed_fixture_stage_has_a_host_command(self):
        launch=launcher()
        for name in ('image_repeat.py','image_routed.py','image_reverse_routed.py'):
            calls=[n for n in ast.walk(ast.parse((REMOTE/name).read_text()))
                   if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='bridge']
            self.assertTrue(calls,name)
            for call in calls:
                stage=ast.literal_eval(call.args[0])
                validate_job(dict(stage=stage),launch.quote.STAGES)
                command=launch.command(ROOT,Path('/exchange'),'image','network','worker',stage)
                self.assertEqual(command[-2:],['/remote-tests/pilot_step.py',stage])
    def test_unknown_stages_and_extra_fields_are_rejected(self):
        launch=launcher()
        for job in ({'stage':'reverse-shell'},{'stage':'reverse-worker','command':'pay'},[],None):
            with self.assertRaises(ValueError):validate_job(job,launch.quote.STAGES)
        for stage in ('reverse-shell','/bin/sh','assert-controller-absent'):
            with self.assertRaises(ValueError):launch.command(ROOT,Path('/exchange'),'image','network','worker',stage)
    def test_direction_dispatch_does_not_load_forward_encoder_for_reverse(self):
        # Import the actual top-level fixture and selected direction, replacing
        # only the compiled-image bootstrap which cannot run without Docker.
        code="""import os,sys,types
dummy=types.ModuleType('image_pair');dummy.PairLab=object;dummy.PIN='pin';dummy.check_bundle=lambda *_:None
sys.modules['image_pair']=dummy
import image_pilot
assert 'pilot_regtest_node' not in sys.modules
import image_reverse_routed,reverse_invoice
assert reverse_invoice.unsigned('ab'*32,'cd'*32,220).startswith('lnxbtrt')
"""
        env=dict(os.environ,BTC_XBT_DISPOSABLE_CONTAINER='1',PYTHONDONTWRITEBYTECODE='1',
                 PYTHONPATH=os.pathsep.join(map(str,[ROOT/'assets',REMOTE,ROOT.parent/'btc-cln-startos/assets/swaps',PINNED])))
        result=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] in ('--node','--enable','--worker'):subprocess_entry(sys.argv[1],sys.argv[2:])
    else:unittest.main()
