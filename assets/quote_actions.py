"""StartOS quote actions. Local inspection; regtest-only preparation/approval."""
import json
import os
from pathlib import Path
import re
import sys
import executor
import quote_workflow as workflow
from controller import private_load, require

JOB = r'[a-z0-9][a-z0-9-]{0,63}'
DIGEST = r'[0-9a-f]{64}'
PAIR_FILE = 'regtest-quote-nodes.json'
PHASES = {'review_required','approved','waiting_for_btc','prepared','outgoing_started',
          'xbt_paid','xbt_failed','btc_failed','btc_released','waiting_for_xbt','btc_paid','xbt_released'}


def name(value):
    require(isinstance(value,str) and re.fullmatch(JOB,value), 'invalid_job')
    return value


def directory(manager):
    require(manager.is_dir() and not manager.is_symlink(), 'invalid_manager')
    require((manager/'jobs').is_dir() and not (manager/'jobs').is_symlink(), 'invalid_jobs_directory')


def existing(manager,job):
    name(job);directory(manager)
    root=manager/'jobs'/job
    require(root.is_dir() and not root.is_symlink(), 'job_not_available')
    require((root/'proposal').is_dir() and not (root/'proposal').is_symlink(), 'proposal_not_available')
    return root


def filtered(value):
    """Allow only reviewed scalar fields, never arbitrary saved/private data."""
    require(value.get('phase') in PHASES and value.get('direction') in ('forward','reverse'), 'invalid_quote_report')
    require(isinstance(value.get('review_digest'),str) and re.fullmatch(DIGEST,value['review_digest']), 'invalid_digest')
    require(isinstance(value.get('recipient'),str) and re.fullmatch('0[23][0-9a-f]{64}',value['recipient']), 'invalid_recipient')
    reverse=value['direction']=='reverse'
    price,amount,fee=('xbt_price_sats','btc_amount_msat','xbt_payer_routing_fee_included') if reverse else ('btc_price_sats','xbt_amount_msat','btc_payer_routing_fee_included')
    for key,maximum in ((price,1000000),(amount,1000000000),('expires_at',2**63-1)):
        require(type(value.get(key)) is int and 0 < value[key] <= maximum, 'invalid_quote_amount_or_expiry')
    require(type(value.get('quote_expired')) is bool and value.get('outgoing_route_fee_msat')==0 and
            value.get(fee) is False and value.get('pricing')=='operator_supplied_regtest',
            'invalid_quote_policy')
    result={k:value[k] for k in ('phase','direction','review_digest','recipient',price,amount,
            'expires_at','quote_expired','outgoing_route_fee_msat',fee,'pricing')}
    if 'quote_policy' in value:
        expected=workflow.quote_policy.commitment(value['direction'])
        require(value['quote_policy']==expected,'quote_policy_changed')
        result.update(quote_policy_version=expected['version'],quote_policy_digest=expected['digest'])
    return result


def local_review(manager,job):
    root=existing(manager,job)
    with executor.lock(root):
        _,data,token=workflow.review(root)
        if os.path.lexists(root/'approval.json'):
            require(private_load(root/'approval.json')==dict(digest=token,expires_at=data['terms']['expires_at']),
                    'approval_changed')
        report=filtered(workflow.report(root))
    return dict(report,job=job,regtest_only=True,live_payment_enabled=False,
                restored_block=os.path.lexists(manager/'restored.json'))


def status(manager):
    report=dict(regtest_only=True,live_payment_enabled=False,
                restored_block=os.path.lexists(manager/'restored.json'),quotes=[])
    if not os.path.lexists(manager):return report
    require(manager.is_dir() and not manager.is_symlink(),'invalid_manager')
    if not os.path.lexists(manager/'jobs'):return report
    directory(manager)
    selected=sorted((manager/'jobs').iterdir())
    require(len(selected)<=64,'too_many_jobs')
    for root in selected:
        if not re.fullmatch(JOB,root.name):
            report['quotes'].append(dict(state='unreadable'));continue
        if root.is_dir() and not root.is_symlink() and not os.path.lexists(root/'review.json'):
            continue  # Existing executor-only jobs are reported in Worker Status.
        try: item=dict(local_review(manager,root.name),state='recorded')
        except Exception: item=dict(job=root.name,state='unreadable')
        report['quotes'].append(item)
    return report


def action(manager,mode,request):
    require(mode in ('status','review','prepare','prepare-reverse','approve'),'invalid_action')
    require(isinstance(request,dict),'invalid_request')
    if mode=='status':
        require(request=={},'status_takes_no_parameters')
        return status(manager)
    if mode=='review':
        require(set(request)=={'job'},'invalid_review_request')
        return local_review(manager,request['job'])
    # No UI field or pairing file can opt into execution. On installed live
    # services this fails before node lookup, record creation or any RPC.
    executor.guard()
    if mode=='prepare-reverse':
        require(set(request)=={'job','btcInvoice','xbtSats'},'invalid_prepare_request')
        name(request['job']);directory(manager)
        from reverse_quote_workflow import prepare
        result=prepare(manager,request['job'],dict(connections=private_load(manager/PAIR_FILE),
                       btc_invoice=request['btcInvoice'],xbt_sats=request['xbtSats']))
        return dict(filtered(result),job=request['job'],regtest_only=True,live_payment_enabled=False)
    if mode=='prepare':
        require(set(request)=={'job','xbtInvoice','btcSats'},'invalid_prepare_request')
        name(request['job']);directory(manager)
        invoice=request['xbtInvoice']
        require(isinstance(invoice,str) and 0<len(invoice)<=65536 and invoice.startswith('lnxbtrt')
                and invoice.isascii() and not any(c.isspace() for c in invoice),'regtest_invoice_required')
        require(type(request['btcSats']) is int and 0<request['btcSats']<=1000000,'invalid_price')
        # Fixture-provisioned only, never the installed read-only pairing file.
        connections=private_load(manager/PAIR_FILE)
        result=workflow.prepare(manager,request['job'],dict(connections=connections,
                                xbt_invoice=invoice,btc_sats=request['btcSats']))
        return dict(filtered(result),job=request['job'],regtest_only=True,live_payment_enabled=False)
    require(set(request)=={'job','expectedDigest','confirmed'},'invalid_approval_request')
    require(request['confirmed'] is True,'explicit_confirmation_required')
    require(isinstance(request['expectedDigest'],str) and re.fullmatch(DIGEST,request['expectedDigest']),'invalid_digest')
    existing(manager,request['job'])
    result=workflow.approve(manager,request['job'],request['expectedDigest'],True)
    key,prefix=('xbt_invoice','lnxbtrt') if result['direction']=='reverse' else ('btc_invoice','lnbcrt')
    invoice=result.get(key)
    require(isinstance(invoice,str) and 0<len(invoice)<=65536 and invoice.startswith(prefix) and
            invoice.isascii() and not any(c.isspace() for c in invoice),'invalid_btc_invoice')
    return dict(filtered(result),job=request['job'],regtest_only=True,live_payment_enabled=False,**{key:invoice})


def main(argv=None):
    argv=sys.argv[1:] if argv is None else argv
    os.umask(0o077)
    try:
        require(len(argv)==2,'invalid_arguments')
        raw=sys.stdin.buffer.read(131073);require(len(raw)<=131072,'request_too_large')
        result=action(Path(argv[0]),argv[1],json.loads(raw or b'{}'))
    except Exception:
        reason='regtest_only' if len(argv)==2 and argv[1] in ('prepare','prepare-reverse','approve') and os.environ.get('BTC_XBT_DISPOSABLE_CONTAINER')!='1' else 'inspection_required'
        print(json.dumps(dict(event='quote_action_blocked',reason=reason,details='withheld')));return 1
    print(json.dumps(result));return 0


if __name__=='__main__':raise SystemExit(main())
