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
  const receiveHost = sdk.MultiHost.of(effects, 'wallet-receive')
  const receiveBinding = await receiveHost.bindPort(9081, { protocol: 'http', preferredExternalPort: 9081,
    addSsl: { alpn: ['http/1.1'], preferredExternalPort: 9444 } })
  const receiveApi = sdk.createInterface(effects, { name: 'Wallet Receive API', id: 'wallet-receive',
    description: 'Authenticated BTC-to-XBT receive requests. Configure Wallet Receive Access before use.',
    type: 'api', masked: true, schemeOverride: null, username: null, path: '', query: {} })
  return [await binding.export([api]), await receiveBinding.export([receiveApi])]
})
