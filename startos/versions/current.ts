import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:2',
  releaseNotes: { en_US: 'Add local recovery status and explicitly confirmed regtest recovery actions. Live recovery and payments remain disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
