import { T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'

const text = (name: string, masked = false) => sdk.Value.text({
  name, masked, required: true, default: null, placeholder: null,
})
const fields = {
  job: text('Recovery job ID'),
  expectedDigest: text('Expected digest from Recovery Status'),
  btcRune: text('BTC resolution-only rune', true),
  xbtRune: text('XBT resolution-only rune', true),
}
const meta = (name: string, description: string) => async () => ({
  name, description, warning: null, allowedStatuses: 'only-running' as const,
  group: 'Recovery', visibility: 'enabled' as const,
})
async function invoke(effects: T.Effects, mode: 'status' | 'confirm' | 'recover', request: object) {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'recovery-action', async sub => {
    const res = await sub.exec(['python3', '/app/recovery_actions.py', rootDir + '/execution', mode],
      { input: JSON.stringify(request) })
    let report: any
    try { report = JSON.parse(String(res.stdout)) }
    catch { throw new Error('Recovery action interrupted. Check Recovery Status before repeating.') }
    if (res.exitCode !== 0) {
      if (report.reason === 'regtest_only') throw new Error('Recovery execution is regtest-only in this release. This installation can inspect recovery status but cannot confirm or execute live recovery.')
      throw new Error('Recovery action blocked. Check Recovery Status; private details withheld.')
    }
    return {
      version: '1' as const, title: 'Recovery',
      message: 'Live recovery is disabled. Saved confirmations require fresh verification before any recovery step.',
      result: { type: 'group' as const, value: Object.entries(report).map(([name, value]) => ({
        name, description: null, type: 'single' as const,
        value: typeof value === 'object' ? JSON.stringify(value) : String(value),
        masked: false, copyable: name === 'jobs', qr: false,
      })) },
    }
  })
}
export const recoveryStatus = sdk.Action.withInput('recovery-status',
  meta('Recovery Status', 'Inspect saved recovery records locally. Does not contact either coordinator or start a payment.'),
  sdk.InputSpec.of({}), async () => {}, async ({ effects }) => invoke(effects, 'status', {}))

export const confirmRecovery = sdk.Action.withInput('confirm-recovery-revocation',
  meta('Confirm Recovery Revocation (Regtest)', 'For an existing disposable regtest recovery job only. Record one coordinator after revoking its old parent rune. No administrator credentials are accepted.'),
  sdk.InputSpec.of({ ...fields,
    network: sdk.Value.select({ name: 'Coordinator', default: 'regtest',
      values: { regtest: 'BTC regtest', 'xbt-regtest': 'XBT regtest' } } as const),
    confirmed: sdk.Value.toggle({ name: 'I revoked this coordinator’s old parent rune and authorize verification', default: false }),
  }), async () => {}, async ({ effects, input }) => invoke(effects, 'confirm', input))

export const recoverOnce = sdk.Action.withInput('recover-existing-swap',
  meta('Recover Existing Swap (Regtest)', 'Run one explicit recovery step for an existing regtest job. Both revocations must be recorded. Cannot originate a new outgoing payment.'),
  sdk.InputSpec.of({ ...fields,
    confirmed: sdk.Value.toggle({ name: 'I reviewed this job and authorize one resolution-only recovery step', default: false }),
  }), async () => {}, async ({ effects, input }) => invoke(effects, 'recover', input))
