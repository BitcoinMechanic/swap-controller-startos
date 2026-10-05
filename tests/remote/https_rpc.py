"""Experimental regtest transport, deliberately excluded from the service image.

Method restrictions are not a spending policy. The pinned controllers enforce
quote/HTLC/amount bindings. No network failure authorizes a retry or refund.
"""
import json
import re
import subprocess
import urllib.error
import urllib.request

from read_only_rpc import Client

COMMON = {'getinfo': (), 'listpeerchannels': (), 'listsendpays': (),
          'decode': ('string',), 'waitsendpay': ('payment_hash', 'timeout')}
GATES = {
    'regtest': {'xbt-spend-info': ('payment_hash',),
                'xbt-quote-status': ('payment_hash',),
                'xbt-release': ('preimage',), 'xbt-fail': ('payment_hash', 'binding')},
    'xbt-regtest': {'xbt-held': (), 'reverse-status': ('payment_hash',),
                    'reverse-release': ('payment_hash', 'binding', 'preimage'),
                    'reverse-fail': ('payment_hash', 'binding')},
}
SEND_FIELDS = {'route', 'payment_hash', 'payment_secret', 'bolt11', 'payment_metadata'}
LIMIT = 1024 * 1024


class RpcFailure(subprocess.CalledProcessError):
    def __init__(self, method, reason):
        # Preserve the controllers' exception class without putting secrets in
        # .cmd, .stdout, .stderr or a traceback. Never retain the remote body.
        super().__init__(1, ['restricted-https-rpc', method],
                         output=json.dumps({'error': reason}), stderr='')


def methods(network):
    if network not in GATES:
        raise ValueError('regtest_network_required')
    return {**COMMON, **GATES[network], 'sendpay': None}


def restrictions(network):
    return [['method='+name for name in sorted(methods(network))]]


def parameters(network, method, args, named=False):
    schema = methods(network)
    if method not in schema:
        raise ValueError('method_not_allowed')
    if method == 'sendpay':
        if not named:
            raise ValueError('named_sendpay_required')
        result = {}
        for arg in args:
            if not isinstance(arg, str) or '=' not in arg:
                raise ValueError('invalid_parameters')
            key, value = arg.split('=', 1)
            if key not in SEND_FIELDS or key in result:
                raise ValueError('invalid_parameters')
            result[key] = json.loads(value) if key == 'route' else value
        if not {'route', 'payment_hash', 'payment_secret'} <= result.keys():
            raise ValueError('invalid_parameters')
        if not isinstance(result['route'], list) or not result['route']:
            raise ValueError('invalid_parameters')
    else:
        if named or len(args) != len(schema[method]):
            raise ValueError('invalid_parameters')
        result = dict(zip(schema[method], args))
        if 'binding' in result:
            result['binding'] = json.loads(result['binding'])
            binding = result['binding']
            if (not isinstance(binding, list) or len(binding) != 2
                    or not isinstance(binding[0], str) or type(binding[1]) is not int):
                raise ValueError('invalid_binding')
        if 'timeout' in result and (type(result['timeout']) is not int
                                    or not 1 <= result['timeout'] <= 10):
            raise ValueError('invalid_timeout')
    for field in ('payment_hash', 'payment_secret', 'preimage'):
        if field in result and not re.fullmatch(r'[0-9a-f]{64}', str(result[field])):
            raise ValueError('invalid_hex_parameter')
    return result


class Remote:
    def __init__(self, config):
        self.network = config['network']
        methods(self.network)  # Reject live networks before constructing transport.
        self.node_id = config['node_id']
        if not re.fullmatch(r'0[23][0-9a-f]{64}', self.node_id):
            raise ValueError('invalid_identity')
        self.client = Client(config['url'], config['rune'], ca_data=config['ca_pem'])

    def _request(self, method, params):
        payload = json.dumps(params, allow_nan=False).encode()
        if len(payload) > LIMIT:
            raise ValueError('request_too_large')
        request = urllib.request.Request(self.client.url+'/v1/'+method, data=payload,
            headers={'Content-Type': 'application/json', 'Rune': self.client.rune}, method='POST')
        try:
            with self.client.opener.open(request, timeout=15) as response:
                data = response.read(LIMIT+1)
            if len(data) > LIMIT:
                raise ValueError()
            value = json.loads(data)
            if not isinstance(value, dict) or 'error' in value:
                raise ValueError()
            return value
        except urllib.error.HTTPError as exc:
            exc.close()
            raise RpcFailure(method, 'rpc_rejected_or_outcome_unknown') from None
        except Exception:
            raise RpcFailure(method, 'rpc_outcome_unknown') from None

    def call(self, method, *args, named=False):
        params = parameters(self.network, method, args, named)
        info = self._request('getinfo', {})
        if info.get('id') != self.node_id or info.get('network') != self.network:
            raise ValueError('operator_identity_mismatch')
        return info if method == 'getinfo' else self._request(method, params)
