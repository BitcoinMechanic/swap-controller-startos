const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  const actions = []; let command, options
  let reply = { exitCode: 0, stdout: '{"pilot_id":"ID","phase":"review","rune":"MUST_NOT_LEAK"}' }
  const sdk = { Value: { text: x => x, toggle: x => x }, InputSpec: { of: x => x },
    Action: { withInput: (id,meta,spec,prefill,handler) => { const a={id,meta,spec,prefill,handler};actions.push(a);return a } },
    SubContainer: { withTemp: async(e,i,m,n,fn) => fn({exec: async(c,o) => {command=c;options=o;return reply}}) } }
  const source = ts.transpileModule(fs.readFileSync('startos/actions/forwardPilot.ts','utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText
  vm.runInNewContext(source,{exports:{},require:n=>n==='../sdk'?{sdk}:{mounts:{},rootDir:'/data'}})
  assert.deepEqual(actions.map(a=>a.id),['prepare-forward-pilot','approve-forward-pilot','forward-pilot-status'])
  assert.equal(actions[1].spec.confirmed.default,false)
  for (const a of actions.slice(0,2)) for (const key of ['btcRune','xbtRune']) assert.equal(a.spec[key].masked,true)
  for (const [index,a] of actions.entries()) {
    const input={btcRune:'PRIVATE_A',xbtRune:'PRIVATE_B'}
    const result=await a.handler({effects:{},input})
    assert.deepEqual(Array.from(command),['python3','/app/forward_pilot.py','/data',['prepare','approve','status'][index]])
    assert.ok(!JSON.stringify(command).includes('PRIVATE'))
    assert.ok(!JSON.stringify(result).includes('MUST_NOT_LEAK'))
    if(index<2)assert.deepEqual(JSON.parse(options.input),input)
  }
  reply={exitCode:1,stdout:'PRIVATE_ERROR'}
  await assert.rejects(actions[1].handler({effects:{},input:{}}),e=>!e.message.includes('PRIVATE_ERROR'))
  console.log('Forward pilot actions: explicit approval, masked credentials, fixed commands and filtered results OK')
}
if(require.main===module)module.exports().catch(e=>{console.error(e);process.exit(1)})
