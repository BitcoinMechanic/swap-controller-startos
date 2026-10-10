import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:24',
  releaseNotes: { en_US: 'Add Neoxa market-priced swaps in both directions with configurable 0% default markup, exact saved quotes and explicitly enabled amount/budget grants. Preserve fixed-price grants and existing recovery records.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
