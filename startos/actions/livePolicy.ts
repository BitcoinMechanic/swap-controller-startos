import { sdk } from '../sdk'
import { mounts } from '../utils'

const labels: Record<string, string> = {
  status: 'Policy status', policy: 'Policy version', policy_digest: 'Policy digest',
  live_payment_enabled: 'Live payments enabled', executor_enforcement: 'Enforced by live executor',
  relative_chain_progress_guaranteed: 'Relative chain progress guaranteed',
  common: 'Common proposed limits', forward: 'BTC to XBT pilot', reverse: 'XBT to BTC pilot',
  outstanding: 'Integration still required',
}
export const livePolicy = sdk.Action.withInput('review-live-policy', async () => ({
  name: 'Review Live Pilot Policy', group: 'Development',
  description: 'Review the proposed amount, routing fee, quote expiry and block timing limits for both gate profiles.',
  warning: null, allowedStatuses: 'only-running' as const, visibility: 'hidden' as const,
}), sdk.InputSpec.of({}), async () => {}, async ({ effects }) =>
  sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'live-policy-review', async sub => {
    const res = await sub.exec(['python3', '/app/live_policy.py', 'review'])
    let report: any
    try { report = JSON.parse(String(res.stdout)) }
    catch { throw new Error('Policy review unavailable; private details withheld.') }
    if (res.exitCode !== 0) throw new Error('Policy review unavailable; private details withheld.')
    return { version: '1' as const, title: 'Proposed Live Pilot Policy',
      message: 'Review only. No policy is activated or saved. Amounts and routing fees below use millisatoshis (1,000 msat = 1 sat). These pilot amounts are not a market exchange rate. Independent chains have no guaranteed relative progress. This action does not authorize payments or clear the restore barrier.',
      result: { type: 'group' as const, value: Object.entries(report).filter(([key]) => key in labels).map(([key, value]) => ({
        name: labels[key], description: null, type: 'single' as const,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value),
        masked: false, copyable: false, qr: false,
      })) },
    }
  }))
