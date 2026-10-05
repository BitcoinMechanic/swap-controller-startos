"""Reviewed direct-channel BTC -> XBT quote flow. Disposable regtest only.

The pinned quote service constructs and signs invoices; this adapter supplies
HTTPS-only RPC, durable review approval, and a handoff to the existing executor.
No live pairing, automatic pricing, routed payments or deadline policy.
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import time

from controller import private_load, require, save
import executor
import lifecycle
from execution_rpc import Remote

READS = {'getinfo': (), 'decode': ('string',), 'listpeerchannels': (), 'listsendpays': (),
         'xbt-quote-status': ('payment_hash',), 'xbt-spend-info': ('payment_hash',),
         'reverse-status': ('payment_hash',), 'xbt-held': ()}
WRITES = {'xbt-register': ('quote',), 'signinvoice': ('invstring',), 'reverse-register': ('quote',)}


class QuoteRemote(Remote):
    publication_network = 'regtest'
    def call(self, method, *args, named=False):
        schema = {**READS, **(WRITES if self.network == self.publication_network else {})}
        require(method in schema and not named and len(args) == len(schema[method]), 'quote_method_refused')
        require(method not in ('xbt-register','xbt-quote-status','xbt-spend-info') or self.network=='regtest', 'quote_target_refused')
        require(method not in ('reverse-register','reverse-status','xbt-held') or self.network=='xbt-regtest', 'quote_target_refused')
        params = dict(zip(schema[method], args))
        if method in ('xbt-register','reverse-register'): params['quote'] = json.loads(params['quote'])
        info = self._request('getinfo', {})
        require(info.get('id') == self.node_id and info.get('network') == self.network, 'operator_identity_mismatch')
        return info if method == 'getinfo' else self._request(method, params)


def service(modules=Path('/opt/swap')):
    require((modules/'SOURCE_COMMIT').read_text().strip() == executor.PIN, 'source_pin_mismatch')
    sys.path.insert(0, str(modules))
    import swap_service
    return swap_service


@contextlib.contextmanager
def transport(core, connections, writable=False, factory=QuoteRemote):
    require(isinstance(connections, list) and len(connections) == 2, 'two_regtest_nodes_required')
    clients = {}
    for c in connections:
        require(set(c) == {'network','node_id','rune','ca_pem','url','cli'}, 'invalid_connections')
        require(isinstance(c['cli'], list) and c['cli'] and all(isinstance(a,str) and a for a in c['cli']), 'invalid_node_binding')
        require('--network='+c['network'] in c['cli'], 'invalid_node_binding')
        client = factory(c)
        require(c['network'] not in clients, 'distinct_networks_required')
        clients[c['network']] = (c, client)
    require(set(clients) == {'regtest','xbt-regtest'}, 'regtest_networks_required')
    require(len({c['node_id'] for c in connections}) == 2 and len({tuple(c['cli']) for c in connections}) == 2,
            'distinct_nodes_required')
    # Check BOTH identities and synchronization before any further RPC.
    for c, client in clients.values():
        info = client.call('getinfo')
        require(info.get('id') == c['node_id'] and info.get('network') == c['network'], 'operator_identity_mismatch')
        require(not any(k.startswith('warning') and v for k,v in info.items()), 'node_not_ready')
    def call(cli, method, *args):
        matches = [client for c,client in clients.values() if c['cli'] == cli]
        require(len(matches) == 1 and (method in READS or writable and method in WRITES), 'quote_rpc_refused')
        return matches[0].call(method, *args)
    old = core.RPC.call
    core.RPC.call = staticmethod(call)
    try: yield call
    finally: core.RPC.call = staticmethod(old)


def job(manager, name):
    require(isinstance(name,str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',name), 'invalid_job')
    lifecycle.setup(manager)
    root = manager/'jobs'/name
    require(not root.is_symlink(), 'invalid_job')
    return root


def review(root):
    record = private_load(root/'review.json')
    require(record.get('schema') == 1 and record.get('source_commit') == executor.PIN, 'invalid_review')
    data = private_load(root/'proposal'/'quote.json')
    direction=record.get('direction','forward')
    require(direction in ('forward','reverse'),'invalid_direction')
    original = {k:v for k,v in data.items() if k != ('xbt_invoice' if direction=='reverse' else 'btc_invoice')}
    require(original == record['quote'], 'quote_binding_changed')
    return record, data, executor.digest(record)


def report(root):
    record, data, token = review(root)
    terms = data['terms']
    reverse=record.get('direction')=='reverse'
    phase = 'review_required'
    if (root/'approval.json').exists(): phase = 'approved'
    if ('xbt_invoice' if reverse else 'btc_invoice') in data: phase = 'waiting_for_xbt' if reverse else 'waiting_for_btc'
    if (root/'intent.json').exists(): phase = executor.records(root)[1]['phase']
    result = dict(regtest_only=True,live_payment_enabled=False,direction='reverse' if reverse else 'forward',review_digest=token,
                phase=phase,btc_price_sats=terms['btc_amount_msat']//1000,xbt_amount_msat=terms['xbt_amount_msat'],
                expires_at=terms['expires_at'],quote_expired=terms['expires_at'] <= int(time.time()),
                outgoing_route_fee_msat=0,btc_payer_routing_fee_included=False,
                pricing='operator_supplied_regtest',recipient=data['controller']['route'][0]['id'])
    if reverse:
        result.pop('btc_price_sats');result.pop('btc_payer_routing_fee_included');result.pop('xbt_amount_msat')
        result.update(xbt_price_sats=terms['xbt_amount_msat']//1000,btc_amount_msat=terms['btc_amount_msat'],
                      xbt_payer_routing_fee_included=False)
    return result


def preflight(data, rpc):
    terms = data['terms']; state = data['controller']
    decoded = rpc(state['xbt_cli'], 'decode', terms['xbt_invoice'])
    expected = dict(valid=True,type='bolt11 invoice',currency='xbtrt',payment_hash=terms['payment_hash'],
                    payment_secret=state['payment_secret'],amount_msat=terms['xbt_amount_msat'],payee=state['route'][0]['id'])
    require(all(decoded.get(k) == v for k,v in expected.items()), 'invoice_binding_changed')
    now = int(time.time())
    require(type(decoded.get('created_at')) is int and type(decoded.get('expiry')) is int and
            decoded['created_at'] <= now and terms['expires_at'] <= decoded['created_at']+decoded['expiry']-60 and
            now < terms['expires_at'], 'quote_expired_or_invalid')
    require(type(decoded.get('min_final_cltv_expiry')) is int and 0 < decoded['min_final_cltv_expiry'] <= 40,
            'unsupported_cltv')
    require(re.fullmatch('[0-9a-f]{64}',terms['payment_hash']) and
            re.fullmatch('[0-9a-f]{64}',state['payment_secret']), 'invalid_invoice_hex')
    route = state['route'][0]
    channels = [c for c in rpc(state['xbt_cli'],'listpeerchannels')['channels']
                if c.get('short_channel_id') == route['channel'] and c.get('peer_id') == route['id']]
    require(len(channels) == 1 and channels[0].get('state') == 'CHANNELD_NORMAL' and
            channels[0].get('peer_connected') is True and channels[0].get('spendable_msat',0) >= terms['xbt_amount_msat']
            and not channels[0].get('htlcs'), 'direct_channel_not_ready')
    require(not any(p.get('payment_hash') == terms['payment_hash'] for p in rpc(state['xbt_cli'],'listsendpays')['payments']),
            'outgoing_attempt_already_exists')


def prepare(manager, name, request, core=None, factory=QuoteRemote):
    executor.guard()
    require(isinstance(request,dict) and set(request) == {'connections','xbt_invoice','btc_sats'}, 'invalid_quote_request')
    invoice = request['xbt_invoice']
    require(isinstance(invoice,str) and 0 < len(invoice) <= 65536 and invoice.isascii() and
            invoice.startswith('lnxbtrt') and not any(c.isspace() for c in invoice), 'regtest_invoice_required')
    require(type(request['btc_sats']) is int and 0 < request['btc_sats'] <= 1000000, 'regtest_price_limit')
    root = job(manager,name)
    with lifecycle.locked(manager):
        executor.execution_allowed(root)
        root.mkdir(mode=0o700,exist_ok=True)
    core = service() if core is None else core
    with executor.lock(root):
        executor.execution_allowed(root)
        if (root/'review.json').exists():
            saved, _, _ = review(root)
            require(saved['request_digest'] == executor.digest(request), 'existing_review_changed')
            return report(root)
        require(not any(p.name != 'executor.lock' for p in root.iterdir()), 'incomplete_review_requires_inspection')
        config = {role+'_cli':next((c['cli'] for c in request['connections'] if c.get('network') == network),None)
                  for role,network in (('btc','regtest'),('xbt','xbt-regtest'))}
        with transport(core,request['connections'],factory=factory) as rpc:
            # Reuse pinned invoice, time-window, direct-channel and amount policy.
            core.create(config,invoice,request['btc_sats'],root/'proposal')
            data = private_load(root/'proposal'/'quote.json')
            require(0 < data['terms']['xbt_amount_msat'] <= 1000000000, 'regtest_amount_limit')
            preflight(data,rpc)
            for other in lifecycle.jobs(manager):
                if other == root: continue
                require(not other.is_symlink() and other.is_dir(), 'unreadable_existing_job')
                if (other/'review.json').exists():
                    require(review(other)[1]['terms']['payment_hash'] != data['terms']['payment_hash'], 'invoice_already_reviewed')
                elif (other/'intent.json').exists():
                    require(executor.records(other)[1]['payment_hash'] != data['terms']['payment_hash'], 'invoice_already_used')
                else: require(False, 'unreadable_existing_job')
        save(root/'review.json',dict(schema=1,source_commit=executor.PIN,request_digest=executor.digest(request),
                                   quote=data,connections=request['connections']))
        return report(root)


def approve(manager,name,token,confirmed=False,core=None,factory=QuoteRemote):
    executor.guard()
    require(confirmed is True, 'confirmation_required')
    root=job(manager,name)
    if review(root)[0].get('direction')=='reverse':
        from reverse_quote_workflow import approve as reverse_approve, ReverseRemote
        return reverse_approve(manager,name,token,confirmed,core,ReverseRemote if factory is QuoteRemote else factory)
    core=service() if core is None else core
    with executor.lock(root):
        executor.execution_allowed(root)
        record,data,expected=review(root)
        require(token == expected, 'review_digest_mismatch')
        approval=dict(digest=token,expires_at=data['terms']['expires_at'])
        if (root/'approval.json').exists():
            require(private_load(root/'approval.json') == approval, 'approval_changed')
        with transport(core,record['connections'],writable=True,factory=factory) as rpc:
            # Reprinting a saved invoice never registers/signs again. Expired
            # copies are not offered as payable invoices.
            if 'btc_invoice' not in data:
                preflight(data,rpc)
                save(root/'approval.json',approval)
            else:
                require((root/'approval.json').exists() and int(time.time()) < data['terms']['expires_at'], 'quote_expired')
            result=core.publish(root/'proposal')
        return dict(report(root),btc_invoice=result['btc_invoice'])


def advance(root,core=None,factory=QuoteRemote):
    """One worker cycle: bind the committed HTLC, then run/recover the executor."""
    executor.guard()
    if review(root)[0].get('direction')=='reverse':
        from reverse_quote_workflow import advance as reverse_advance, ReverseRemote
        return reverse_advance(root,core,ReverseRemote if factory is QuoteRemote else factory)
    core=service() if core is None else core
    handoff=None
    with executor.lock(root):
        executor.execution_allowed(root)
        record,data,token=review(root)
        if not (root/'approval.json').exists(): return report(root)
        require(private_load(root/'approval.json') == dict(digest=token,expires_at=data['terms']['expires_at']), 'approval_changed')
        if (root/'intent.json').exists():
            receipt=private_load(root/'handoff.json')
            initial,_=executor.records(root)
            require(initial['state'] == receipt['state'] and initial['connections'] == record['connections'] and
                    receipt['review_digest'] == token, 'handoff_changed')
            handoff=receipt
        else:
            require('btc_invoice' in data, 'finish_quote_publication')
            with transport(core,record['connections'],factory=factory) as rpc:
                terms=data['terms']; config=data['config']
                status=rpc(config['btc_cli'],'xbt-quote-status',terms['payment_hash'])
                require(status.get('payment_hash') == terms['payment_hash'], 'quote_identity_mismatch')
                if status['phase'] == 'quoted': return report(root)
                require(status['phase'] == 'held', 'unexpected_quote_phase')
                binding=status.get('binding')
                require(isinstance(binding,list) and len(binding)==2 and isinstance(binding[0],str) and
                        type(binding[1]) is int and binding[1]>=0, 'invalid_htlc_binding')
                matches=[h for c in rpc(config['btc_cli'],'listpeerchannels')['channels']
                         if c.get('short_channel_id')==binding[0] and c.get('state')=='CHANNELD_NORMAL'
                         for h in c.get('htlcs',[]) if h.get('id')==binding[1] and h.get('direction')=='in'
                         and h.get('payment_hash')==terms['payment_hash'] and h.get('state')=='RCVD_ADD_ACK_REVOCATION']
                if not matches: return report(root)
                require(len(matches)==1 and matches[0].get('amount_msat')==terms['btc_amount_msat'], 'incoming_amount_mismatch')
                preflight(data,rpc)
                state=dict(data['controller'],btc_binding=binding)
                # The existing packaged executor supports direct controlled-chain
                # fixtures, not the service's CLI/on-chain deadline watcher.
                state.pop('btc_deadline_guard',None)
                info=rpc(config['btc_cli'],'xbt-spend-info',terms['payment_hash'])
                require(all(info.get(k)==terms[k] for k in ('payment_hash','btc_amount_msat','xbt_amount_msat',
                        'xbt_invoice','expires_at','min_cltv_delta','max_cltv_delta')) and info.get('binding')==binding,
                        'held_terms_mismatch')
                require(matches[0].get('expiry') == info.get('cltv_expiry'), 'incoming_expiry_mismatch')
                from swap_controller import check_spend
                require(check_spend(state) is None, 'spend_preflight_refused')
                handoff=dict(review_digest=token,state=state)
                if (root/'handoff.json').exists(): require(private_load(root/'handoff.json')==handoff,'handoff_changed')
                else: save(root/'handoff.json',handoff)
    # These APIs acquire the same lifecycle lock independently. The durable
    # handoff makes interrupted or overlapping imports idempotent; restore and
    # backup are checked again by both APIs and before the actual child launch.
    if not (root/'intent.json').exists():
        executor.prepare(root,'forward',handoff['state'],record['connections'])
    initial,state=executor.records(root)
    if state['phase']=='prepared' and not (root/'launched.json').exists():
        executor.authorize(root,executor.digest(initial),data['terms']['expires_at'],confirmed=True)
    return executor.step(root)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manager',type=Path);parser.add_argument('mode',choices=('prepare','approve','status','step'))
    parser.add_argument('job');args=parser.parse_args()
    executor.guard()
    if args.mode in ('prepare','approve'):
        raw=sys.stdin.read(131073);require(len(raw)<=131072,'request_too_large');request=json.loads(raw)
    if args.mode=='prepare': result=prepare(args.manager,args.job,request)
    elif args.mode=='approve':
        require(set(request)=={'digest','confirmed'},'invalid_approval')
        result=approve(args.manager,args.job,request['digest'],request['confirmed'])
    else:
        root=job(args.manager,args.job)
        if args.mode=='step': advance(root)
        with executor.lock(root): result=report(root)
    print(json.dumps(result))


if __name__=='__main__':
    try: main()
    except Exception:
        print('{"event":"quote_refused_or_needs_inspection","details":"withheld","live_payment_enabled":false}')
        raise SystemExit(1) from None
