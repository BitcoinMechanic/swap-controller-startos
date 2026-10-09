const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),ts=require('typescript')
module.exports=async function(){
 const actions=[];let cmd,input
 let report={pilot_id:'a'.repeat(64),phase:'review',btc_sats:1000,xbt_sats:2000,rune:'SECRET'}
 let exitCode=0
 const sdk={Value:{text:x=>x,toggle:x=>x},InputSpec:{of:x=>x},Action:{withInput:(id,meta,spec,prefill,handler)=>{const a={id,meta,spec,prefill,handler};actions.push(a);return a}},SubContainer:{withTemp:async(e,i,m,n,f)=>f({exec:async(c,o)=>{cmd=c;input=JSON.parse(o.input);return {exitCode,stdout:JSON.stringify(report)}}})}}
 const source=ts.transpileModule(fs.readFileSync('startos/actions/reverseSwaps.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
 vm.runInNewContext(source,{exports:{},require:n=>n==='../sdk'?{sdk}:{mounts:{},rootDir:'/data'}})
 assert.deepEqual(actions.map(a=>a.id),['pair-reverse-grants','new-reverse-swap','confirm-reverse-swap','reverse-swap-status','cancel-reverse-draft'])
 assert.equal(actions[0].spec.btcCredential.masked,true);assert.equal(actions[0].spec.xbtCredential.masked,true)
 assert.deepEqual(Object.keys(actions[1].spec),['invoice']);assert.equal(actions[2].spec.confirmed.default,false)
 report={pilotId:'b'.repeat(64),confirmed:true}
 const prefilled=await actions[2].prefill({effects:{}})
 assert.equal(prefilled.pilotId,'b'.repeat(64));assert.equal(prefilled.confirmed,false)
 assert.equal(cmd.at(-1),'approval-input')
 report={pilot_id:'b'.repeat(64),phase:'waiting_for_xbt',invoice:'BTC_INVOICE',rune:'SECRET'}
 const result=await actions[2].handler({effects:{},input:{pilotId:prefilled.pilotId,confirmed:true}})
 assert.deepEqual(Array.from(cmd),['python3','/app/reverse_swaps.py','/data','approve'])
 assert.equal(input.pilotId,prefilled.pilotId);assert.ok(!JSON.stringify(result).includes('SECRET'))
 assert.equal(result.result.value.find(r=>r.name==='XBT invoice to pay').qr,true)
 exitCode=1;report={reason:'bounded_route_unavailable'}
 await assert.rejects(actions[1].handler({effects:{},input:{invoice:'PRIVATE'}}),e=>e.message.includes('288 blocks'))
 for(const limit of [80,144,288]) {
  report={reason:'bounded_route_unavailable_'+limit}
  await assert.rejects(actions[1].handler({effects:{},input:{invoice:'PRIVATE'}}),e=>e.message.includes(limit+' blocks maximum') && !e.message.includes('PRIVATE'))
 }
 report={reason:'PRIVATE_RPC_URL'}
 await assert.rejects(actions[2].handler({effects:{},input:{}}),e=>!e.message.includes('PRIVATE_RPC_URL'))
 console.log('Repeat swap forms: invoice-only preparation, prefilled exact approval, false confirmation default, private credentials and invoice QR OK')
}
if(require.main===module)module.exports().catch(e=>{console.error(e);process.exit(1)})
