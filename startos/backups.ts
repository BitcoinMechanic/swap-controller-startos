import { unlink } from 'fs/promises'
import { T } from '@start9labs/start-sdk'
import { sdk } from './sdk'
import { mounts, rootDir } from './utils'
async function workerHook(effects: T.Effects, mode: string) {
  await sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'worker-backup', async sub => {
    const res = await sub.exec(['python3', '/app/lifecycle.py', rootDir + '/execution', mode])
    if (res.exitCode !== 0) throw new Error('Worker backup/restore refused. Unresolved or unreadable execution records require inspection.')
  })
}
// Execution credentials and authorizations are never portable in a backup.
export const { createBackup, restoreInit } = sdk.setupBackups(async () =>
  sdk.Backups.ofVolumes('main')
    .setOptions({ exclude: ['pairing.json', 'status.json', 'pairing.lock', '.controller-*',
      'execution/jobs', 'execution/heartbeat.json', 'execution/lifecycle.lock', 'execution/backup-paused.json'] })
    .setPreBackup(async (effects) => workerHook(effects, 'backup-begin'))
    .setPostBackup(async (effects) => workerHook(effects, 'backup-end'))
    .setPostRestore(async (effects) => {
      await workerHook(effects, 'restored')
      for (const name of ['pairing.json', 'status.json']) {
        await unlink(sdk.volumes.main.subpath(name)).catch((error: NodeJS.ErrnoException) => {
          if (error.code !== 'ENOENT') throw error
        })
      }
    }),
)
