const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  const actions=[];let command,options
  let reply={exitCode:0,stdout:'{"preflight_matches":false,"execution_authorized":false,"reasons":["pair_nodes_first"],"rune":"SECRET"}'}
  const sdk={Value:{text:x=>x,number:x=>x},InputSpec:{of:x=>x},
    Action:{withInput:(id,meta,spec,prefill,handler)=>{const a={id,meta,spec,prefill,handler};actions.push(a);return a}},
    SubContainer:{withTemp:async(e,image,m,n,fn)=>{assert.equal(image.imageId,'controller');return fn({exec:async(cmd,opts)=>{command=cmd;options=opts;return reply}})}}}
  const source=ts.transpileModule(fs.readFileSync('startos/actions/preflight.ts','utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
  vm.runInNewContext(source,{exports:{},require:n=>{
    if(n==='../sdk')return {sdk};if(n==='../utils')return {mounts:{},rootDir:'/data'};throw Error(n)
  }})
  assert.deepEqual(actions.map(a=>a.id),['inspect-live-forward','inspect-live-reverse'])
  for(const [i,a] of actions.entries()){
    for(const key of ['btcRune','xbtRune','invoice'])assert.equal(a.spec[key].masked,true)
    assert.equal(await a.prefill(),undefined)
    const input={invoice:'SECRET',btcRune:'RUNE1',xbtRune:'RUNE2'}
    const result=await a.handler({effects:{},input})
    assert.deepEqual(Array.from(command),['python3','/app/preflight_actions.py','/data',i?'reverse':'forward'])
    assert.deepEqual(JSON.parse(options.input),input)
    assert.equal(JSON.stringify(command).includes('SECRET'),false)
    assert.equal(JSON.stringify(result).includes('SECRET'),false)
    assert.ok(JSON.stringify(result).includes('pair_nodes_first'))
  }
  reply={exitCode:1,stdout:'SECRET'}
  await assert.rejects(actions[0].handler({effects:{},input:{}}),e=>!e.message.includes('SECRET'))
  console.log('Preflight actions: masked per-run credentials, stdin only, rejected reports and no authority OK')
}
if(require.main===module)module.exports().catch(e=>{console.error(e);process.exit(1)})
