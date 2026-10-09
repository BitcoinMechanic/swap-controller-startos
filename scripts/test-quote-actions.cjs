const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
module.exports = async function () {
  const registered = {}, calls = []
  let result = { exitCode: 0, stdout: '{"live_payment_enabled":false,"quotes":[],"rune":"PRIVATE"}' }
  const sdk = {
    Value: { text: x => x, number: x => x, toggle: x => x },
    InputSpec: { of: x => x },
    Action: { withInput: (id, meta, spec, prefill, handler) => (registered[id] = { meta, spec, prefill, handler }) },
    SubContainer: { withTemp: async (_e, image, mounts, name, fn) => {
      assert.deepEqual(JSON.parse(JSON.stringify(image)), { imageId: 'controller' })
      return fn({ exec: async (command, options) => { calls.push({ command, options }); return result } })
    } },
  }
  const code = ts.transpileModule(fs.readFileSync('startos/actions/quotes.ts', 'utf8'),
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  vm.runInNewContext(code, { exports: {}, require: name => {
    if (name === '../sdk') return { sdk }
    if (name === './legacy') return { legacyVisibility: async () => 'enabled' }
    if (name === '../utils') return { mounts: {}, rootDir: '/data' }
    throw new Error('Unexpected module')
  } })
  const ids = ['quote-status', 'review-swap-quote', 'prepare-swap-quote', 'approve-swap-quote', 'prepare-reverse-swap-quote']
  assert.deepEqual(Object.keys(registered), ids)
  assert.deepEqual(Object.keys(registered[ids[0]].spec), [])
  assert.deepEqual(Object.keys(registered[ids[1]].spec), ['job'])
  assert.deepEqual(Object.keys(registered[ids[2]].spec), ['job', 'xbtInvoice', 'btcSats'])
  assert.equal(registered[ids[2]].spec.xbtInvoice.masked, true)
  assert.equal(registered[ids[2]].spec.btcSats.integer, true)
  assert.equal(registered[ids[2]].spec.btcSats.default, null)
  assert.equal(registered[ids[3]].spec.confirmed.default, false)
  assert.deepEqual(Object.keys(registered[ids[4]].spec), ['job', 'btcInvoice', 'xbtSats'])
  assert.equal(registered[ids[4]].spec.btcInvoice.masked, true)
  assert.equal(registered[ids[4]].spec.xbtSats.default, null)
  assert.equal(registered[ids[4]].spec.xbtSats.min, 200000)
  assert.equal(registered[ids[4]].spec.xbtSats.max, 200000)
  const input = { job: 'swap', xbtInvoice: 'PRIVATE-INVOICE', btcSats: 100000, expectedDigest: 'a'.repeat(64), confirmed: true }
  for (const [index, id] of ids.entries()) {
    const action = registered[id]
    assert.equal((await action.meta()).group, 'Advanced / Legacy')
    assert.equal(await action.prefill(), undefined)
    const report = await action.handler({ effects: {}, input })
    assert.equal(JSON.stringify(report).includes('PRIVATE'), false)
    assert.deepEqual(Array.from(calls[index].command), ['python3', '/app/quote_actions.py', '/data/execution', ['status', 'review', 'prepare', 'approve', 'prepare-reverse'][index]])
    assert.equal(JSON.stringify(calls[index].command).includes('PRIVATE'), false)
    assert.equal(JSON.stringify(calls[index].options).includes('BTC_XBT_DISPOSABLE_CONTAINER'), false)
    assert.deepEqual(Object.keys(calls[index].options), ['input'])
  }
  assert.deepEqual(JSON.parse(calls[0].options.input), {})
  assert.equal(JSON.parse(calls[2].options.input).xbtInvoice, 'PRIVATE-INVOICE')
  result = { exitCode: 0, stdout: '{"btc_invoice":"lnbcrt1test","review_digest":"code"}' }
  const report = await registered[ids[3]].handler({ effects: {}, input })
  const invoice = report.result.value.find(row => row.name === 'BTC regtest invoice')
  assert.equal(invoice.masked, true); assert.equal(invoice.copyable, true); assert.equal(invoice.qr, true)
  result = { exitCode: 0, stdout: '{"xbt_invoice":"lnxbtrt1test","direction":"reverse"}' }
  const reverseReport = await registered[ids[3]].handler({ effects: {}, input })
  const xbtInvoice = reverseReport.result.value.find(row => row.name === 'XBT regtest invoice')
  assert.equal(xbtInvoice.masked, true); assert.equal(xbtInvoice.copyable, true); assert.equal(xbtInvoice.qr, true)
  result = { exitCode: 1, stdout: '{"reason":"regtest_only","details":"PRIVATE"}' }
  await assert.rejects(registered[ids[2]].handler({ effects: {}, input }), /regtest-only/)
  result = { exitCode: 1, stdout: '{"reason":"PRIVATE"}' }
  await assert.rejects(registered[ids[3]].handler({ effects: {}, input }), e => !e.message.includes('PRIVATE'))
  result = { exitCode: 0, stdout: 'PRIVATE malformed response' }
  await assert.rejects(registered[ids[1]].handler({ effects: {}, input }), e => !e.message.includes('PRIVATE'))
  console.log('Quote action forms, review/approval separation, private stdin and blocked live execution OK')
}
if (require.main === module) module.exports().catch(e => { console.error(e); process.exit(1) })
