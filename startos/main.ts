import { sdk } from './sdk'
import { mounts, rootDir } from './utils'
export const main = sdk.setupMain(async ({ effects }) => {
  const sub = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'controller')
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
  })
})
