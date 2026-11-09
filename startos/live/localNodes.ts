import { mkdir, writeFile } from 'node:fs/promises'
import { FileHelper, T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'

const profiles = { 'c-lightning': 'btc-cln-v1', 'xbt-cln': 'xbt-privkeyio-v1' } as const
export const settingsPath = () => sdk.volumes.main.subpath('local-node-setup.txt')
export function parsePublication(raw: string, profile: string) {
  if (raw.length > 131072) throw Error('local_node_unavailable')
  const v = JSON.parse(raw)
  if (!v || Object.keys(v).sort().join() !== 'credentials,format,hostId,internalPort,profile' || v.format !== 'local-swap-node-v1' || v.profile !== profile || typeof v.hostId !== 'string' || !/^[a-z0-9-]{1,64}$/.test(v.hostId) || !Number.isInteger(v.internalPort) || v.internalPort < 1 || v.internalPort > 65535 || !v.credentials || Object.keys(v.credentials).sort().join() !== 'incoming,outgoing') throw Error('local_node_unavailable')
  for (const kind of ['incoming', 'outgoing']) {
    const c = v.credentials[kind]
    if (!c || Object.keys(c).sort().join() !== 'grant,rune' || typeof c.rune !== 'string' || !/^[A-Za-z0-9_=-]{32,8192}$/.test(c.rune) || c.grant?.kind !== kind || c.grant?.chain?.profile !== profile || c.grant?.chain?.environment !== 'mainnet') throw Error('local_node_unavailable')
  }
  return v
}
export async function discover(effects: T.Effects, reactive: boolean) {
  const read = <V>(w: { const(): Promise<V>; once(): Promise<V> }) => reactive ? w.const() : w.once()
  const enabled = await (reactive ? FileHelper.string(settingsPath()).read().const(effects) : FileHelper.string(settingsPath()).read().once())
  if (enabled === 'disabled') return { config: null, message: 'Local node setup is disabled. Manual pairings are preserved.' }
  const nodes: Record<string, any> = {}; const missing: string[] = []
  for (const [packageId, profile] of Object.entries(profiles)) {
    // Subscribe even while absent so installing the dependency later heals setup.
    const status = await read(sdk.getStatus(effects, { packageId }))
    const location = sdk.volumes.main.subpath('.local-node-mounts/'+packageId)
    try {
      if (status === null) throw Error()
      await mkdir(location, { recursive: true })
      await sdk.mount(effects, { location, target: { packageId, volumeId: 'public', subpath: null, readonly: true, idmap: [] } })
      const file = FileHelper.string(location+'/swap-controller.json')
      const raw = await (reactive ? file.read().const(effects) : file.read().once())
      if (raw === null) throw Error()
      const publication = parsePublication(raw, profile)
      // The node publishes its stable host/port contract; never guess an assigned port.
      const address = await read(sdk.host.getBridgeAddress(effects, {
        packageId, hostId: publication.hostId, internalPort: publication.internalPort, ssl: true,
      }))
      if (!address) throw Error()
      const url = new URL('https://'+address)
      if (url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw Error()
      nodes[packageId === 'c-lightning' ? 'btc' : 'xbt'] = { url: url.origin,
        ca_pem: await sdk.getRootCa(effects), credentials: publication.credentials }
    } catch {
      missing.push(packageId === 'c-lightning' ? 'BTC Core Lightning' : 'XBT Core Lightning')
    }
  }
  return missing.length ? { config: null, message: 'Waiting for '+missing.join(' and ')+'. Enable Trust Local Swap Controller on each node. Grants become available automatically when channels have suitable liquidity.' }
    : { config: nodes, message: 'Local nodes discovered. Verifying approved grants and both node identities.' }
}
export const localSetup = sdk.Action.withInput('local-node-setup', async () => ({
  name: 'Local Node Setup', group: 'Swap Setup', allowedStatuses: 'any' as const, visibility: 'enabled' as const,
  description: 'Automatically connect the BTC and XBT Lightning services on this StartOS server.', warning: null,
}), sdk.InputSpec.of({ enabled: sdk.Value.toggle({ name: 'Automatically pair local approved grants', default: true }) }),
async () => ({ enabled: await FileHelper.string(settingsPath()).read().once() !== 'disabled' }),
async ({ effects, input }) => {
  await writeFile(settingsPath(), input.enabled ? 'enabled' : 'disabled', { mode: 0o600 })
  const report = await discover(effects, false)
  return { version: '1' as const, title: 'Local Node Setup', message: report.message,
    result: { type: 'single' as const, value: input.enabled ? 'Automatic' : 'Manual', copyable: false, masked: false, launchable: false, qr: false } }
})
