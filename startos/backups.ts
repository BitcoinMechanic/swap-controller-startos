import { unlink } from 'fs/promises'
import { sdk } from './sdk'
// Credentials are deliberately omitted; restore requires explicit fresh pairing.
export const { createBackup, restoreInit } = sdk.setupBackups(async () =>
  sdk.Backups.ofVolumes('main')
    .setOptions({ exclude: ['pairing.json', 'status.json', 'pairing.lock', '.controller-*'] })
    .setPostRestore(async () => {
      for (const name of ['pairing.json', 'status.json']) {
        await unlink(sdk.volumes.main.subpath(name)).catch((error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOENT') throw error
        })
      }
    }),
)
