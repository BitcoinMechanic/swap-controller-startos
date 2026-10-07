const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  const actions=[];let command, options
  let reply={exitCode:0,stdout:JSON.stringify({setup_saved:true,rune:'DO_NOT_DISPLAY'})}
  const sdk={Value:{text:x=>x,toggle:x=>x},InputSpec:{of:x=>x},
    Action:{withInput:(id,meta,spec,prefill,handler)=>{const a={id,meta,spec,handler};actions.push(a);return a}},
    SubContainer:{withTemp:async(e,i,m,n,fn)=>fn({exec:async(c,o)=>{command=c;options=o;return reply}})}}
  let delegated
  const source=ts.transpileModule(fs.readFileSync('startos/actions/swapSetup.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
  vm.runInNewContext(source,{exports:{},require:n=>n==='../sdk'?{sdk}:n==='./forwardPilot'?{invoke:async(...args)=>{delegated=args;return {}}}:{mounts:{},rootDir:'/data'}})
  assert.deepEqual(actions.map(a=>a.id),['save-swap-inspection','find-swap-channels','prepare-forward-swap'])
  assert.equal(actions[0].spec.confirmed.default,false)
  for(const key of ['btcRune','xbtRune'])assert.equal(actions[0].spec[key].masked,true)
  const input={btcRune:'PRIVATE',xbtRune:'SECRET',confirmed:true}
  const result=await actions[0].handler({effects:{},input})
  assert.deepEqual(Array.from(command),['python3','/app/swap_setup.py','/data','configure'])
  assert.equal(JSON.stringify(command).includes('PRIVATE'),false)
  assert.deepEqual(JSON.parse(options.input),input)
  assert.equal(JSON.stringify(result).includes('DO_NOT_DISPLAY'),false)
  assert.equal(actions[2].spec.incomingChannel.required,false)
  assert.equal(actions[2].spec.outgoingChannel.required,false)
  await actions[2].handler({effects:{},input:{invoice:'test'}})
  assert.equal(delegated[1],'prepare');assert.equal(delegated[3],'/app/swap_setup.py')
  reply={exitCode:1,stdout:JSON.stringify({reason:'PRIVATE_URL_AND_RUNE'})}
  await assert.rejects(actions[0].handler({effects:{},input}),e=>!e.message.includes('PRIVATE_URL_AND_RUNE'))
  console.log('Swap setup actions: private stdin, masked credentials, optional channels and filtered errors OK')
}
if(require.main===module)module.exports().catch(e=>{console.error(e);process.exit(1)})
