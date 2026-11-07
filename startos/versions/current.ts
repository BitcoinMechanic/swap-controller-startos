import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
import { inspectLegacy } from '../live/legacy'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:4',
  releaseNotes: { en_US: 'Identify public pricing requests as SwapController so Neoxa accepts them. Preserves existing pairing, grants, swap journals and recovery state during updates. No pricing policy or settlement behavior changes.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE,
    other: { '=#privkeyio:0.1.0:3': { up: async () => {} }, '=#privkeyio:0.1.0:2': { up: async () => {} }, '=#privkeyio:0.1.0:1': { up: async () => {} }, '=0.1.0:24': { up: async ({ effects }) => { await inspectLegacy(effects) } },
      '=#privkeyio:0.1.0:0': { up: async () => {} } },
  },
})
