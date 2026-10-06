import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:11',
  releaseNotes: { en_US: 'Add read-only direct live candidate inspection actions using separate per-run credentials. Live execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
