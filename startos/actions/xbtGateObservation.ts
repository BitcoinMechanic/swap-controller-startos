import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
export const pairXbtGate = sdk.Action.withInput('pair-xbt-gate-observation', async () => ({
  name: 'Pair XBT Gate Observation', group: 'Pairing',
  description: 'Use the separate XBT gate observation rune with the saved XBT HTTPS endpoint and certificate.',
  warning: null, allowedStatuses: 'only-running' as const, visibility: 'enabled' as const,
}), sdk.InputSpec.of({
  rune: sdk.Value.text({ name: 'XBT gate observation rune', masked: true, required: true, default: null, placeholder: null }),
  confirmed: sdk.Value.toggle({ name: 'Save this verified read-only gate credential', default: false }),
}), async () => {}, async ({ effects, input }) =>
  sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'gate-observation', async sub => {
    const res = await sub.exec(['python3', '/app/gate_observation.py', rootDir], { input: JSON.stringify({ rune: input.rune.trim(), confirmed: input.confirmed, role: 'xbt' }) })
    if (res.exitCode !== 0) throw new Error('Gate pairing failed. Check both node connections, the active XBT gate and its observation credential.')
    return { version: '1' as const, title: 'XBT Gate Observation Paired',
      message: 'Run Live Swap Readiness for a fresh observation. Live execution and the restore barrier are unchanged.',
      result: { type: 'group' as const, value: [] } }
  }))
