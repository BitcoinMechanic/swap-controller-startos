import { sdk } from './sdk'
export const rootDir = '/data'
export const mounts = sdk.Mounts.of().mountVolume({ volumeId: 'main', subpath: null, mountpoint: rootDir, readonly: false })
