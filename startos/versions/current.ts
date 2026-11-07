import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:6',
  releaseNotes: { en_US: 'Automatic local BTC/XBT pairing from approved grants, discovered HTTPS endpoints and the StartOS root CA; preserves manual pairings and existing recovery.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
