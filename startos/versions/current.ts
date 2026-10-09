import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:21',
  releaseNotes: { en_US: 'Add explicitly renewed 288-block reverse grants for public-prefix routes and show the actual saved limit on refusal. Existing 80- and 144-block grants, pending swaps and recovery retain their original authority.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
