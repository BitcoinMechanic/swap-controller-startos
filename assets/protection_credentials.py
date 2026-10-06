"""Server-side rune restrictions for disposable regtest protection.

No issuance or live authority. Forward release lacks a payment_hash parameter;
this module deliberately cannot create a bound forward release credential.
"""
import re
from controller import require


def restrictions(network, purpose, target):
    require(network in ('regtest', 'xbt-regtest'), 'regtest_network_required')
    require(type(target) is str and re.fullmatch('[0-9a-f]{64}', target), 'invalid_credential_target')
    require(purpose in ('close', 'reverse-release'), 'unsupported_credential_purpose')
    require(purpose != 'reverse-release' or network == 'xbt-regtest', 'reverse_network_required')
    reads = ['getinfo', 'listpeerchannels', 'listsendpays']
    reads += ['xbt-quote-status', 'xbt-spend-info'] if network == 'regtest' else ['reverse-status']
    rules = [['method=' + method for method in reads + [purpose]]]
    # Each list is OR; separate lists are AND. Only named parameters are allowed
    # for the mutation, matching the packaged HTTPS clients. pnum rejects extras.
    if purpose == 'close':
        rules += [['method/close', 'pnum=2'],
                  ['method/close', 'pnameid=' + target],
                  ['method/close', 'pnameunilateraltimeout=1']]
    else:
        rules += [['method/reverse-release', 'pnum=3'],
                  ['method/reverse-release', 'pnamepayment_hash=' + target]]
    return rules
