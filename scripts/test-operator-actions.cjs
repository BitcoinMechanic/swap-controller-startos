const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),ts=require('typescript')
module.exports=async function(){
 const actions={},cache=new Map();let pilot=false,jobs=false,unreadable=false,command,options,exitCode=0,report
 const sdk={volumes:{main:{subpath:p=>'/volume/'+p}},Value:Object.fromEntries(['text','textarea','toggle','number','select'].map(k=>[k,x=>x])),InputSpec:{of:x=>x},
  Action:{withInput:(id,meta,spec,prefill,handler)=>{const a={id,meta,spec,prefill,handler};actions[id]=a;return a}},
  Actions:{of:()=>({addAction(){return this}})},
  SubContainer:{withTemp:async(e,i,m,n,f)=>f({exec:async(c,o)=>{command=c;options=o;return {exitCode,stdout:JSON.stringify(report)}}})}}
 const notFound=()=>{const error=new Error('PRIVATE');error.code='ENOENT';throw error}
 function load(file){file=path.resolve(file);if(cache.has(file))return cache.get(file);const exports={};cache.set(file,exports)
  const source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
  vm.runInNewContext(source,{exports,require:n=>{
   if(n==='../sdk')return {sdk};if(n==='../i18n')return {i18n:s=>s};if(n==='../utils')return {mounts:{},rootDir:'/data'}
   if(n==='fs/promises')return {lstat:async p=>{assert.equal(p,'/volume/execution/forward-pilot/record.json');if(unreadable)throw Error('PRIVATE');if(!pilot)notFound();return {}},readdir:async p=>{assert.equal(p,'/volume/execution/jobs');if(unreadable)throw Error('PRIVATE');return jobs?['job']:[]}}
   if(n.startsWith('./'))return load(path.join(path.dirname(file),n+'.ts'));throw Error('Unexpected import '+n)
  }});return exports
 }
 load('startos/actions/index.ts')
 const hidden=['prepare-forward-pilot','approve-forward-pilot','forward-pilot-status','find-swap-channels','prepare-forward-swap','prepare-swap-quote','approve-swap-quote','prepare-reverse-swap-quote','quote-status','review-swap-quote','recovery-status','confirm-recovery-revocation','recover-existing-swap','live-readiness','review-live-policy','inspect-live-forward','inspect-live-reverse','pair-btc-gate-observation','pair-xbt-gate-observation']
 for(const id of hidden)assert.equal((await actions[id].meta({effects:{}})).visibility,'hidden',id)
 const expected={'swap-status':'Swaps','new-forward-swap':'Swaps','new-reverse-swap':'Swaps','confirm-forward-swap':'Swaps','confirm-reverse-swap':'Swaps','pair-swap-grants':'Swap Setup','pair-reverse-grants':'Swap Setup','save-swap-inspection':'Swap Setup','pair-nodes':'Swap Setup','connection-status':'Status','worker-status':'Advanced / Recovery'}
 for(const [id,group] of Object.entries(expected)){const m=await actions[id].meta({effects:{}});assert.equal(m.visibility,'enabled',id);assert.equal(m.group,group,id)}
 assert.equal(Object.keys(actions).length-hidden.length,15)
 assert.equal((await actions['confirm-forward-swap'].meta({effects:{}})).name,'Confirm BTC to XBT Swap')
 pilot=jobs=true
 for(const id of ['forward-pilot-status','approve-forward-pilot','quote-status','review-swap-quote','recovery-status'])assert.equal((await actions[id].meta({effects:{}})).visibility,'enabled',id)
 for(const id of ['prepare-forward-pilot','prepare-swap-quote','recover-existing-swap'])assert.equal((await actions[id].meta({effects:{}})).visibility,'hidden',id)
 unreadable=true
 assert.equal((await actions['forward-pilot-status'].meta({effects:{}})).visibility,'enabled')
 // Render only fixed status fields; backend strings and secret extras cannot become UI text.
 report={read_only:true,checked_at:2000000000,pairing:'ready',worker:'fresh',inspection:'saved',rune:'PRIVATE',directions:{forward:{state:'ready_to_prepare',active:0,blockers:['PRIVATE_RPC'],nodes:{btc:{state:'available',remaining:5,expires_at:2000000100,routed:true,max_fee_msat:10000,max_delay_blocks:80,max_hops:4,blockers:['grant_paused'],rune:'PRIVATE'}}},reverse:{state:'blocked',active:1,blockers:[],nodes:{}}}}
 const status=await actions['swap-status'].handler({effects:{}})
 assert.deepEqual(Array.from(command),['python3','/app/swap_status.py','/data']);assert.equal(options,undefined)
 assert.ok(!JSON.stringify(status).includes('PRIVATE'));assert.ok(JSON.stringify(status).includes('UTC'))
 report={read_only:true,worker:'PRIVATE',directions:{forward:{active:'PRIVATE',state:'PRIVATE',blockers:['PRIVATE'],nodes:{btc:{state:'available',remaining:'PRIVATE',expires_at:'PRIVATE',max_fee_msat:'PRIVATE',max_delay_blocks:'PRIVATE',max_hops:'PRIVATE'}}}}}
 assert.ok(!JSON.stringify(await actions['swap-status'].handler({effects:{}})).includes('PRIVATE'))
 report={read_only:true,pairing:'__proto__',inspection:'constructor',worker:'toString',directions:{forward:{state:'__proto__',active:0,blockers:['constructor'],nodes:{}}}}
 const unknown=await actions['swap-status'].handler({effects:{}})
 for(const row of unknown.result.value)assert.equal(typeof row.value,'string')
 exitCode=1;report={reason:'PRIVATE_RPC'}
 await assert.rejects(actions['swap-status'].handler({effects:{}}),e=>!e.message.includes('PRIVATE'))
 for(const id of ['new-forward-swap','new-reverse-swap']){
  for(const [reason,text] of [['confirmed_reserve_insufficient','50,000'],['worker_heartbeat_required','heartbeat'],['session_rpc_refused_or_uncertain','Swap Status']]){
   report={reason};await assert.rejects(actions[id].handler({effects:{},input:{invoice:'PRIVATE'}}),e=>e.message.includes(text))
  }
  report={reason:'PRIVATE_RAW_ERROR'};await assert.rejects(actions[id].handler({effects:{},input:{}}),e=>!e.message.includes('PRIVATE'))
 }
 for(const [reason,fragments] of [
  ['bounded_route_unavailable',['10 XBT sats','80 blocks','4 hops','private routing hints','do not guarantee']],
  ['route_planning_refused',['XBT grant','Swap Status']],
  ['invalid_recipient_invoice',['2,000-sat XBT','40 blocks']],
  ['route_outside_grant',['outside the saved grant']],
 ]){
  report={reason,private:'PRIVATE'}
  await assert.rejects(actions['new-forward-swap'].handler({effects:{},input:{invoice:'PRIVATE'}}),e=>fragments.every(s=>e.message.includes(s))&&!e.message.includes('PRIVATE'))
 }
 for(const reason of ['__proto__','constructor','bounded_route_unavailable PRIVATE']){
  report={reason};await assert.rejects(actions['new-forward-swap'].handler({effects:{},input:{}}),e=>e.message.includes('private details withheld')&&!e.message.includes('PRIVATE'))
 }
 console.log('Operator menus: 15 normal actions, conditional legacy access, retained hidden fixture IDs, read-only status rendering and safe errors OK')
}
if(require.main===module)module.exports().catch(e=>{console.error(e);process.exit(1)})
