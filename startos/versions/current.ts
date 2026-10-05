import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:5',
  releaseNotes: { en_US: 'Add local quote status and review, plus explicitly approved regtest quote preparation and publication. Live swap execution remains disabled.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
