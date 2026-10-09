import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
const text = (name: string, masked = false) => sdk.Value.text({
  name, masked, required: true, default: null, placeholder: null,
})
const labels: Record<string, string> = {
  preflight_matches: 'Candidate passes inspection', reasons: 'Reasons',
  execution_authorized: 'Execution authorized', live_payment_enabled: 'Live payments enabled',
  payment_started: 'Payment started', scope: 'Inspection scope',
  policy_digest: 'Policy digest', candidate_digest: 'Candidate digest', observed_at: 'Checked at (Unix seconds)',
  btc_amount_msat: 'BTC amount (msat)', xbt_amount_msat: 'XBT amount (msat)',
  route_delay_blocks: 'Outgoing delay (blocks)', quote_expires_at: 'Proposed quote expiry (Unix seconds)',
  proposed_incoming_expiry: 'Proposed incoming expiry (chain height)',
  incoming_remaining_blocks: 'Incoming remaining blocks', minimum_incoming_remaining_blocks: 'Minimum incoming blocks',
  held_htlc_verified: 'Held incoming HTLC verified', deadline_protection_verified: 'Deadline protection verified',
  current_fee_trim_checks_passed: 'Current fee dust checks passed', confirmed_reserves_checked: 'Reserves checked',
}
const common = {
  invoice: text('Recipient invoice', true),
  incomingChannel: text('Incoming coordinator channel short ID (block x transaction x output, without spaces)'),
  outgoingChannel: text('Direct recipient channel short ID'),
  btcRune: text('BTC inspection credential', true), xbtRune: text('XBT inspection credential', true),
}
const meta = (reverse: boolean) => async () => ({
  name: reverse ? 'Inspect XBT to BTC Candidate' : 'Inspect BTC to XBT Candidate', group: 'Development',
  description: reverse
    ? 'Read-only direct-channel inspection for a 1,500-sat BTC recipient invoice and your proposed XBT price.'
    : 'Read-only direct-channel inspection for a 2,000-sat XBT recipient invoice and fixed 1,000-sat BTC price.',
  warning: null, allowedStatuses: 'only-running' as const, visibility: 'hidden' as const,
})
async function invoke(effects: any, direction: 'forward' | 'reverse', input: object) {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'live-preflight', async sub => {
    const res = await sub.exec(['python3', '/app/preflight_actions.py', rootDir, direction], { input: JSON.stringify(input) })
    let report: any
    try { report = JSON.parse(String(res.stdout)) }
    catch { throw new Error('Inspection unavailable; private details withheld.') }
    if (res.exitCode !== 0 || !report || typeof report !== 'object') throw new Error('Inspection unavailable; private details withheld.')
    return { version: '1' as const, title: 'Read-only Live Candidate Inspection',
      message: 'No quote published or payment started. Credentials are not saved by the controller. A passing snapshot does not authorize execution, reserve liquidity or verify a held HTLC. Live execution and restore barriers remain unchanged. Amounts use msat (1,000 msat = 1 sat); pilot prices are not a market rate.',
      result: { type: 'group' as const, value: Object.entries(report).filter(([key]) => key in labels).map(([key, value]) => ({
        name: labels[key], description: null, type: 'single' as const,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value), masked: false,
        copyable: ['policy_digest', 'candidate_digest'].includes(key), qr: false,
      })) },
    }
  })
}
export const inspectForward = sdk.Action.withInput('inspect-live-forward', meta(false), sdk.InputSpec.of(common),
  async () => {}, async ({ effects, input }) => invoke(effects, 'forward', input))
export const inspectReverse = sdk.Action.withInput('inspect-live-reverse', meta(true), sdk.InputSpec.of({ ...common,
  xbtSats: sdk.Value.number({ name: 'Proposed XBT price', required: true, default: null,
    min: 1, max: 500000, integer: true, units: 'sats', placeholder: null }),
  routeDelay: sdk.Value.number({ name: 'Direct BTC route delay', required: true, default: 40,
    min: 40, max: 576, integer: true, units: 'blocks', placeholder: null }),
}), async () => {}, async ({ effects, input }) => invoke(effects, 'reverse', input))
