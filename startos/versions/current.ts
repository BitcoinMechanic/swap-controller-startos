import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:8',
  releaseNotes: { en_US: 'Add separate read-only BTC and XBT gate pairing and fresh readiness observations. Live swap execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
