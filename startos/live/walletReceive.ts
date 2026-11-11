import { readFile, writeFile, rename } from 'node:fs/promises'
import { randomBytes, randomUUID } from 'node:crypto'
import { sdk } from '../sdk'
const path = () => sdk.volumes.main.subpath('.wallet-receive.json')
export const walletReceive = sdk.Action.withInput('wallet-receive', async () => ({
  name: 'Wallet Swap Access', group: 'Swap Setup', allowedStatuses: 'any' as const, visibility: 'enabled' as const,
  description: 'Allow a wallet to request BTC-to-XBT receive invoices and review and accept XBT-to-BTC swap quotes. This key cannot administer nodes or submit arbitrary payments. Enable a Wallet Swap API address under Interfaces.', warning: null,
}), sdk.InputSpec.of({
  enabled: sdk.Value.toggle({ name: 'Enable wallet swap requests', default: false }),
  rotate: sdk.Value.toggle({ name: 'Replace access key', default: false }),
}), async () => {
  try { const v = JSON.parse(await readFile(path(), 'utf8')); return { enabled: v.enabled === true, rotate: false } }
  catch { return { enabled: false, rotate: false } }
}, async ({ input }) => {
  let token = ''
  try { token = JSON.parse(await readFile(path(), 'utf8')).token } catch {}
  if (input.rotate || !/^[a-f0-9]{64}$/.test(token)) token = randomBytes(32).toString('hex')
  const tmp = path()+'.'+randomUUID()
  await writeFile(tmp, JSON.stringify({ enabled: input.enabled, token }), { mode: 0o600, flag: 'wx' })
  await rename(tmp, path())
  return { version: '1' as const, title: 'Wallet Swap Access', message: input.enabled ? 'Add the Wallet Swap API address and this key in the wallet’s controller settings. Keep the key private. Disabling access does not stop recovery of existing swaps.' : 'New wallet API requests disabled. Existing swap recovery continues.',
    result: { type: 'single' as const, value: input.enabled ? token : 'Disabled', copyable: input.enabled, masked: true, launchable: false, qr: input.enabled } }
})
