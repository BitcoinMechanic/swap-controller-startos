"""Read-only StartOS adapter. Credentials and candidates are never persisted."""
import json
from pathlib import Path
import sys
from controller import require
from live_preflight import inspect
import live_policy as policy


def action(root, direction, request, **kwargs):
    try:
        require(direction in ('forward','reverse'), 'invalid_direction')
        expected={'invoice','incomingChannel','outgoingChannel','btcRune','xbtRune'}
        if direction=='reverse': expected |= {'xbtSats','routeDelay'}
        require(type(request) is dict and set(request)==expected, 'invalid_input')
        require(all(type(request[k]) is str and request[k] for k in expected-{'xbtSats','routeDelay'}), 'invalid_input')
        reverse=direction=='reverse'
        if reverse:
            require(policy.integer(request['xbtSats'],1,500000), 'invalid_input')
            require(policy.integer(request['routeDelay'],40,576), 'invalid_input')
        candidate=dict(policy_digest=policy.digest(),profile=policy.REVERSE if reverse else policy.FORWARD,
            btc_amount_msat=1500000 if reverse else 1000000,
            xbt_amount_msat=request['xbtSats']*1000 if reverse else 2000000,
            invoice=request['invoice'], incoming_channel=request['incomingChannel'],
            outgoing_channel=request['outgoingChannel'], route_delay_blocks=request['routeDelay'] if reverse else 40,
            quote_expires_at=0,incoming_expiry=0)
        return inspect(root,dict(candidate=candidate,credentials=dict(btc=request['btcRune'],xbt=request['xbtRune'])),
                       derive_timing=True,**kwargs)
    except Exception:
        return dict(read_only=True,preflight_matches=False,execution_authorized=False,
                    live_payment_enabled=False,payment_started=False,reasons=['invalid_input'])


def main():
    try:
        require(len(sys.argv)==3,'invalid_arguments')
        raw=sys.stdin.read(262145)
        require(len(raw)<=262144,'invalid_request')
        result=action(Path(sys.argv[1]),sys.argv[2],json.loads(raw))
    except Exception:
        result=dict(read_only=True,preflight_matches=False,execution_authorized=False,
                    live_payment_enabled=False,payment_started=False,reasons=['invalid_input'])
    print(json.dumps(result))
    # A rejected candidate is a normal inspection result, not a hidden UI error.
    return 0

if __name__=='__main__':raise SystemExit(main())
