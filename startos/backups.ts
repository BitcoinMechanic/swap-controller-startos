import { lstat, writeFile } from 'fs/promises'
import { sdk } from './sdk'
// Credentials and execution journals are not portable authority.
export const { createBackup, restoreInit } = sdk.setupBackups(async () =>
  sdk.Backups.ofVolumes('main')
    .setOptions({ exclude: ['.wallet-receive.json', '.local-node-mounts', 'mainnet-swaps-v1', 'migrated-swaps-v1',
      'pairing.json', 'pairing.lock', 'status.json', '.controller-*',
      'forward-session-pairing.json', 'reverse-session-pairing.json', 'swap-inspection.json',
      'btc-gate-observation.json', 'xbt-gate-observation.json',
      'execution/forward-swaps', 'execution/reverse-swaps', 'execution/forward-pilot', 'execution/reverse-pilot',
      'execution/jobs', 'execution/regtest-quote-nodes.json', 'execution/heartbeat.json',
      'execution/lifecycle.lock', 'execution/backup-paused.json',
      'execution/forward-pilot-status.json', 'execution/reverse-pilot-status.json'] })
    .setPostRestore(async () => {
      const path = sdk.volumes.main.subpath('swap-restored.json')
      try {
        await writeFile(path, JSON.stringify({schema:1,format:'swap-restore-barrier-v1',blocked:true}), {mode:0o600,flag:'wx'})
      } catch (error: any) {
        if (error?.code !== 'EEXIST' || !(await lstat(path)).isFile()) throw error
        // Any existing regular marker continues to fence restored authority.
      }
    }),
)
