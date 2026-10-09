import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:18',
  releaseNotes: { en_US: 'Correct anchor-channel fee/dust admission for fixed forward swaps. Share the channel-type check between preparation and payment submission; preserve existing grants, history and actual HTLC protection checks.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
