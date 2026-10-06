"""Read-only regtest claim verification using authenticated CLN wallet evidence.

Raw transaction links/witnesses are checked locally. Confirmation heights and
wallet ownership are node-reported, not independent chain proofs. No mutations.
"""
import contextlib
import hashlib
from pathlib import Path
import re
import time
from controller import private_load,require,save
from deadline_boundary import validate
from execution_rpc import Remote
from executor import PIN,digest,guard,lock


def sha(value):return hashlib.sha256(value).digest()


class ChainReader(Remote):
    def call(self,method,*args):
        require(method in ('getinfo','listtransactions','listfunds','listsendpays'),'verification_read_only')
        if method=='listsendpays':
            require(len(args)==1 and type(args[0]) is str and re.fullmatch('[0-9a-f]{64}',args[0]),'invalid_hash')
            params=dict(payment_hash=args[0])
        else:
            require(not args,'verification_no_parameters');params={}
        info=self._request('getinfo',{})
        require(info.get('id')==self.node_id and info.get('network')==self.network,'operator_identity_mismatch')
        return info if method=='getinfo' else self._request(method,params)


class Wire:
    def __init__(self,raw):self.raw=raw;self.pos=0
    def take(self,n):
        require(0<=n<=len(self.raw)-self.pos,'truncated_transaction')
        value=self.raw[self.pos:self.pos+n];self.pos+=n;return value
    def num(self,n):return int.from_bytes(self.take(n),'little')
    def varint(self):
        n=self.num(1)
        if n<253:return n
        size={253:2,254:4,255:8}[n];value=self.num(size)
        require(value>={2:253,4:65536,8:4294967296}[size],'noncanonical_varint')
        return value
    def vector(self):return self.take(self.varint())


def transaction(rawhex):
    require(type(rawhex) is str and 20<=len(rawhex)<=8000000 and len(rawhex)%2==0
            and re.fullmatch('[0-9a-fA-F]+',rawhex),'invalid_raw_transaction')
    w=Wire(bytes.fromhex(rawhex));version=w.take(4)
    segwit=w.raw[w.pos:w.pos+2]==b'\x00\x01'
    if segwit:w.take(2)
    start=w.pos;n=w.varint();require(0<n<=10000,'invalid_input_count')
    inputs=[]
    for _ in range(n):
        txid=w.take(32)[::-1].hex();index=w.num(4);w.vector();sequence=w.num(4)
        inputs.append(dict(txid=txid,index=index,sequence=sequence,witness=[]))
    n=w.varint();require(0<n<=10000,'invalid_output_count');outputs=[]
    for _ in range(n):
        amount=w.num(8);require(amount<=2100000000000000,'invalid_output_value')
        outputs.append(dict(amount_msat=amount*1000,script=w.vector()))
    stripped=version+w.raw[start:w.pos]
    if segwit:
        for txin in inputs:
            n=w.varint();require(n<=10000,'invalid_witness_count')
            txin['witness']=[w.vector() for _ in range(n)]
    locktime=w.take(4);require(w.pos==len(w.raw),'trailing_transaction_data')
    return dict(txid=sha(sha(stripped+locktime))[::-1].hex(),version=int.from_bytes(version,'little'),inputs=inputs,outputs=outputs)


def csv_delay(script):
    # Exact non-lease to_local template used by these pinned regtest channels.
    w=Wire(script);require(w.take(2)==b'\x63\x21','unsupported_delayed_script')
    require(w.take(33)[0] in (2,3) and w.take(1)==b'\x67','unsupported_delayed_script')
    op=w.num(1)
    if 81<=op<=96:delay=op-80
    else:
        require(1<=op<=3,'invalid_csv_delay');value=w.take(op)
        require(not value[-1]&128 and (value[-1]!=0 or (len(value)>1 and value[-2]&128)),'noncanonical_csv_delay')
        delay=int.from_bytes(value,'little');require(delay>16,'noncanonical_csv_delay')
    require(1<=delay<=2016 and w.take(3)==b'\xb2\x75\x21','unsupported_delayed_script')
    require(w.take(33)[0] in (2,3) and w.take(2)==b'\x68\xac' and w.pos==len(script),'unsupported_delayed_script')
    return delay


def p2wsh(script,witness):return script==b'\x00\x20'+sha(witness)


def prove(spec,rows,funds,height):
    """No fixture-provided transaction IDs; discover descendants of funding."""
    require(type(height) is int and height>0 and type(rows) is list and len(rows)<=10000 and type(funds) is list,'invalid_chain_evidence')
    confirmed={}
    for row in rows:
        h=row.get('blockheight')
        require(type(h) is int and 0<=h<=height,'invalid_confirmation_height')
        if not h:continue
        tx=transaction(row.get('rawtx'));require(tx['txid']==row.get('hash'),'transaction_hash_mismatch')
        require(tx['txid'] not in confirmed,'duplicate_transaction')
        tx['height']=h;confirmed[tx['txid']]=tx
    def spends(tx,txid,index):return [i for i in tx['inputs'] if i['txid']==txid and i['index']==index]
    pin=spec['channel']
    commits=[t for t in confirmed.values() if spends(t,pin['funding_txid'],pin['funding_outnum'])]
    require(len(commits)<=1,'ambiguous_commitment')
    if not commits:return dict(phase='awaiting_commitment',verified=False)
    commit=commits[0];successes=[]
    for n,output in enumerate(commit['outputs']):
        if output['amount_msat']!=spec['incoming_amount_msat']:continue
        for tx in confirmed.values():
            for txin in spends(tx,commit['txid'],n):
                witness=txin['witness']
                require(len(witness)==5 and witness[0]==b'' and len(witness[-2])==32,'not_htlc_success')
                require(sha(witness[-2]).hex()==spec['payment_hash'] and p2wsh(output['script'],witness[-1]),'htlc_witness_mismatch')
                require(b'\xa9\x14'+hashlib.new('ripemd160',bytes.fromhex(spec['payment_hash'])).digest() in witness[-1],'htlc_script_hash_mismatch')
                require(commit['height']<=tx['height']<spec['expiry'],'late_htlc_claim')
                successes.append(tx)
    require(len(successes)<=1,'ambiguous_htlc_claim')
    if not successes:return dict(phase='awaiting_htlc_success',verified=False)
    success=successes[0];sweeps=[]
    for n,output in enumerate(success['outputs']):
        if not spec['incoming_amount_msat']*8//10<output['amount_msat']<=spec['incoming_amount_msat']:continue
        for tx in confirmed.values():
            for txin in spends(tx,success['txid'],n):
                witness=txin['witness']
                require(len(witness)==3 and witness[1]==b'' and p2wsh(output['script'],witness[-1]),'delayed_witness_mismatch')
                delay=csv_delay(witness[-1]);sequence=txin['sequence']
                require(tx['version']>=2 and not sequence&((1<<31)|(1<<22)) and (sequence&65535)>=delay,'invalid_csv_sequence')
                require(tx['height']-success['height']>=delay,'premature_csv_sweep')
                owned=[]
                for f in funds:
                    if f.get('txid')!=tx['txid'] or f.get('status')!='confirmed':continue
                    index=f.get('output');require(type(index) is int and 0<=index<len(tx['outputs']),'invalid_wallet_outpoint')
                    o=tx['outputs'][index]
                    require(type(f.get('amount_msat')) is int and f['amount_msat']==o['amount_msat'] and
                            f.get('scriptpubkey')==o['script'].hex() and f.get('blockheight')==tx['height'],'wallet_output_mismatch')
                    require(index not in owned,'duplicate_wallet_output');owned.append(index)
                require(sum(tx['outputs'][i]['amount_msat'] for i in owned)>output['amount_msat']*8//10,'wallet_sweep_not_confirmed')
                sweeps.append(dict(commitment=commit['txid'],success=success['txid'],sweep=tx['txid'],csv_delay=delay,
                                   heights=[commit['height'],success['height'],tx['height']],wallet_outputs=owned))
    require(len(sweeps)<=1,'ambiguous_csv_sweep')
    if not sweeps:return dict(phase='awaiting_csv_sweep',verified=False)
    return dict(phase='claim_and_sweep_verified',verified=True,proof=sweeps[0])


def report(result):
    return dict(result,regtest_only=True,read_only=True,
                confirmation_source='paired_cln',independent_chain_proof=False)


@contextlib.contextmanager
def inspection(root):
    # Invalidate durably before configuration, validation or network work.
    # Abrupt exit leaves in_progress, never an earlier success/proof.
    guard()
    with lock(root):
        path=root/'chain-verification.json'
        save(path,report(dict(phase='verification_in_progress',verified=False)))
        try:yield
        except Exception:
            save(path,report(dict(phase='verification_unavailable',verified=False)))
            raise ValueError('verification_unavailable') from None


def inspect_config(root,load_request):
    """Load private request and construct read-only clients after invalidation."""
    with inspection(root):
        request=load_request();clients={}
        for config in request['connections']:
            role={'regtest':'btc','xbt-regtest':'xbt'}[config['network']]
            require(role not in clients,'duplicate_node')
            clients[role]=ChainReader(config)
        return _inspect(root,request['spec'],clients)


def inspect(root,spec,clients,*,clock=time.monotonic):
    with inspection(root):return _inspect(root,spec,clients,clock=clock)


def _inspect(root,spec,clients,*,clock=time.monotonic):
    validate(spec)
    incoming='btc' if spec['direction']=='forward' else 'xbt';outgoing='xbt' if incoming=='btc' else 'btc'
    require(set(clients)=={'btc','xbt'},'two_nodes_required')
    record=private_load(root/'deadline.json');receipt=private_load(root/'claim-receipt.json')
    require(record['source_commit']==PIN and digest(record['spec'])==digest(spec) and
            receipt.get('spec_digest')==digest(spec) and receipt.get('stage')=='gate_resolved','resolved_claim_record_required')
    start=clock()
    def call(role,method,*args):
        require(0<=clock()-start<=120,'verification_expired');result=clients[role].call(method,*args)
        require(0<=clock()-start<=120,'verification_expired');return result
    infos={role:call(role,'getinfo') for role in ('btc','xbt')}
    for role,network in (('btc','regtest'),('xbt','xbt-regtest')):
        require(infos[role].get('id')==spec['node_ids'][role] and infos[role].get('network')==network
                and not any(k.startswith('warning') for k in infos[role]),'verification_identity_mismatch')
    payments=call(outgoing,'listsendpays',spec['payment_hash'])['payments']
    require(len(payments)==1,'original_attempt_required');p=dict(payments[0]);p.setdefault('partid',0)
    require(receipt['attempt'].get('groupid')==spec['groupid'] and receipt['attempt'].get('partid')==spec['partid'],'attempt_binding_changed')
    expected=dict(receipt['attempt'],payment_hash=spec['payment_hash'],amount_sent_msat=spec['outgoing_amount_msat'],status='complete')
    require(all(p.get(k)==v and type(p.get(k)) is type(v) for k,v in expected.items()),'outgoing_attempt_changed')
    rows=call(incoming,'listtransactions')['transactions'];funds=call(incoming,'listfunds')['outputs']
    height=infos[incoming]['blockheight'];result=prove(spec,rows,funds,height)
    fresh=call(incoming,'getinfo');require(fresh.get('blockheight')==height,'chain_advanced_retry_inspection')
    result=report(result)
    save(root/'chain-verification.json',result)
    return result
