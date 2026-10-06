import { pairBtcGate } from './gateObservation'
import { liveReadiness } from './readiness'
import { quoteStatus, reviewQuote, prepareQuote, approveQuote, prepareReverseQuote } from './quotes'
import { recoveryStatus, confirmRecovery, recoverOnce } from './recovery'
import { T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
const text = (name: string, masked = false) => sdk.Value.text({ name, masked, required: true, default: null, placeholder: null })
const ca = (name: string) => sdk.Value.textarea({ name, description: 'Paste the complete PEM root CA downloaded from this node’s authenticated StartOS session.', required: true, default: null, maxLength: 65536 })
const meta = (name: string) => async () => ({ name, description: 'Certificate-verified, read-only connections. No payment authority.', warning: null, allowedStatuses: 'only-running' as const, group: 'Pairing', visibility: 'enabled' as const })
async function invoke(effects: T.Effects, mode: 'pair' | 'status', request?: object): Promise<T.ActionResult & { version: '1' }> {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'pairing-action', async sub => {
    const res = await sub.exec(['python3', '/app/controller.py', rootDir, mode], { input: request ? JSON.stringify(request) : '' })
    let report: any
    try { report = JSON.parse(String(res.stdout)) } catch { throw new Error('Action interrupted. Check Connection Status before repeating.') }
    if (res.exitCode !== 0) throw new Error('Pairing unavailable: ' + (report.error || 'private_details_withheld'))
    return { version: '1', title: 'Read-only Coordinator Connections', message: 'No funds moved. Connection readiness does not establish routing or liquidity.',
      result: { type: 'group', value: Object.entries(report).map(([name,value]) => ({ name, description: null, type: 'single' as const, value: typeof value === 'object' ? JSON.stringify(value) : String(value), masked: false, copyable: false, qr: false })) } }
  })
}
const pair = sdk.Action.withInput('pair-nodes', meta('Pair Coordinator Nodes'), sdk.InputSpec.of({
  btcUrl: text('BTC HTTPS base URL (without query or rune)'), btcId: text('Expected BTC node ID'), btcRune: text('BTC read-only rune', true), btcCa: ca('BTC root CA PEM'),
  xbtUrl: text('XBT HTTPS base URL (without query or rune)'), xbtId: text('Expected XBT node ID'), xbtRune: text('XBT read-only rune', true), xbtCa: ca('XBT root CA PEM'),
  confirmed: sdk.Value.toggle({ name: 'Save these verified read-only connections', default: false }),
  replace: sdk.Value.toggle({ name: 'Replace an existing pair if any connection details differ', description: 'Both new connections must pass verification before the saved pair changes.', default: false }),
}), async () => {}, async ({ effects, input: i }) => invoke(effects, 'pair', {
  confirmed: i.confirmed, replace: i.replace,
  nodes: { btc: { url: i.btcUrl.trim(), node_id: i.btcId.trim(), rune: i.btcRune.trim(), ca_pem: i.btcCa.trim() },
    xbt: { url: i.xbtUrl.trim(), node_id: i.xbtId.trim(), rune: i.xbtRune.trim(), ca_pem: i.xbtCa.trim() } },
}))
const status = sdk.Action.withInput('connection-status', meta('Connection Status'), sdk.InputSpec.of({}), async () => {}, async ({ effects }) => invoke(effects, 'status'))
const workerStatus = sdk.Action.withInput('worker-status', async () => ({
  name: 'Worker Status', description: 'Inspect execution lifecycle and filtered per-swap status. Live execution remains disabled.',
  warning: null, allowedStatuses: 'only-running' as const, group: 'Execution', visibility: 'enabled' as const,
}), sdk.InputSpec.of({}), async () => {}, async ({ effects }) =>
  sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'worker-status', async sub => {
    const res = await sub.exec(['python3', '/app/lifecycle.py', rootDir + '/execution', 'status'])
    if (res.exitCode !== 0) throw new Error('Worker status unavailable; private details withheld.')
    let report: any
    try { report = JSON.parse(String(res.stdout)) } catch { throw new Error('Worker status unavailable.') }
    return { version: '1' as const, title: 'Execution Worker', message: 'Live payments are disabled. Restored execution records cannot run automatically.',
      result: { type: 'group' as const, value: Object.entries(report).map(([name, value]) => ({ name, description: null,
        type: 'single' as const, value: typeof value === 'object' ? JSON.stringify(value) : String(value), masked: false, copyable: false, qr: false })) } }
  }))
export const actions = sdk.Actions.of().addAction(pair).addAction(status).addAction(workerStatus).addAction(recoveryStatus).addAction(confirmRecovery).addAction(recoverOnce).addAction(quoteStatus).addAction(reviewQuote).addAction(prepareQuote).addAction(approveQuote).addAction(prepareReverseQuote).addAction(liveReadiness).addAction(pairBtcGate)
