import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:19',
  releaseNotes: { en_US: 'Keep current swap details visible: show active swaps first, select one historical swap at a time, and move grant limits into Grant Status.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:18': { up: async () => {} }, '=#privkeyio:0.1.0:17': { up: async () => {} }, '=#privkeyio:0.1.0:16': { up: async () => {} }, '=#privkeyio:0.1.0:15': { up: async () => {} }, '=#privkeyio:0.1.0:14': { up: async () => {} }, '=#privkeyio:0.1.0:13': { up: async () => {} }, '=#privkeyio:0.1.0:12': { up: async () => {} }, '=#privkeyio:0.1.0:11': { up: async () => {} }, '=#privkeyio:0.1.0:10': { up: async () => {} }, '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
