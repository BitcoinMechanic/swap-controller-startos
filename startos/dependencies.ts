import { sdk } from './sdk'
import { FileHelper } from '@start9labs/start-sdk'
import { settingsPath } from './live/localNodes'
const enabled = async ({ effects }: { effects: Parameters<typeof sdk.getRootCa>[0] }) =>
  await FileHelper.string(settingsPath()).read().const(effects) !== 'disabled'
export const dependencies = sdk.Dependencies.of()
  .addDependency(sdk.Dependency.optional('c-lightning', {
    description: 'Automatic local BTC coordinator connection',
    metadata: { title: 'Core Lightning', icon: 'https://raw.githubusercontent.com/BitcoinMechanic/btc-cln-startos/master/icon.svg' },
    kind: 'running', healthChecks: [], versionRange: '>=#privkeyio:26.6.10:3', enabled,
  }))
  .addDependency(sdk.Dependency.optional('xbt-cln', {
    description: 'Automatic local XBT coordinator connection',
    metadata: { title: 'XBT Core Lightning', icon: 'https://raw.githubusercontent.com/BitcoinMechanic/xbt-cln-startos/master/icon.svg' },
    kind: 'running', healthChecks: [], versionRange: '>=#privkeyio:26.6.9:5', enabled,
  }))
