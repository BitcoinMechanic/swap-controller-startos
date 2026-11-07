import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
import { inspectLegacy } from '../live/legacy'
import { legacyProgramV1 } from '../live/legacyProgramV1'
export const v_privkeyio_0_1_0_1 = VersionInfo.of({
  version: '#privkeyio:0.1.0:1',
  releaseNotes: { en_US: 'Checked same-volume transition from legacy 0.1.0:24. Retains terminal history and its execution restore barrier without importing credentials or old spending authority. Requires explicit initialization and fresh bounded pairing. Pending, contradictory, unknown or restored new-runtime state is refused.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE,
    other: { '=0.1.0:24': { up: async ({ effects }) => { await inspectLegacy(effects, 'admit', legacyProgramV1) } },
      '=#privkeyio:0.1.0:0': { up: async () => {} } },
  },
})
