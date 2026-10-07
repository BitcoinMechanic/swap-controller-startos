import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:15',
  releaseNotes: { en_US: 'Bounded repeat forward swaps with 24-hour channel-pinned grants, per-swap confirmation and durable history. Fixed 1,000 BTC sat to 2,000 XBT sat amounts; existing pilot records retained.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
