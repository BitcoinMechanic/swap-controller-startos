import { readFile, writeFile, rename, mkdir } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { sdk } from '../sdk'

const path = () => sdk.volumes.discovery.subpath('provider.json')
const field = (name: string) => sdk.Value.text({ name, masked: false, required: false, default: null, placeholder: null })
function origin(raw: string) {
  const u = new URL(raw)
  const onion = /^[a-z2-7]{56}\.onion$/.test(u.hostname)
  if ((u.protocol !== 'https:' && !(u.protocol === 'http:' && onion)) || u.username || u.password || u.search || u.hash || u.pathname !== '/' || /\s/.test(raw)) throw Error('Use an HTTPS origin or HTTP v3 onion origin, without a path or credentials.')
  return u.origin
}
export const discoverySettings = sdk.Action.withInput('wallet-discovery', async () => ({
  name: 'Wallet Discovery', group: 'Swap Setup', allowedStatuses: 'any' as const, visibility: 'enabled' as const,
  description: 'Publish controller details for wallets. Enable addresses separately under Interfaces. This does not enable swap payments.', warning: null,
}), sdk.InputSpec.of({
  enabled: sdk.Value.toggle({ name: 'Publish controller details', default: false }),
  name: field('Public name'), provider: field('XBT Lightning public key'),
  clearnet: field('Clearnet HTTPS origin (optional)'), onion: field('Tor onion origin (optional)'),
}), async () => {
  try {
    const v = JSON.parse(await readFile(path(), 'utf8'))
    return { enabled: !!v.provider, name: v.name || '', provider: v.provider || '',
      clearnet: v.endpoints?.find((s: string) => !new URL(s).hostname.endsWith('.onion')) || '',
      onion: v.endpoints?.find((s: string) => new URL(s).hostname.endsWith('.onion')) || '' }
  } catch { return { enabled: false, name: '', provider: '', clearnet: '', onion: '' } }
}, async ({ input }) => {
  let value: any = {}
  if (input.enabled) {
    const name = (input.name || '').trim(), provider = (input.provider || '').trim().toLowerCase()
    if (!name || name.length > 80 || /[\x00-\x1f]/.test(name)) throw Error('Enter a public name, up to 80 characters.')
    if (!/^(02|03)[0-9a-f]{64}$/.test(provider)) throw Error('Enter the XBT Lightning node public key.')
    const endpoints = [input.clearnet, input.onion].filter((s): s is string => !!s?.trim()).map(s => origin(s.trim()))
    if (!endpoints.length) throw Error('Enter at least one enabled HTTPS or onion origin from Interfaces.')
    value = { name, provider, environment: 'mainnet', endpoints }
  }
  await mkdir(sdk.volumes.discovery.path, { recursive: true })
  const temporary = path()+'.'+randomUUID()+'.tmp'
  await writeFile(temporary, JSON.stringify(value), { mode: 0o644, flag: 'wx' })
  await rename(temporary, path())
  return { version: '1' as const, title: 'Wallet Discovery', message: input.enabled ? 'Public details saved. Existing swap authority is unchanged.' : 'Public details disabled.',
    result: { type: 'single' as const, value: input.enabled ? 'Enabled' : 'Disabled', copyable: false, masked: false, launchable: false, qr: false } }
})
