import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:14',
  releaseNotes: { en_US: 'Support recipient invoice timelocks within the existing 288-block route budget. Bind final payment checks to the saved route and preserve recovery margins. Report safe invoice and route failures explicitly.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE, other: { '=#privkeyio:0.1.0:13': { up: async () => {} }, '=#privkeyio:0.1.0:12': { up: async () => {} }, '=#privkeyio:0.1.0:11': { up: async () => {} }, '=#privkeyio:0.1.0:10': { up: async () => {} }, '=#privkeyio:0.1.0:9': { up: async () => {} }, '=#privkeyio:0.1.0:8': { up: async () => {} }, '=#privkeyio:0.1.0:7': { up: async () => {} }, '=#privkeyio:0.1.0:6': { up: async () => {} }, '=#privkeyio:0.1.0:5': { up: async () => {} } } },
})
