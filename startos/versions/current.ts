import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:1',
  releaseNotes: { en_US: 'Add dormant execution worker, filtered status and restore barrier. Live execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
