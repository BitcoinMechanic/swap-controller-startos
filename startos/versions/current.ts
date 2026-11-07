import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
import { inspectLegacy } from '../live/legacy'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:1',
  releaseNotes: { en_US: 'Checked same-volume transition from legacy 0.1.0:24. Retains terminal history and its execution restore barrier without importing credentials or old spending authority. Requires explicit initialization and fresh bounded pairing. Pending, contradictory, unknown or restored new-runtime state is refused.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE,
    other: { '=0.1.0:24': { up: async ({ effects }) => { await inspectLegacy(effects) } },
      '=#privkeyio:0.1.0:0': { up: async () => {} } },
  },
})
