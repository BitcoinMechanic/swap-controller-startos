import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:3',
  releaseNotes: { en_US: 'Distinguish inherited backup pauses from current backups and omit transient pauses from backups. Restore barriers remain intact.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
