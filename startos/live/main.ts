import { createHash } from 'node:crypto'
import { discover } from './localNodes'
import { sdk } from '../sdk'
import { mounts } from '../utils'
import { directory, entrypoint, invoke } from './actions'

// Initial state must already have been explicitly created. A missing or restored
// journal must not be silently initialized by a restart or image replacement.
export const command: [string, ...string[]] = [entrypoint, '--mainnet', directory, 'run']
export const main = sdk.setupMain(async ({ effects }) => {
  // Discovery inputs are volatile. Reconcile them in a child context rather
  // than letting .const() restart the payment/recovery worker's root context.
  let previousConfig: string | undefined
  return sdk.Daemons.dynamic(effects, async ({ effects }) => {
  const worker = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'migrated-worker')
  const local = await discover(effects, true)
  const configDigest = createHash('sha256').update(JSON.stringify(local.config)).digest('hex')
  if (previousConfig !== undefined && previousConfig !== configDigest)
    console.info('Local node pairing configuration changed; reconciling pairing only. Payment worker is preserved.')
  previousConfig = configDigest
  let daemons = sdk.Daemons.of(effects).addDaemon('migrated-worker', {
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
  if (local.config) {
    const pairing = sdk.SubContainer.of(effects, { imageId: 'controller' }, mounts, 'local-node-setup')
    daemons = daemons.addDaemon('local-node-setup', {
      subcontainer: pairing, requires: [], uses: configDigest,
      // Keep construction lazy: unchanged entries must not create containers
      // or rewrite credentials. Only this helper restarts for changed grants.
      exec: { fn: async sub => {
        await sub.writeFile('/tmp/local-nodes.json', JSON.stringify(local.config))
        await sub.exec(['chmod', '600', '/tmp/local-nodes.json'])
        return { command: [entrypoint, '--mainnet', directory, 'local-run', '--local-config', '/tmp/local-nodes.json'] }
      } },
      ready: { display: 'Local Lightning Nodes', fn: async () => {
        try {
          const output = await pairing.exec(['cat', '/tmp/local-node-status.json'])
          const report = JSON.parse(String(output.stdout))
          const age = Date.now()/1000-report.checked_at
          return output.exitCode === 0 && report.paired === true && Number.isSafeInteger(report.checked_at) && age >= 0 && age <= 120 ? { result: 'success', message: 'Local BTC and XBT approved grants paired in both directions.' }
            : { result: 'failure', message: 'Local pairing unavailable. Check grants, node readiness, manual pairings and restore status.' }
        } catch { return { result: 'failure', message: 'Local pairing is waiting for controller initialization and available nodes.' } }
      } },
    }) as typeof daemons
  } else {
    daemons = daemons.addHealthCheck('local-node-setup', {
      ready: { display: 'Local Lightning Nodes', fn: () => ({ result: 'disabled', message: local.message }) },
      requires: [],
    }) as typeof daemons
  }
  const publicMounts = sdk.Mounts.of().mountVolume({ volumeId: 'discovery', subpath: null, mountpoint: '/public', readonly: true })
  const discovery = sdk.SubContainer.of(effects, { imageId: 'controller' }, publicMounts, 'wallet-discovery')
  daemons = daemons.addDaemon('wallet-discovery', {
    subcontainer: discovery, requires: [],
    exec: { command: ['python3', '/usr/local/libexec/swap-discovery/server.py', '--bind', '0.0.0.0', '--config', '/public/provider.json'] },
    ready: { display: 'Wallet Discovery', fn: async () => {
      try {
        const result = await discovery.exec(['python3', '-c', 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:9080/v1/provider/info", timeout=3).read(65537)'])
        return result.exitCode === 0 ? { result: 'success', message: 'Public controller information available.' }
          : { result: 'disabled', message: 'Configure Wallet Discovery to advertise this controller.' }
      } catch { return { result: 'failure', message: 'Discovery endpoint unavailable.' } }
    } },
  }) as typeof daemons
  return daemons
  })
})
