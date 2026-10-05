"""Restricted controller probe transport. No payment, parameter or retry API."""
import json
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request

METHODS = ('getinfo', 'listpeerchannels')
RESTRICTIONS = [['method=getinfo', 'method=listpeerchannels'], ['pnum=0']]


class ProbeError(ValueError):
    """Fixed privacy-safe label; never a raw remote error."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path not in ('', '/')
                or parsed.query or parsed.fragment or any(c.isspace() for c in url)
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError()
    except (ValueError, TypeError):
        raise ProbeError('invalid_https_endpoint') from None
    return url.rstrip('/')


class Client:
    def __init__(self, url, rune, ca_file=None, ca_data=None):
        self.url = endpoint(url)
        if not isinstance(rune, str) or not re.fullmatch(r'[A-Za-z0-9_+=/-]{1,8192}', rune):
            raise ProbeError('invalid_rune_format')
        self.rune = rune
        try:
            context = ssl.create_default_context(cafile=ca_file, cadata=ca_data)
            self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context), NoRedirect())
        except Exception:
            raise ProbeError('tls_configuration_failed') from None

    def call(self, method):
        if method not in METHODS:
            raise ProbeError('method_not_allowed')
        request = urllib.request.Request(self.url+'/v1/'+method, data=b'{}',
            headers={'Content-Type': 'application/json', 'Rune': self.rune}, method='POST')
        try:
            with self.opener.open(request, timeout=15) as response:
                data = response.read(1024*1024+1)
            if len(data) > 1024*1024:
                raise ProbeError('response_too_large')
            value = json.loads(data)
            if not isinstance(value, dict) or 'error' in value:
                raise ProbeError('invalid_rpc_response')
            return value
        except ProbeError:
            raise
        except urllib.error.HTTPError as error:
            error.close()
            raise ProbeError('http_request_rejected') from None
        except Exception:
            raise ProbeError('tls_or_transport_failed') from None

    def inspect(self, node_id, network):
        if (not isinstance(node_id, str) or not re.fullmatch(r'0[23][0-9a-f]{64}', node_id)
                or network not in ('bitcoin', 'xbt', 'regtest', 'xbt-regtest')):
            raise ProbeError('invalid_expected_identity')
        info = self.call('getinfo')
        if info.get('id') != node_id or info.get('network') != network:
            raise ProbeError('operator_identity_mismatch')
        channels = self.call('listpeerchannels').get('channels')
        if not isinstance(channels, list):
            raise ProbeError('invalid_channel_response')
        try:
            return dict(read_only=True, identity_matches=True, network=network,
                        warning_present=any(k.startswith('warning_') for k in info),
                        normal_channels=sum(c.get('state') == 'CHANNELD_NORMAL' for c in channels),
                        pending_htlcs=sum(len(c.get('htlcs', [])) for c in channels),
                        payment_started=False)
        except Exception:
            raise ProbeError('invalid_channel_response') from None
