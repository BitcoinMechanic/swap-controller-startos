import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'

const labels: Record<string, string> = {
  btc_gate: 'BTC gate observation', xbt_gate: 'XBT gate observation',
  read_only: 'Read-only check', live_ready: 'Ready for live swaps',
  live_payment_enabled: 'Live payments enabled', paired: 'Nodes paired',
  connection_ready: 'Node connections verified', restore_barrier: 'Restore barrier',
  gate_activation: 'Coordinator swap gates', execution_credentials: 'Execution credentials',
  live_amount_fee_expiry_policy: 'Amount, fee and expiry policy',
  cross_chain_timing_policy: 'Cross-chain timing policy', live_executor: 'Live execution support',
  nodes: 'Fresh node observations', blockers: 'Requirements still outstanding', checked_at: 'Checked at (Unix seconds)',
}
export const liveReadiness = sdk.Action.withInput('live-readiness', async () => ({
  name: 'Live Swap Readiness', group: 'Readiness',
  description: 'Check paired identities and channels over read-only HTTPS and report outstanding live-swap requirements.',
  warning: null, allowedStatuses: 'only-running' as const, visibility: 'enabled' as const,
}), sdk.InputSpec.of({}), async () => {}, async ({ effects }) =>
  sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'readiness-action', async sub => {
    const res = await sub.exec(['python3', '/app/readiness.py', rootDir])
    let report: any
    try { report = JSON.parse(String(res.stdout)) }
    catch { throw new Error('Readiness unavailable; private details withheld.') }
    if (res.exitCode !== 0) throw new Error('Readiness unavailable; private details withheld.')
    return { version: '1' as const, title: 'Live Swap Readiness',
      message: 'Observation only. This release cannot execute live swaps. Each gate observation uses its own restricted credential. Verified gate profiles do not authorize execution. Channel counts do not establish a route or sufficient liquidity. The restore barrier is unchanged.',
      result: { type: 'group' as const, value: Object.entries(report).filter(([key]) => key in labels).map(([key, value]) => ({
        name: labels[key], description: null, type: 'single' as const,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value),
        masked: false, copyable: false, qr: false,
      })) },
    }
  }))
