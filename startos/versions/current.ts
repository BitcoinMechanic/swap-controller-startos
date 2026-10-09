import { IMPOSSIBLE, VersionInfo } from '@start9labs/start-sdk'
export const current = VersionInfo.of({
  version: '0.1.0:22',
  releaseNotes: { en_US: 'Simplify the swap menus, add read-only grant and worker status, and clarify preparation errors. Preserve existing grants, direct and routed swaps, legacy records and recovery without state migrations.' },
  migrations: { up: async () => {}, down: IMPOSSIBLE },
})
