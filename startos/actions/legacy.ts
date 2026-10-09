import { lstat, readdir } from 'fs/promises'
import { sdk } from '../sdk'

// Visibility is presentation only; existing handlers retain their authorization checks.
export async function legacyVisibility(kind: 'pilot' | 'jobs'): Promise<'enabled' | 'hidden'> {
  try {
    const path = sdk.volumes.main.subpath(kind === 'pilot' ? 'execution/forward-pilot/record.json' : 'execution/jobs')
    if (kind === 'pilot') await lstat(path)
    else if (!(await readdir(path)).length) return 'hidden'
    return 'enabled'
  } catch (error) {
    // Keep inspection accessible if a record cannot be read.
    return (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'hidden' : 'enabled'
  }
}
