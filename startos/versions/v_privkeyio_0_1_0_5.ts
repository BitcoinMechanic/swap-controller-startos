import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
import { inspectLegacy } from '../live/legacy'
export const v_privkeyio_0_1_0_5 = VersionInfo.of({
  version: '#privkeyio:0.1.0:5',
  releaseNotes: { en_US: 'Give new quotes a 10-minute payment window while preserving old deadlines. Retire expired unpaid swaps promptly with compatible node packages. Show setup and retirement states and prefill the sole waiting payment invoice. Existing journals and pairing are preserved.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE,
    other: { '=#privkeyio:0.1.0:4': { up: async () => {} }, '=#privkeyio:0.1.0:3': { up: async () => {} }, '=#privkeyio:0.1.0:2': { up: async () => {} }, '=#privkeyio:0.1.0:1': { up: async () => {} }, '=0.1.0:24': { up: async ({ effects }) => { await inspectLegacy(effects) } },
      '=#privkeyio:0.1.0:0': { up: async () => {} } },
  },
})
