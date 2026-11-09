import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:7',
  releaseNotes: { en_US: 'Read-only wallet discovery interface with independently configured clearnet and Tor addresses; preserves grants, pairing and recovery.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
