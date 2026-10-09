import { sdk } from '../sdk'
import { invoke } from './forwardPilot'
import { mounts, rootDir } from '../utils'
const text = (name: string, masked = false, required = true) => sdk.Value.text({ name, masked, required, default: null, placeholder: null })
const meta = (name: string, description: string) => async () => ({ name, description,
  group: name === 'Save Swap Inspection Credentials' ? 'Swap Setup' : 'Advanced / Legacy', warning: null, allowedStatuses: 'only-running' as const, visibility: name === 'Save Swap Inspection Credentials' ? 'enabled' as const : 'hidden' as const })
const errors: Record<string,string> = {
  inspection_setup_required: 'Run Save Swap Inspection Credentials once before checking a recipient invoice.',
  inspection_setup_changed_pair_again: 'Your node pairing or restore state changed. Save fresh inspection credentials.',
  choose_incoming_channels: 'Several BTC channels qualify. Run Find Swap Channels and select the channel from your payer.',
  choose_outgoing_channels: 'Several channels to this recipient qualify. Run Find Swap Channels and select one.',
  no_eligible_incoming_channels: 'No connected BTC channel has enough incoming liquidity and an eligible HTLC amount.',
  no_eligible_outgoing_channels: 'No connected, eligible XBT channel goes directly to this invoice recipient.',
  one_pilot_only: 'A pilot already exists. Open Swap Status. Use the current BTC to XBT swap actions for additional swaps.',
}
async function readAction(effects: any, mode: string, input: object) {
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'swap-setup', async sub => {
    const res = await sub.exec(['python3', '/app/swap_setup.py', rootDir, mode], { input: JSON.stringify(input) })
    let report: any
    try { report = JSON.parse(String(res.stdout)) } catch { throw new Error('Inspection unavailable. Private connection details were withheld.') }
    if (res.exitCode !== 0) throw new Error(errors[report.reason] || 'Inspection could not complete. Check coordinator connections and inspection credentials.')
    const choices: Array<{name:string,value:string}> = mode === 'configure'
      ? [{ name: 'Setup', value: 'Inspection credentials saved. No payment authority granted.' }]
      : [
        { name: 'BTC incoming channels', value: report.incoming_channels.join(', ') || 'None eligible' },
        { name: 'XBT channels to recipient', value: report.outgoing_channels.join(', ') || 'None eligible' },
        { name: 'Selection', value: report.automatic_selection ? 'Both channels can be selected automatically.' : 'Select a channel if more than one is listed.' },
      ]
    return { version: '1' as const, title: 'Swap Setup',
      message: 'Read-only inspection. No invoice published and no payment started.',
      result: { type: 'group' as const, value: choices.map(row => ({ ...row, description: null,
        type: 'single' as const, masked: false, copyable: mode === 'inspect', qr: false })) } }
  })
}
export const saveSwapInspection = sdk.Action.withInput('save-swap-inspection', meta('Save Swap Inspection Credentials',
  'One-time setup for invoice and channel checks. Credentials are saved privately, excluded from backups, and invalidated by pairing changes or restore.'), sdk.InputSpec.of({
    btcRune: text('BTC inspection credential', true), xbtRune: text('XBT inspection credential', true),
    confirmed: sdk.Value.toggle({ name: 'Save these inspection credentials for future checks', default: false }),
  }), async () => {}, async ({effects,input}) => readAction(effects,'configure',input))
export const findSwapChannels = sdk.Action.withInput('find-swap-channels', meta('Find Swap Channels',
  'Paste a recipient invoice to find eligible incoming BTC channels and direct XBT channels to that recipient.'), sdk.InputSpec.of({
    invoice: text('2,000-sat XBT recipient invoice', true),
  }), async () => {}, async ({effects,input}) => readAction(effects,'inspect',input))
export const prepareSwap = sdk.Action.withInput('prepare-forward-swap', meta('Advanced: Prepare Single Pilot',
  'Review a fixed 1,000 BTC sat to 2,000 XBT sat pilot. Leave channel fields blank for automatic selection when there is exactly one eligible channel. Existing pilots cannot be replaced.'), sdk.InputSpec.of({
    invoice: text('Fresh 2,000-sat XBT invoice (at least 33 minutes remaining)', true),
    incomingChannel: text('BTC incoming channel (optional)', false, false),
    outgoingChannel: text('XBT outgoing channel (optional)', false, false),
  }), async () => {}, async ({effects,input}) => {
    try { return await invoke(effects,'prepare',{ ...input, incomingChannel: input.incomingChannel ?? null, outgoingChannel: input.outgoingChannel ?? null },'/app/swap_setup.py') }
    catch (error) {
      const message = error instanceof Error ? error.message : ''
      for (const [code, friendly] of Object.entries(errors)) if (message.includes(`: ${code}.`)) throw new Error(friendly)
      throw error
    }
  })
