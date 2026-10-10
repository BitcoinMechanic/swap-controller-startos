import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:23',
  releaseNotes: { en_US: 'Explain bounded forward route and recipient invoice refusals with private-hint guidance. Preserve grant budgets, payment limits, direct swaps, recovery and private error details; no state migration or grant renewal.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
