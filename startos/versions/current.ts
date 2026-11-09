import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:15',
  releaseNotes: { en_US: 'Support routes up to 576 blocks and safely refresh idle automatically managed outgoing grants. Preserve the additional 150-block incoming recovery margin, existing swap authority, manual grants, fee limits and hop limits.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:14': { up: async () => {} }, '=#privkeyio:0.1.0:13': { up: async () => {} }, '=#privkeyio:0.1.0:12': { up: async () => {} }, '=#privkeyio:0.1.0:11': { up: async () => {} }, '=#privkeyio:0.1.0:10': { up: async () => {} }, '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
