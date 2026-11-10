#!/usr/bin/env python3
"""Authenticated BTC-to-XBT receive adapter; never a general RPC/admin proxy."""
import argparse
import hmac
import json
from pathlib import Path
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LIMIT = 24000
FORMAT = 'xbt-wallet-receive-v1'


def receive(service, request):
    import held_gate as hg
    hg.need(type(request) is dict and set(request)=={'action','invoice','environment','provider'}
            and request['action'] in ('create','status'), 'wallet_request_invalid')
    invoice=request['invoice']
    hg.need(type(invoice) is str and 100<=len(invoice)<=20000,'wallet_invoice_invalid')
    import service as engine
    hg.need(request['environment']==engine.environment(service.record),'wallet_network_mismatch')
    service.guard()
    # Original recipient invoice is the durable idempotency key. A lost reply
    # never substitutes an invoice, creates a second swap, or replays a payment.
    cid=None
    for key, job in service.record['jobs'].items():
        c=job['worker']['swaps'][key]['contract']
        if c['direction']=='forward' and c['outgoing']['invoice']==invoice:
            cid=key;break
    if cid is None:
        hg.need(request['action']=='create','wallet_receive_not_found')
        generation=service.record['selected']['forward']
        hg.need(generation is not None,'service_pairing_required')
        binding=service.record['pairings'][generation]
        hg.need(request['provider']==binding['nodes']['outgoing']['grant']['chain']['node_id'],'wallet_provider_changed')
        view=service.prepare('forward',invoice)
        cid=view['confirmation']
    job=service.record['jobs'][cid]; saved=job['worker']['swaps'][cid]; c=saved['contract']
    hg.need(request['provider']==c['outgoing']['chain']['node_id'],'wallet_provider_changed')
    if request['action']=='create' and 'approval' not in saved:
        if int(service.clock()) >= c['pay_until']:
            service.cancel(cid,cid)
        else:
            service.confirm(cid,cid,confirmed=True)
        saved=service.record['jobs'][cid]['worker']['swaps'][cid]
    status=service.cached(cid)
    result=dict(format=FORMAT, id=cid, provider=request['provider'], environment=request['environment'],
        state=status['state'], terminal=status['terminal'], blocked=status['blocked'],
        payment_hash=c['outgoing']['payment_hash'], xbt_msat=c['outgoing']['amount_msat'],
        btc_msat=c['incoming']['amount_msat'], expires_at=c['pay_until'])
    if 'invoice' in saved and not status['terminal']:
        result['invoice']=saved['invoice']['invoice']
    return result


def server(address, directory, config, factory=None):
    import service_cli, service
    gate=threading.BoundedSemaphore(4)
    class Handler(BaseHTTPRequestHandler):
        server_version='WalletReceive/1';sys_version=''
        def log_message(self,*args):pass
        def setup(self):
            super().setup();self.connection.settimeout(10)
        def reply(self, code, value):
            raw=json.dumps(value,separators=(',',':')).encode()
            self.send_response(code)
            self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('Connection','close');self.end_headers()
            try:self.wfile.write(raw)
            except OSError:pass
            self.close_connection=True
        def do_GET(self):
            self.reply(200 if self.path=='/health' else 404,dict(format=FORMAT))
        def do_POST(self):
            if self.path!='/v1/receive':return self.reply(404,dict(error='not_found'))
            try:
                settings=json.loads(Path(config).read_text())
                token=settings['token']
                valid=settings.get('enabled') is True and type(token) is str and re.fullmatch('[a-f0-9]{64}',token)
            except (OSError,ValueError,KeyError,TypeError):valid=False;token=''
            if not valid or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):
                return self.reply(401,dict(error='wallet_access_denied'))
            if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1:
                return self.reply(400,dict(error='wallet_request_invalid'))
            try:
                size=int(self.headers['Content-Length'])
                if not 0<size<=LIMIT:raise ValueError()
                if self.headers.get_content_type()!='application/json':raise ValueError()
                raw=self.rfile.read(size)
                if len(raw)!=size:raise ValueError()
                request=json.loads(raw,object_pairs_hook=service.hg.unique_object)
            except (ValueError,OSError):return self.reply(400,dict(error='wallet_request_invalid'))
            journal=None
            try:
                journal=service_cli.action_journal(Path(directory))
                instance=(factory or service.Service)(journal)
                self.reply(200,dict(result=receive(instance,request)))
            except Exception as error:
                report=service_cli.failure_report(error,directory)
                # Fixed local validation codes are also safe for wallet clients.
                if isinstance(error,ValueError) and str(error) in {'wallet_request_invalid','wallet_invoice_invalid','wallet_network_mismatch','wallet_receive_not_found','wallet_provider_changed'}:
                    report['reason']=str(error)
                self.reply(200,dict(error=report['reason'],diagnostic=report['diagnostic']))
            finally:
                if journal:journal.close()
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def process_request(self,request,address):
            if not gate.acquire(False):self.shutdown_request(request);return
            try:super().process_request(request,address)
            except BaseException:gate.release();raise
        def process_request_thread(self,request,address):
            try:super().process_request_thread(request,address)
            finally:gate.release()
        def handle_error(self,*args):pass
    return Server(address,Handler)


def main():
    sys.dont_write_bytecode=True
    sys.path.insert(0,'/usr/local/libexec/swap-controller')
    import package_verify
    package_verify.verify('/usr/local/libexec/swap-controller','controller')
    p=argparse.ArgumentParser();p.add_argument('--directory',required=True);p.add_argument('--config',required=True)
    p.add_argument('--bind',default='0.0.0.0');p.add_argument('--port',type=int,default=9081);a=p.parse_args()
    server((a.bind,a.port),a.directory,a.config).serve_forever()

if __name__=='__main__':main()
