import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:13',
  releaseNotes: { en_US: 'Add an explicitly approved single forward pilot with exact node-side authority and persistent reconciliation. Reverse live execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
