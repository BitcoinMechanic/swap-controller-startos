import { T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'

const text = (name: string, masked = false) => sdk.Value.text({
  name, masked, required: true, default: null, placeholder: null,
})
const meta = (name: string, description: string) => async () => ({
  name, description, warning: null, allowedStatuses: 'only-running' as const,
  group: 'Quotes', visibility: 'enabled' as const,
})
const labels: Record<string, string> = {
  job: 'Quote ID', phase: 'Saved state', direction: 'Direction',
  quote_policy_version: 'Admission policy', quote_policy_digest: 'Admission policy digest',
  review_digest: 'Review code', recipient: 'Recipient node ID',
  btc_price_sats: 'BTC price (sats)', xbt_amount_msat: 'XBT recipient amount (msat)',
  expires_at: 'Quote expiry (Unix seconds)', quote_expired: 'Quote expired',
  outgoing_route_fee_msat: 'Outgoing routing fee (msat)',
  btc_payer_routing_fee_included: 'BTC payer routing fee included',
  pricing: 'Pricing policy', regtest_only: 'Regtest only',
  live_payment_enabled: 'Live payments enabled', restored_block: 'Restore barrier',
  xbt_price_sats: 'XBT price (sats)', btc_amount_msat: 'BTC recipient amount (msat)',
  xbt_payer_routing_fee_included: 'XBT payer routing fee included', xbt_invoice: 'XBT regtest invoice',
  quotes: 'Saved quotes', btc_invoice: 'BTC regtest invoice',
}
async function invoke(effects: T.Effects, mode: 'status' | 'review' | 'prepare' | 'prepare-reverse' | 'approve', request: object) {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'quote-action', async sub => {
    const res = await sub.exec(['python3', '/app/quote_actions.py', rootDir + '/execution', mode],
      { input: JSON.stringify(request) })
    let report: any
    try { report = JSON.parse(String(res.stdout)) }
    catch { throw new Error('Quote action interrupted. Check Quote Status before repeating.') }
    if (res.exitCode !== 0) {
      if (report.reason === 'regtest_only') throw new Error('Quote preparation and approval are regtest-only in this release. Live swap execution is disabled.')
      throw new Error('Quote action blocked. Check Quote Status and retain the saved quote; private details withheld.')
    }
    return {
      version: '1' as const, title: 'Swap Quote',
      message: mode === 'approve'
        ? 'Disposable regtest only. Pay the returned invoice before expiry. The worker sends the reviewed recipient amount after the matching incoming payment is committed. Payer routing fees are additional.'
        : mode === 'prepare' || mode === 'prepare-reverse' || mode === 'review'
          ? 'Review the recipient, amounts and expiry. Preparation does not register a quote or start a payment. Approval authorizes this exact swap when the matching incoming payment arrives. Live execution is disabled.'
          : 'Local saved status only; no coordinator RPC. Live swaps are disabled. A recorded incoming release is not independent proof of payer settlement.',
      result: { type: 'group' as const, value: Object.entries(report).filter(([key]) => key in labels).map(([key, value]) => ({
        name: labels[key], description: null, type: 'single' as const,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value),
        masked: ['btc_invoice', 'xbt_invoice'].includes(key), copyable: ['job', 'review_digest', 'recipient', 'btc_invoice', 'xbt_invoice'].includes(key),
        qr: ['btc_invoice', 'xbt_invoice'].includes(key),
      })) },
    }
  })
}
export const quoteStatus = sdk.Action.withInput('quote-status',
  meta('Quote Status', 'Inspect saved quote summaries locally, without contacting coordinators or starting payments.'),
  sdk.InputSpec.of({}), async () => {}, async ({ effects }) => invoke(effects, 'status', {}))

export const reviewQuote = sdk.Action.withInput('review-swap-quote',
  meta('Review Saved Quote', 'Read one saved quote and its review code. Does not publish an invoice or authorize spending.'),
  sdk.InputSpec.of({ job: text('Quote ID') }), async () => {},
  async ({ effects, input }) => invoke(effects, 'review', input))

export const prepareQuote = sdk.Action.withInput('prepare-swap-quote',
  meta('Prepare BTC to XBT Quote (Regtest)', 'Requires fixture-provisioned regtest coordinators. Validate an XBT regtest invoice and save terms for review. Unavailable on live installations.'),
  sdk.InputSpec.of({
    job: text('New quote ID'),
    xbtInvoice: text('Recipient XBT regtest invoice', true),
    btcSats: sdk.Value.number({ name: 'BTC price', description: 'Explicit regtest price; no market rate is inferred.',
      required: true, default: null, min: 1, max: 1000000, integer: true, units: 'BTC sats', placeholder: null }),
  }), async () => {}, async ({ effects, input }) => invoke(effects, 'prepare', input))

export const approveQuote = sdk.Action.withInput('approve-swap-quote',
  meta('Approve and Publish Swap Invoice (Regtest)', 'Approve the saved terms and publish their incoming BTC or XBT regtest invoice. Authorizes the reviewed outgoing payment after the matching incoming payment is committed, before expiry.'),
  sdk.InputSpec.of({
    job: text('Quote ID'), expectedDigest: text('Review code from Review Saved Quote'),
    confirmed: sdk.Value.toggle({ name: 'I reviewed the recipient, amounts and expiry and authorize this exact regtest swap', default: false }),
  }), async () => {}, async ({ effects, input }) => invoke(effects, 'approve', input))

export const prepareReverseQuote = sdk.Action.withInput('prepare-reverse-swap-quote',
  meta('Prepare XBT to BTC Quote (Regtest)', 'Fixed disposable fixture: receive 100,000 BTC sats for 200,000 XBT sats. Requires fixture-provisioned coordinators; live execution is disabled.'),
  sdk.InputSpec.of({
    job: text('New quote ID'), btcInvoice: text('Recipient BTC regtest invoice (100,000 sats)', true),
    xbtSats: sdk.Value.number({ name: 'XBT price', description: 'Pinned regtest policy requires exactly 200,000 XBT sats. This is not a market rate.',
      required: true, default: null, min: 200000, max: 200000, integer: true, units: 'XBT sats', placeholder: null }),
  }), async () => {}, async ({ effects, input }) => invoke(effects, 'prepare-reverse', input))
