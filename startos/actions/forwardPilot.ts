import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
const text = (name: string, masked = false) => sdk.Value.text({ name, masked, required: true, default: null, placeholder: null })
const meta = (name: string) => async () => ({
  name, group: 'Forward Pilot',
  description: 'One direct 1,000 BTC sat to 2,000 XBT sat pilot. Explicit approval is required before publishing its BTC invoice.',
  warning: 'This is a fixed-price pilot. Deadline recovery can force-close the selected BTC channel and incur on-chain fees. Keep both coordinators and the controller running.',
  allowedStatuses: 'only-running' as const, visibility: 'enabled' as const,
})
export async function invoke(effects: any, mode: string, input: object = {}, script = "/app/forward_pilot.py") {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'forward-pilot', async sub => {
    const res = await sub.exec(['python3', script, rootDir, mode], { input: JSON.stringify(input) })
    let report: any
    try { report = JSON.parse(String(res.stdout)) } catch { throw new Error('Pilot operation interrupted. Inspect Forward Pilot Status; do not repeat a payment.') }
    if (res.exitCode !== 0) {
      const reason = typeof report?.reason === 'string' && /^[a-z_]{1,80}$/.test(report.reason)
        ? report.reason : 'pilot_refused_or_uncertain'
      throw new Error(`Pilot stopped: ${reason}. Existing records were preserved.`)
    }
    const stages: Record<string,string> = { not_prepared: 'No swap prepared', review: 'Awaiting authorization and approval', publishing: 'Creating BTC payment invoice', waiting_for_btc: 'Awaiting BTC payment', send_intent: 'Paying XBT recipient', release_intent: 'Completing settlement', settled: 'Settled', fail_intent: 'Returning incoming payment', failed: 'Payment failed', close_intent: 'Protecting funds on-chain', onchain_recovery: 'On-chain recovery — claim verification required' }
    const labels: Record<string,string> = { phase: 'Status', pilot_id: 'Swap ID', btc_sats: 'You pay (BTC sats)', xbt_sats: 'Recipient receives (XBT sats)', invoice: 'BTC payment invoice', contract: 'Authorization contract', payment_started: 'Payment started', approval_required: 'Approval required', close_requested: 'Channel close requested', onchain_claim_verified: 'On-chain claim verified', restore_blocked: 'Blocked after restore', needs_attention: 'Needs attention', admission_until: 'Approval deadline (Unix time)', quote_expires_at: 'Payment deadline (Unix time)' }
    const permitted = ['pilot_id','contract','btc_sats','xbt_sats','payment_started','approval_required','admission_until','phase','invoice','close_requested','onchain_claim_verified','restore_blocked','quote_expires_at','needs_attention']
    return { version: '1' as const, title: 'Forward Pilot',
      message: mode === 'prepare'
        ? 'Review the fixed amounts and channels in this contract. Authorize the same contract on both coordinators, then approve its pilot ID here with the returned credentials. Preparation does not publish an invoice.'
        : report.phase === 'settled' ? 'Swap complete. The XBT recipient was paid and both channels have cleared their HTLCs. Keep this settlement record. Repeat swaps are not enabled in this version.' : 'Pay only the BTC invoice shown for this approved pilot. Status “settled” requires the original outgoing payment to complete and both channel HTLC sets to clear. “onchain_recovery” is not a verified claim or sweep.',
      result: { type: 'group' as const, value: Object.entries(report).filter(([key]) => permitted.includes(key)).map(([key,value]) => ({
        name: labels[key] || key, description: null, type: 'single' as const,
        value: key === 'phase' ? (stages[String(value)] || 'Needs inspection') : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : typeof value === 'object' ? JSON.stringify(value) : String(value),
        masked: ['contract','invoice'].includes(key), copyable: ['pilot_id','contract','invoice'].includes(key), qr: key === 'invoice',
      })) },
    }
  })
}
export const prepareForwardPilot = sdk.Action.withInput('prepare-forward-pilot', meta('Prepare Forward Pilot'), sdk.InputSpec.of({
  invoice: text('Fresh 2,000-sat XBT recipient invoice (at least 33 minutes remaining)', true),
  incomingChannel: text('LND to BTC coordinator short channel ID'), outgoingChannel: text('XBT coordinator to recipient short channel ID'),
  btcRune: text('BTC inspection credential', true), xbtRune: text('XBT inspection credential', true),
}), async () => {}, async ({effects,input}) => invoke(effects,'prepare',input))
export const approveForwardPilot = sdk.Action.withInput('approve-forward-pilot', meta('Approve Forward Pilot and Publish BTC Invoice'), sdk.InputSpec.of({
  pilotId: text('Reviewed pilot ID'), btcRune: text('BTC forward pilot credential',true), xbtRune: text('XBT forward pilot credential',true),
  confirmed: sdk.Value.toggle({ name: 'Approve this exact 1,000 BTC sat to 2,000 XBT sat swap and its deadline protection', default: false }),
}), async () => {}, async ({effects,input}) => invoke(effects,'approve',input))
export const forwardPilotStatus = sdk.Action.withInput('forward-pilot-status', meta('First Pilot Status'), sdk.InputSpec.of({}),
  async () => {}, async ({effects}) => invoke(effects,'status'))
