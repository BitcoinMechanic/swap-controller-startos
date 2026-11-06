import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '#privkeyio:0.1.0:0',
  releaseNotes: { en_US: 'Dedicated controller test candidate for the migrated BTC/XBT coordinators. Explicit initialization, bounded grants, invoice review, confirmed payments and automatic recovery. Fresh install only; no legacy authority import.' },
  migrations: { up: IMPOSSIBLE, down: IMPOSSIBLE },
})
