import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:16',
  releaseNotes: { en_US: 'Keep swap workers running through routine Lightning-node health and readiness updates. React to dependency installation/removal, grant publications and bridge addresses without watching whole node status objects.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:15': { up: async () => {} }, '=#privkeyio:0.1.0:14': { up: async () => {} }, '=#privkeyio:0.1.0:13': { up: async () => {} }, '=#privkeyio:0.1.0:12': { up: async () => {} }, '=#privkeyio:0.1.0:11': { up: async () => {} }, '=#privkeyio:0.1.0:10': { up: async () => {} }, '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
