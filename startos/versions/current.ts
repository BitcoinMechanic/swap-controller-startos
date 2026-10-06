import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:7',
  releaseNotes: { en_US: 'Add separate read-only BTC gate pairing and fresh readiness observations. Live swap execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
