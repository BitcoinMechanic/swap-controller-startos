import { lstat, writeFile } from 'fs/promises'
import { sdk } from './sdk'
// Credentials and execution journals are not portable authority.
export const { createBackup, restoreInit } = sdk.setupBackups(async () =>
  sdk.Backups.ofVolumes('main')
    .setOptions({ exclude: ['mainnet-swaps-v1', 'migrated-swaps-v1'] })
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
