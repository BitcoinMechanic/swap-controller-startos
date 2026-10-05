import { setupManifest } from '@start9labs/start-sdk'
import { long, short } from './i18n'
export const manifest = setupManifest({
  id: 'swap-controller', title: 'Swap Controller', license: 'MIT',
  packageRepo: 'https://github.com/BitcoinMechanic/swap-controller-startos',
  upstreamRepo: 'https://github.com/BitcoinMechanic/lightning',
  marketingUrl: 'https://github.com/BitcoinMechanic/lightning', donationUrl: null, description: { short, long },
  volumes: ['main'],
  images: { controller: { source: { dockerBuild: { dockerfile: 'Dockerfile', workdir: '.' } }, arch: ['x86_64'] } },
  dependencies: {},
})
