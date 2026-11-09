#!/usr/bin/env python3
"""Read-only public discovery. No imports, mounts or access to swap authority."""
import argparse
import json
import re
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

FORMAT = 'xbt-swap-provider-v1'
LIMIT = 65536


def endpoint(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError('Invalid endpoint')
    p = urlsplit(value)
    onion = p.hostname and re.fullmatch(r'[a-z2-7]{56}\.onion', p.hostname)
    if (p.scheme != 'https' and not (p.scheme == 'http' and onion)) or not p.hostname or p.username is not None or p.password is not None or p.query or p.fragment or p.path not in ('', '/'):
        raise ValueError('Use an HTTPS origin or HTTP v3 onion origin')
    if p.port is not None and not 1 <= p.port <= 65535:
        raise ValueError('Invalid port')
    return value.rstrip('/')


def metadata(value):
    if not isinstance(value, dict) or set(value) != {'name', 'provider', 'environment', 'endpoints'}:
        raise ValueError('Invalid public metadata')
    name = value['name']
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
        raise ValueError('Invalid name')
    if not isinstance(value['provider'], str) or not re.fullmatch(r'(02|03)[0-9a-f]{64}', value['provider']):
        raise ValueError('Invalid provider public key')
    if value['environment'] not in ('mainnet', 'regtest'):
        raise ValueError('Invalid environment')
    urls = value['endpoints']
    if not isinstance(urls, list) or not 1 <= len(urls) <= 4:
        raise ValueError('Invalid endpoints')
    urls = list(dict.fromkeys(endpoint(u) for u in urls))
    return dict(format=FORMAT, name=name.strip(), provider=value['provider'], environment=value['environment'],
                endpoints=urls, pairs=['XBT/BTC'], capabilities=['discovery'], payment_available=False)


def server(address, config, context=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'SwapDiscovery/1'
        sys_version = ''
        def log_message(self, *args): pass
        def setup(self):
            super().setup()
            self.connection.settimeout(5)
        def do_GET(self):
            code = 404; reply = {'error': 'not_found'}
            if self.path in ('/v1/provider/info', '/v1/providers'):
                try:
                    with Path(config).open('rb') as stream:
                        raw = stream.read(LIMIT + 1)
                    if len(raw) > LIMIT: raise ValueError()
                    item = metadata(json.loads(raw))
                    reply = item if self.path.endswith('/info') else {'format': 'xbt-swap-directory-v1', 'providers': [item]}
                    code = 200
                except (OSError, ValueError, TypeError):
                    code = 503; reply = {'error': 'discovery_not_configured'}
            raw = json.dumps(reply, ensure_ascii=True, separators=(',', ':')).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Connection', 'close')
            self.end_headers()
            try: self.wfile.write(raw)
            except OSError: pass
            self.close_connection = True
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        slots = threading.BoundedSemaphore(8)
        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try: super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise
        def process_request_thread(self, request, address):
            try: super().process_request_thread(request, address)
            finally: self.slots.release()
        def handle_error(self, *args): pass
        def get_request(self):
            conn, address = super().get_request()
            conn.settimeout(5)
            if context:
                try: conn = context.wrap_socket(conn, server_side=True)
                except BaseException: conn.close(); raise
            return conn, address
    return Server(address, Handler)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True)
    p.add_argument('--bind', default='127.0.0.1')
    p.add_argument('--port', type=int, default=9080)
    p.add_argument('--certificate'); p.add_argument('--key')
    args = p.parse_args()
    if bool(args.certificate) != bool(args.key): p.error('Certificate and key required together')
    context = None
    if args.certificate:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.certificate, args.key)
    server((args.bind, args.port), args.config, context).serve_forever()


if __name__ == '__main__': main()
