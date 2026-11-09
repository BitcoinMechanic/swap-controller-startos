/* Execute compiled real SDK forms/handlers. No Lightning or TLS substitution here. */
const assert = require('node:assert/strict')
const fs = require('node:fs')

// Runtime paths are fixture-owned transport mapping, never form input. Only the
// exported StartOS invoke functions construct the commands and private request.
async function execute(moduleFile, id, input, dispatch, prefillOnly=false) {
  const mod=require(moduleFile),calls=[],effects={eventId:'fixture-'+process.pid}
  const actions=mod.createActions(async (_effects,request)=>{
    calls.push(request);return dispatch(request)
  })
  const action=actions.get(id);assert.ok(action,'unknown registered action')
  const form=await action.getInput({effects})
  if(prefillOnly)return {form,calls}
  const output=await action.run({effects,input})
  return {output,form,calls}
}

async function tests(files) {
  const cid='a'.repeat(64)
  for(const [asset,file] of Object.entries(files)) {
    const mod=require(file)
    const privateRequest={action:'prepare',invoice:'PRIVATE_INVOICE'}
    let sent=[]
    await mod.executeRequest(privateRequest,async(argv,options)=>{
      sent.push({argv,options});return {exitCode:0,stdout:JSON.stringify(asset==='controller'?{result:{}}:{})}
    })
    assert.deepEqual(sent[0].argv,asset==='controller'?[mod.entrypoint,'--mainnet',mod.directory,'action']:mod.adminCommand)
    assert.deepEqual(JSON.parse(sent[0].options.input),privateRequest)
    assert.ok(!JSON.stringify(sent[0].argv).includes('PRIVATE_INVOICE'))
    for(const exec of [async()=>{throw Error('PRIVATE_ERROR')},async()=>({exitCode:1,stdout:'PRIVATE_ERROR'}),
      async()=>({exitCode:1,stdout:JSON.stringify({reason:'PRIVATE_ERROR'})})]) {
      await assert.rejects(mod.executeRequest({action:'status'},exec),e=>!e.message.includes('PRIVATE_ERROR'))
    }
    if(asset==='controller') {
      for(const [reason,expected] of [['recipient_route_unknown','route hints'],['recipient_invoice_cltv_limit','576 blocks'],['recipient_invoice_timing','fresh invoice'],['outgoing_channel_not_ready','disconnected'],['worker_invoice_unavailable','still being prepared'],['contract_reserve_insufficient','25,000 sats'],['service_lock_timeout','action did not start'],['market_amount_limit','1,000,000 XBT'],['market_depth_unavailable','order-book liquidity'],['market_data_invalid','data failed validation']]) {
        await assert.rejects(mod.executeRequest({action:'prepare'},async()=>({exitCode:1,stdout:JSON.stringify({reason})})),e=>e.message.includes(expected))
      }
      await assert.rejects(mod.executeRequest({action:'prepare'},async()=>({exitCode:1,stdout:JSON.stringify({reason:'node_timeout',diagnostic:{node:'btc-cln-v1',operation:'plan',stage:'response_headers',reference:'1234567890abcdef',locations:['service.py:123'],http_status:503,url:'PRIVATE_URL',rune:'PRIVATE_RUNE'}})})),e=>e.message.includes('15-second') && e.message.includes('plan') && e.message.includes('503') && e.message.includes('service.py:123') && !e.message.includes('PRIVATE'))
      await assert.rejects(mod.executeRequest({action:'prepare'},async()=>({exitCode:1,stdout:JSON.stringify({reason:'incoming_amount_below_safe_minimum',diagnostic:{node:'btc-cln-v1',operation:'inspect',amount_msat:39000,minimum_msat:546001}})})),e=>e.message.includes('39 sats') && e.message.includes('547 whole sats') && e.message.includes('too small'))
      let res=await execute(file,'migrated-new-forward',{invoice:'private-invoice'},async r=>{assert.equal(r.action,'prepare');return {confirmation:cid,direction:'forward',btc_msat:1,xbt_msat:2,rune:'SECRET',url:'SECRET'}})
      assert.deepEqual(Object.keys(res.form.spec),['invoice']);assert.ok(!JSON.stringify(res.output).includes('SECRET'))
      assert.equal(res.form.spec.invoice.masked,true)
      const status=()=>({swaps:[{confirmation:cid,direction:'forward',state:'ready_for_confirmation'}]})
      res=await execute(file,'migrated-confirm-forward',{},status,true)
      assert.equal(res.form.value.cid,cid);assert.equal(res.form.value.confirmed,false)
      await assert.rejects(execute(file,'migrated-confirm-forward',{cid,confirmed:false},async r=>{assert.equal(r.action,'status');return status()}))
      res=await execute(file,'migrated-confirm-forward',{cid,confirmed:true},async r=>r.action==='status'?status():{confirmation:cid})
      assert.deepEqual(res.calls.at(-1),{action:'confirm',cid,confirmation:cid,confirmed:true})
      await assert.rejects(execute(file,'migrated-confirm-forward',{cid,confirmed:true},async r=>{
        assert.equal(r.action,'status');return {swaps:[{confirmation:cid,direction:'reverse'}]}
      }))
      await assert.rejects(execute(file,'migrated-initialize',{confirmed:false},async()=>assert.fail('unconfirmed initialize dispatched')))
      const bolt='lnbc20n1'+ 'q'.repeat(120)
      res=await execute(file,'migrated-invoice',{cid},async r=>({invoice:bolt,rune:'SECRET'}))
      const row=res.output.result.value.find(r=>r.name==='Payment invoice');assert.equal(row.value,bolt);assert.equal(row.qr,true);assert.equal(row.masked,true)
      await assert.rejects(execute(file,'migrated-settings',{paused:false,markupBps:501},async()=>{throw Error('not reached')}))
      res=await execute(file,'migrated-settings',{paused:true,markupBps:0},async r=>r)
      assert.deepEqual(res.calls.at(-1),{action:'configure',paused:true,markup_bps:0})
      for(const poison of ['SECRET','__proto__','constructor'])assert.ok(!JSON.stringify(mod.display({state:poison,confirmation:poison,btc_msat:poison,invoice:poison,rune:poison})).includes(poison))
      assert.ok(mod.command.includes('--mainnet'));assert.equal(mod.command.at(-1),'run');assert.ok(!mod.command.includes('init'))
    } else {
      await assert.rejects(execute(file,'migrated-admin-init',{confirmed:false},async()=>assert.fail('unconfirmed initialize dispatched')))
      const grant={chain:{profile:mod.profile,environment:'mainnet'}},rune='A'.repeat(50)
      sent=[]
      const credential=await mod.executeRequest({action:'credential',grant_id:cid,confirmed:true},async(argv,options)=>{
        assert.equal(options,undefined);sent.push(argv)
        return {exitCode:0,stdout:JSON.stringify(argv[6]==='swap-customer-credential'?{rune}:{regtest_only:false,grant})}
      })
      assert.deepEqual(credential,{credential:{grant,rune}})
      assert.deepEqual(sent.map(a=>a[6]),['swap-customer-credential','swap-customer-call'])
      assert.ok(sent.every(a=>a[2]==='--network=bitcoin'))
      const visible=mod.display(credential).result.value[0]
      assert.equal(visible.masked,true);assert.equal(visible.copyable,true)
      await assert.rejects(mod.executeRequest({action:'credential',grant_id:cid,confirmed:false},async()=>assert.fail('credential dispatched')))
      await assert.rejects(mod.executeRequest({action:'credential',grant_id:cid,confirmed:true},async argv=>({exitCode:0,
        stdout:JSON.stringify(argv[6]==='swap-customer-credential'?{rune}:{regtest_only:false,grant:{chain:{profile:mod.profile,environment:'regtest'}}})})))
      sent=[]
      await mod.executeRequest({action:'setup',confirmed:true},async (argv,options)=> {
        sent.push({argv,options});return {exitCode:0,stdout:JSON.stringify({initialized:true,restart_required:true})}
      })
      assert.deepEqual(sent[0].argv,['/usr/local/libexec/swap-node/package_node.py','--mainnet','--root','/root/.lightning','--profile',mod.profile,'prepare'])
      assert.deepEqual(JSON.parse(sent[0].options.input),{confirmed:true})
      for(const kind of ['incoming','outgoing']) {
        const input={maxSwaps:4,maxAmount:50000000,totalAmount:150000000,lifetime:86400,
          ...(kind==='incoming'?{minDelay:350,maxDelay:2016}:{maxFee:10000,maxDelay:288,maxHops:4})}
        const res=await execute(file,'migrated-review-'+kind,input,async r=>({confirmation:cid,kind,phase:'review',limits:r.limits,channel_count:1,rune:'SECRET'}))
        assert.ok(!Object.keys(res.form.spec).some(k=>/scid|channel/i.test(k)));assert.ok(!JSON.stringify(res.output).includes('SECRET'))
        assert.equal(res.calls[0].limits[kind==='incoming'?'close_before_blocks':'max_fee_msat'],kind==='incoming'?72:10000)
      }
      for(const limits of [{maxFee:10001,maxDelay:288,maxHops:4},{maxFee:10000,maxDelay:289,maxHops:4},{maxFee:10000,maxDelay:288,maxHops:5}]) {
        await assert.rejects(execute(file,'migrated-review-outgoing',{maxSwaps:4,maxAmount:10000000,totalAmount:30000000,lifetime:86400,...limits},async()=>assert.fail('outside runtime bounds dispatched')))
      }
      await assert.rejects(execute(file,'migrated-grant-confirm',{confirmation:cid,confirmed:false},async()=>{throw Error('not reached')}))
      assert.ok(mod.adminCommand.includes('--mainnet'));assert.ok(mod.directory.endsWith('/bitcoin'))
    }
  }
  console.log('PASS: real SDK forms, exact confirmation, default-off approval, masked credentials/invoices, bounded settings, no SCID input and fixed daemon command')
}
module.exports={execute,tests}
if(require.main===module)tests(JSON.parse(fs.readFileSync(process.argv[2],'utf8'))).catch(e=>{console.error(e);process.exit(1)})
