#!/usr/bin/env python3
"""Explicitly provisioned TLS quote/status endpoint. No payments or admin proxy."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import ssl
from pathlib import Path

import customer_https as https
import held_gate as hg
import provider_api as api
import provider_quote as protocol
import service


class Signer:
    """Private, separate CLNrest credential restricted to getinfo/signmessage."""
    def __init__(self,config):
        hg.need(type(config) is dict and set(config)=={'url','ca_pem','rune'},'provider_signer_invalid')
        self.url=https.endpoint(config['url']); self.context=https.context(config['ca_pem'])
        hg.need(https.remote.base.token(config['rune']),'provider_signer_invalid')
        self.rune=config['rune']

    def rpc(self,method,body):
        hg.need(method in ('getinfo','signmessage'),'provider_signer_method_refused')
        p=https.urllib.parse.urlsplit(self.url)
        conn=https.http.client.HTTPSConnection(p.hostname,p.port or 443,context=self.context,timeout=15)
        try:
            conn.request('POST','/v1/'+method,body=json.dumps(body),headers={'Content-Type':'application/json','Rune':self.rune})
            response=conn.getresponse(); raw=response.read(protocol.LIMIT+1)
            hg.need(response.status==201 and len(raw)<=protocol.LIMIT
                    and response.getheader('Content-Type','').split(';')[0]=='application/json','provider_signer_unavailable')
            return json.loads(raw,object_pairs_hook=hg.unique_object,
                              parse_constant=lambda _:hg.need(False,'provider_signer_unavailable'))
        except Exception: raise ValueError('provider_signer_unavailable') from None
        finally: conn.close()

    def __call__(self,terms):
        protocol.terms(terms)
        https.ba.ci.node(self.rpc('getinfo',{}),https.ba.ci.PRIVKEYIO_XBT,terms['environment'],expected_id=terms['provider'])
        return self.rpc('signmessage',dict(message=protocol.message(terms)))['zbase']


def dispatcher(directory,service_directory,origin,environment,signer,*,factory=https.Client,pricer=None,clock=api.time.time):
    origin=https.endpoint(origin)
    hg.need(environment in https.ba.ci.ENVIRONMENTS,'provider_environment_invalid')
    hg.need(Path(directory).resolve()!=Path(service_directory).resolve(),'provider_directory_invalid')
    def quote(invoice,rid):
        journal=hg.Journal(service_directory)
        try:
            controller=service.Service(journal,factory=factory,pricer=pricer,clock=clock)
            hg.need(service.environment(controller.record)==environment,'provider_environment_changed')
            return api.preview(controller,invoice,origin,rid)
        finally: journal.close()
    def dispatch(operation,body):
        journal=hg.Journal(directory)
        try: return api.Gateway(journal,quote=quote,sign=signer,clock=clock).execute(operation,body)
        finally: journal.close()
    return dispatch


def server(address,dispatch,context):
    hg.need(context.minimum_version>=ssl.TLSVersion.TLSv1_2,'provider_tls_required')
    class Handler(BaseHTTPRequestHandler):
        server_version='MerchantQuote/1'
        sys_version=''
        def log_message(self,*args): pass  # No paths, bearer tokens, invoices or raw errors.
        def setup(self):
            super().setup(); self.connection.settimeout(15)
        def do_POST(self):
            code=400; reply={'error':'provider_request_invalid'}
            try:
                operations={v:k for k,v in protocol.PATHS.items()}
                hg.need(self.path in operations and self.headers.get('Transfer-Encoding') is None
                        and len(self.headers.get_all('Content-Length',[]))==1
                        and self.headers.get('Content-Type','').split(';')[0]=='application/json','provider_request_invalid')
                length=int(self.headers['Content-Length'])
                hg.need(0<length<=protocol.LIMIT,'provider_request_invalid')
                raw=self.rfile.read(length)
                hg.need(len(raw)==length,'provider_request_invalid')
                body=json.loads(raw,object_pairs_hook=hg.unique_object,
                                parse_constant=lambda _:hg.need(False,'provider_request_invalid'))
                reply=dispatch(operations[self.path],body); code=200
            except (hg.StorageError,BlockingIOError):
                code=503; reply={'error':'provider_request_uncertain'}
            except Exception as error:
                # Only fixed public protocol failures cross this boundary.
                public={'provider_request_unknown':404,'provider_request_conflict':409,
                        'provider_rate_limit':429,'provider_capacity':503,'provider_blocked':503,
                        'provider_request_invalid':400,'invoice_invalid':400}
                reason=str(error)
                code=public.get(reason,400)
                reply={'error':reason if reason in public else 'provider_request_refused'}
            raw=json.dumps(reply,allow_nan=False,separators=(',',':')).encode()
            if len(raw)>protocol.LIMIT:
                code=503; raw=b'{"error":"provider_request_uncertain"}'
            try:
                self.send_response(code); self.send_header('Content-Type','application/json')
                self.send_header('Cache-Control','no-store'); self.send_header('Connection','close')
                self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
            except (OSError,ssl.SSLError): pass
            self.close_connection=True
    class TLSHTTPServer(HTTPServer):
        def get_request(self):
            sock,address=super().get_request(); sock.settimeout(15)
            try: return context.wrap_socket(sock,server_side=True),address
            except BaseException: sock.close(); raise
        def handle_error(self,*args): pass
    return TLSHTTPServer(address,Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('initialize','run'))
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=9443)
    args=parser.parse_args(); os.umask(0o077)
    if args.action=='initialize': api.initialize(args.directory); return
    hg.need(args.config is not None,'provider_config_required')
    fd=os.open(args.config,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        hg.Journal.check_file(stream.fileno()); raw=stream.read(131073)
    hg.need(len(raw)<=131072,'provider_config_invalid')
    c=json.loads(raw,object_pairs_hook=hg.unique_object)
    hg.need(type(c) is dict and set(c)=={'service_directory','origin','environment','signer','certificate','key'},
            'provider_config_invalid')
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.minimum_version=ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(c['certificate'],c['key'])
    dispatch=dispatcher(args.directory,c['service_directory'],c['origin'],c['environment'],Signer(c['signer']))
    server((args.bind,args.port),dispatch,ctx).serve_forever()


if __name__=='__main__':
    try: main()
    except Exception:
        raise SystemExit('Provider endpoint unavailable; preserve configuration and journals.') from None
