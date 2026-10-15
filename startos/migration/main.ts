import { sdk } from '../sdk'
import { mounts } from '../utils'
import { directory, entrypoint, invoke } from './actions'

// Initial state must already have been explicitly created. A missing or restored
// journal must not be silently initialized by a restart or image replacement.
export const command: [string, ...string[]] = [entrypoint, '--disposable-regtest', directory, 'run']
export const main = sdk.setupMain(async ({ effects }) => {
  const worker = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'migrated-worker')
  return sdk.Daemons.of(effects).addDaemon('migrated-worker', {
    subcontainer: worker, exec: { command }, requires: [],
    ready: { display: 'Swap Worker', fn: async () => {
      try {
        const report = await invoke(effects, { action: 'status' })
        if (report.restored === true) return { result: 'failure', message: 'Restored authority is blocked. Preserve its records.' }
        if (report.worker_fresh !== true) return { result: 'loading', message: 'Waiting for the existing worker journal.' }
        return { result: 'success', message: report.paused === true ? 'New swaps paused; approved recovery continues.' : 'Worker ready for explicitly confirmed swaps.' }
      } catch { return { result: 'failure', message: 'Worker unavailable. Inspect saved status; do not recreate its state.' } }
    } },
  })
})
