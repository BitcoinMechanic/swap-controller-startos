import { lstat } from 'fs/promises'
import { T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { legacyProgram } from './legacyProgram'

export async function present(name: string) {
  try { await lstat(sdk.volumes.main.subpath(name)); return true }
  catch (error: any) { if (error?.code === 'ENOENT') return false; throw error }
}

export async function inspectLegacy(effects: T.Effects, mode: 'admit' | 'history' = 'admit', program = legacyProgram) {
  const mounts = sdk.Mounts.of().mountVolume({ volumeId: 'main', subpath: null, mountpoint: '/data', readonly: true })
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'legacy-controller-check', async sub => {
    const result = await sub.exec(['python3', '-I', '-B', '-c', program, '/data', mode])
    let report: any
    try { report = JSON.parse(String(result.stdout)) } catch { throw new Error('Legacy controller records could not be verified; all records preserved.') }
    if (result.exitCode !== 0 || report?.legacy_records_preserved !== true || report?.legacy_authority_imported !== false || (mode === 'admit' && report?.admitted !== true)) {
      const reason = typeof report?.reason === 'string' && /^[a-z_]{1,80}$/.test(report.reason) ? report.reason : 'legacy_state_unverified'
      throw new Error('Controller transition refused: ' + reason + '. All records preserved.')
    }
    return report
  })
}

export async function verifyInitialization(effects: T.Effects) {
  if (await present('execution') || await present('pairing.json')) await inspectLegacy(effects)
}
