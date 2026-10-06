import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:9',
  releaseNotes: { en_US: 'Add a read-only review of proposed live pilot limits and strict numeric validation. Live execution and restore barriers remain unchanged.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
