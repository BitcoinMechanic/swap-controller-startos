import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:9',
  releaseNotes: { en_US: 'Guide local node setup through one-time controller trust and automatic scoped grants; preserve discovery, pairings and swap recovery.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
