import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
import { inspectLegacy } from '../live/legacy'
export const v_privkeyio_0_1_0_3 = VersionInfo.of({
  version: '#privkeyio:0.1.0:3',
  releaseNotes: { en_US: 'Preserve validated per-swap attention reports alongside complete legacy atomic-write telemetry. Aggregate record validation failures. Checked same-volume transition from legacy 0.1.0:24. Retains terminal history and its execution restore barrier without importing credentials or old spending authority. Requires explicit initialization and fresh bounded pairing. Pending, contradictory, unknown or restored new-runtime state is refused.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE,
    other: { '=#privkeyio:0.1.0:2': { up: async () => {} }, '=#privkeyio:0.1.0:1': { up: async () => {} }, '=0.1.0:24': { up: async ({ effects }) => { await inspectLegacy(effects) } },
      '=#privkeyio:0.1.0:0': { up: async () => {} } },
  },
})
