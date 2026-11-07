import { sdk } from '../sdk'
import { dependencies } from '../dependencies'
import { setInterfaces } from '../interfaces'
import { versionGraph } from '../versions'
import { actions } from '../actions'
import { restoreInit } from '../backups'
import { present } from '../live/legacy'

const setup = sdk.setupOnInit(async (effects, kind) => {
  if (kind !== 'restore' && !(await present('mainnet-swaps-v1'))) await sdk.action.createOwnTask(effects, actions.get('migrated-initialize') as Parameters<typeof sdk.action.createOwnTask>[1], 'critical', {
    reason: 'Initialize the controller, then start it. Local Node Setup connects approved BTC/XBT grants automatically.',
  })
})

export const init = sdk.setupInit(
  restoreInit,
  versionGraph,
  setInterfaces,
  actions,
  dependencies,
  setup,
)

export const uninit = sdk.setupUninit(versionGraph)
