import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:10',
  releaseNotes: { en_US: 'Bind regtest quote reviews to admission policy and recheck amount, fee, expiry and held timing before publication or spending. Live execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
