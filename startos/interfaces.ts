import { sdk } from './sdk'

// StartOS controls reachability. Operators enable clearnet/Tor addresses here.
export const setInterfaces = sdk.setupInterfaces(async ({ effects }) => {
  const host = sdk.MultiHost.of(effects, 'wallet-discovery')
  const binding = await host.bindPort(9080, {
    protocol: 'http', preferredExternalPort: 9080,
    addSsl: { alpn: ['http/1.1'], preferredExternalPort: 9443 },
  })
  const api = sdk.createInterface(effects, {
    name: 'Wallet Discovery', id: 'wallet-discovery',
    description: 'Public controller information and directory. No payment or administration methods.',
    type: 'api', masked: true, schemeOverride: null, username: null, path: '', query: {},
  })
  return [await binding.export([api])]
})
