import { sdk } from './sdk'
import { mounts, rootDir } from './utils'
export const main = sdk.setupMain(async ({ effects }) => {
  const sub = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'controller')
  const worker = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'worker')
  return sdk.Daemons.of(effects).addDaemon('monitor', {
    subcontainer: sub,
    exec: { command: ['python3', '/app/controller.py', rootDir, 'run'] },
    requires: [],
    ready: { display: 'Coordinator Connections', fn: async () => {
      const res = await sub.exec(['python3', '/app/controller.py', rootDir, 'status'])
      if (res.exitCode !== 0) return { result: 'failure', message: 'Pairing record unavailable; inspect Connection Status.' }
      try {
        const status = JSON.parse(String(res.stdout))
        if (!status.paired) return { result: 'loading', message: 'Run Pair Coordinator Nodes. Read-only mode.' }
        if (status.ready) return { result: 'success', message: 'Both node identities verified over HTTPS. Read-only; liquidity and routes not checked.' }
        return { result: 'loading', message: 'Waiting for healthy, freshly verified connections. Inspect Connection Status.' }
      } catch { return { result: 'failure', message: 'Invalid connection status.' } }
    } },
  }).addDaemon('worker', {
    subcontainer: worker,
    exec: { command: ['python3', '/app/lifecycle.py', rootDir + '/execution', 'run'] },
    requires: [],
    ready: { display: 'Execution Worker', fn: async () => {
      const res = await worker.exec(['python3', '/app/lifecycle.py', rootDir + '/execution', 'status'])
      if (res.exitCode !== 0) return { result: 'failure', message: 'Worker status unavailable.' }
      try {
        const status = JSON.parse(String(res.stdout))
        if (!status.worker_fresh) return { result: 'loading', message: 'Waiting for worker heartbeat.' }
        if (status.forward_pilot?.phase === 'attention' || status.forward_pilot?.needs_attention) return { result: 'failure', message: 'Legacy pilot needs inspection. Open Legacy Pilot Status and preserve its records.' }
        if (status.live_payment_enabled) return { result: 'success', message: 'Approved legacy pilot worker active. Inspect Legacy Pilot Status for its outcome.' }
        if (status.restored_block) return { result: 'success', message: 'Old execution remains blocked after restore. Inspect Swap Status and the directional histories.' }
        if (status.backup_paused) return { result: 'loading', message: 'Execution paused for backup; inspect Worker Status if this persists.' }
        if (status.jobs.some((j: any) => j.outcome === 'unreadable' || j.outcome === 'launch_uncertain')) return { result: 'failure', message: 'Execution record needs inspection. See Worker Status.' }
        return { result: 'success', message: 'Worker running for explicitly confirmed BTC to XBT and XBT to BTC swaps. Inspect Swap Status and the directional histories.' }
      } catch { return { result: 'failure', message: 'Invalid worker status.' } }
    } },
  })
})
