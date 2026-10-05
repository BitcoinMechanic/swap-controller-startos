const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  const registered = {}, calls = []
  let result = { exitCode: 0, stdout: '{"live_payment_enabled":false,"jobs":[]}' }
  const sdk = {
    Value: { text: x => x, select: x => x, toggle: x => x },
    InputSpec: { of: x => x },
    Action: { withInput: (id, meta, spec, prefill, handler) => (registered[id] = { meta, spec, prefill, handler }) },
    SubContainer: { withTemp: async (_e, image, mounts, name, fn) => {
      assert.deepEqual(JSON.parse(JSON.stringify(image)), { imageId: 'controller' })
      return fn({ exec: async (command, options) => { calls.push({ command, options }); return result } })
    } },
  }
  const code = ts.transpileModule(fs.readFileSync('startos/actions/recovery.ts', 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(code, { exports: {}, require: name => {
    if (name === '../sdk') return { sdk }
    if (name === '../utils') return { mounts: {}, rootDir: '/data' }
    throw new Error('Unexpected module')
  } })
  const ids = ['recovery-status', 'confirm-recovery-revocation', 'recover-existing-swap']
  assert.deepEqual(Object.keys(registered), ids)
  for (const id of ids) {
    const action = registered[id]
    assert.equal((await action.meta()).group, 'Recovery')
    assert.equal(await action.prefill(), undefined)
    if (id !== 'recovery-status') {
      assert.equal(action.spec.btcRune.masked, true)
      assert.equal(action.spec.xbtRune.masked, true)
      assert.equal(action.spec.confirmed.default, false)
    }
  }
  const input = { job: 'swap', expectedDigest: 'a'.repeat(64), btcRune: 'SECRET-BTC', xbtRune: 'SECRET-XBT', confirmed: true }
  for (const [index, id] of ids.entries()) {
    await registered[id].handler({ effects: {}, input: index === 1 ? { ...input, network: 'regtest' } : input })
    assert.deepEqual(Array.from(calls[index].command), ['python3', '/app/recovery_actions.py', '/data/execution', ['status', 'confirm', 'recover'][index]])
    assert.equal(JSON.stringify(calls[index].command).includes('SECRET'), false)
    assert.equal(JSON.stringify(calls[index].command).includes('BTC_XBT_DISPOSABLE_CONTAINER'), false)
  }
  assert.deepEqual(JSON.parse(calls[0].options.input), {})
  assert.equal(JSON.parse(calls[1].options.input).btcRune, 'SECRET-BTC')
  result = { exitCode: 1, stdout: '{"reason":"regtest_only","details":"SECRET"}' }
  await assert.rejects(registered[ids[2]].handler({ effects: {}, input }), /regtest-only/)
  result = { exitCode: 1, stdout: '{"reason":"SECRET"}' }
  await assert.rejects(registered[ids[2]].handler({ effects: {}, input }), e => !e.message.includes('SECRET'))
  console.log('Recovery action forms, private stdin, fixed commands and blocked live execution OK')
}
if (require.main === module) module.exports().catch(e => { console.error(e); process.exit(1) })
