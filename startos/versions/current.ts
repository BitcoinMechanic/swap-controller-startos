import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:11',
  releaseNotes: { en_US: 'Wait briefly for the worker journal lock before starting an action. Never replay an action after dispatch; distinguish lock timeout from uncertain execution.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:10': { up: async () => {} }, '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
