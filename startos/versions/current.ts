import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:10',
  releaseNotes: { en_US: 'Align quote validation with the 1,000,000-sat XBT limit. Distinguish amount and market-depth limits from invalid exchange data; preserve pricing safeguards and existing swaps.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
