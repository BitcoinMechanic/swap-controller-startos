const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  let action, command, options
  let result = { exitCode: 0, stdout: '{"live_ready":false,"gate_activation":"not_verified_with_read_only_credentials","rune":"PRIVATE"}' }
  const sdk = {
    InputSpec: { of: x => x }, Action: { withInput: (id, meta, spec, prefill, handler) => (action = { id, meta, spec, prefill, handler }) },
    SubContainer: { withTemp: async (_e, image, _mounts, _name, fn) => {
      assert.equal(image.imageId, 'controller')
      return fn({ exec: async (cmd, opts) => { command = cmd; options = opts; return result } })
    } },
  }
  const source = ts.transpileModule(fs.readFileSync('startos/actions/livePolicy.ts', 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText
  vm.runInNewContext(source, { exports: {}, require: name => {
    if (name === '../sdk') return { sdk }
    if (name === '../utils') return { mounts: {}, rootDir: '/data' }
    throw new Error('Unexpected module')
  } })
  assert.equal(action.id, 'review-live-policy'); assert.deepEqual(Object.keys(action.spec), [])
  assert.equal(await action.prefill(), undefined)
  const report = await action.handler({ effects: {} })
  assert.deepEqual(Array.from(command), ['python3', '/app/live_policy.py', 'review'])
  assert.equal(options, undefined); assert.equal(JSON.stringify(report).includes('PRIVATE'), false)
  assert.ok(report.message.includes('does not authorize payments'))
  result = { exitCode: 1, stdout: 'PRIVATE' }
  await assert.rejects(action.handler({ effects: {} }), e => !e.message.includes('PRIVATE'))
  result = { exitCode: 1, stdout: '{"error":"PRIVATE"}' }
  await assert.rejects(action.handler({ effects: {} }), e => !e.message.includes('PRIVATE'))
  console.log('Policy review action: no inputs, no credentials, fixed local command and private errors OK')
}
if (require.main === module) module.exports().catch(e => { console.error(e); process.exit(1) })
