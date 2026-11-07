import { sdk } from '../sdk'
import { setDependencies } from '../dependencies'
import { setInterfaces } from '../interfaces'
import { versionGraph } from '../versions'
import { actions } from '../actions'
import { restoreInit } from '../backups'
import { present } from '../live/legacy'

const setup = sdk.setupOnInit(async (effects, kind) => {
  if (kind !== 'restore' && !(await present('mainnet-swaps-v1'))) await sdk.action.createOwnTask(effects, actions.get('migrated-initialize') as Parameters<typeof sdk.action.createOwnTask>[1], 'critical', {
    reason: 'Initialize fresh swap controller state, then start the service and pair bounded coordinator grants.',
  })
})

export const init = sdk.setupInit(
  restoreInit,
  versionGraph,
  setInterfaces,
  setDependencies,
  actions,
  setup,
)

export const uninit = sdk.setupUninit(versionGraph)
