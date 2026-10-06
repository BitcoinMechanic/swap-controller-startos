const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  let action, command, options
  let result = { exitCode: 0, stdout: '{"private":"PRIVATE"}' }
  const sdk = {
    Value: { text: x => x, toggle: x => x }, InputSpec: { of: x => x },
    Action: { withInput: (id, meta, spec, prefill, handler) => (action = { id, meta, spec, prefill, handler }) },
    SubContainer: { withTemp: async (_e, image, _mounts, _name, fn) => {
      assert.equal(image.imageId, 'controller')
      return fn({ exec: async (cmd, opts) => { command = cmd; options = opts; return result } })
    } },
  }
  for (const role of ['btc','xbt']) {
  const source = ts.transpileModule(fs.readFileSync(role==='btc'?'startos/actions/gateObservation.ts':'startos/actions/xbtGateObservation.ts', 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  vm.runInNewContext(source, { exports: {}, require: name => {
    if (name === '../sdk') return { sdk }
    if (name === '../utils') return { mounts: {}, rootDir: '/data' }
    throw new Error('Unexpected module')
  } })
  assert.equal(action.id, 'pair-'+role+'-gate-observation')
  assert.deepEqual(Object.keys(action.spec), ['rune','confirmed'])
  assert.equal(action.spec.rune.masked, true);assert.equal(action.spec.confirmed.default, false)
  const request={effects:{},input:{rune:' PRIVATE ',confirmed:true}}
  const report=await action.handler(request)
  assert.deepEqual(Array.from(command),['python3','/app/gate_observation.py','/data'])
  assert.deepEqual(JSON.parse(options.input),role==='btc'?{rune:'PRIVATE',confirmed:true}:{rune:'PRIVATE',confirmed:true,role:'xbt'})
  assert.equal(JSON.stringify(report).includes('PRIVATE'),false)
  result={exitCode:1,stdout:'PRIVATE'}
  await assert.rejects(action.handler(request), e=>!e.message.includes('PRIVATE'))
  result={exitCode:0,stdout:'{}'}
  }
  console.log('BTC/XBT gate observation action: masked credential, private stdin, fixed command and private errors OK')
}
if (require.main===module) module.exports().catch(e=>{console.error(e);process.exit(1)})
