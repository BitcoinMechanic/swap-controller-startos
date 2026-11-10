import { walletReceive } from './walletReceive'
import { discoverySettings } from './discovery'
import { localSetup } from './localNodes'
import { T } from '@start9labs/start-sdk'
import { sdk } from '../sdk'
import { mounts } from '../utils'
import { inspectLegacy, present, verifyInitialization } from './legacy'

// Mainnet actions use a separate immutable-environment journal.
export const directory = '/data/mainnet-swaps-v1'
export const entrypoint = '/usr/local/libexec/swap-controller/package_service.py'
export type Call = (effects: T.Effects, request: Record<string, unknown>) => Promise<any>
const errors: Record<string, string> = {
  incoming_amount_below_safe_minimum: 'The incoming payment is too small to meet the channel’s on-chain HTLC safety threshold. Increase the swap amount. No new swap was created.',
  incoming_channel_disconnected: 'The incoming channel is disconnected or has not finished reconnecting.',
  incoming_channel_busy: 'The incoming channel still has pending HTLCs. Wait for them to resolve.',
  incoming_liquidity_insufficient: 'The approved incoming channels do not have enough inbound liquidity for this payment.',
  incoming_htlc_policy_limit: 'The incoming amount is outside the peer’s advertised HTLC limits, or that policy is unavailable.',
  incoming_hint_unavailable: 'No usable private-channel routing hint is available for the incoming channel.',
  incoming_channels_unavailable: 'No approved incoming channel passes all connection, liquidity, HTLC safety and routing-hint checks.',
  channel_fee_metadata_invalid: 'The node supplied missing or invalid channel fee/dust metadata; safe HTLC admission cannot be checked.',
  channel_alias_metadata_invalid: 'The node supplied invalid channel alias metadata.',
  grant_amount_limit: 'The quoted swap amount exceeds the saved node grant. No new swap was created.',
  outside_grant: 'The requested amount exceeds the saved node grant.',
  node_not_synced: 'The Lightning node is not synced.',
  recipient_route_unknown: 'The receiving node is unknown to the routing graph and the invoice contains no private-channel route hints. Open the receiving wallet and generate a fresh invoice with route hints enabled.',
  recipient_invoice_timing: 'The recipient invoice has expired or has less than two minutes remaining. Generate a fresh invoice.',
  recipient_invoice_limits: 'The recipient invoice amount is missing or outside the supported amount range.',
  recipient_invoice_cltv_limit: 'The recipient invoice requires a timelock above the current route limit (up to 576 blocks). Use an invoice with a shorter timelock.',
  outgoing_channel_not_ready: 'The outgoing channel is disconnected, busy, or has insufficient spendable funds. Check the Lightning node channel status.',
  route_outside_approved_channels: 'The available route uses a channel outside the current swap grant. Refresh the local node setup.',
  route_fee_or_delay_limit: 'The route exceeds the allowed fee or timelock budget. No swap was created.',
  approved_channel_changed: 'A channel in the swap grant has changed or is missing. Refresh the local node setup.',
  worker_invoice_unavailable: 'The payment invoice is still being prepared. Check Swap Status, then reopen Payment Invoice for this same swap.',

  contract_reserve_insufficient: 'Each Lightning node needs at least 25,000 sats in confirmed, unreserved on-chain funds. Add the shortfall to the affected node and wait for confirmation.',
  confirmation_required: 'Confirm the exact review before continuing.',
  service_pairing_required: 'Pair the bounded grants for this direction first.',
  service_previous_swap_active: 'Finish the existing swap or cancel its unapproved draft.',
  service_review_superseded: 'The selected grants changed. Cancel the old draft and review again.',
  service_worker_unavailable: 'The automatic worker is unavailable. Check Swap Status.',
  service_paused: 'New swaps are paused. Existing approved swaps retain recovery.',
  service_restored: 'Restored authority is blocked. Preserve its records.',
  contract_admission_expired: 'This review expired. Cancel its unapproved draft and use a fresh invoice.',
  bounded_route_unavailable: 'No route fits the saved fee, delay and hop limits. Check liquidity and private invoice hints.',
  market_unavailable: 'Neoxa pricing is unavailable. Try a new review later.',
  market_amount_limit: 'The swap exceeds the pricing limit: at most 1,000,000 XBT sats and 10,000 BTC sats. Use a smaller invoice; node grant limits also apply.',
  market_depth_unavailable: 'Neoxa does not have enough usable order-book liquidity within the allowed price range. No quote was approved.',
  market_data_invalid: 'Neoxa data failed validation. No new quote was approved.',
  service_lock_timeout: 'The controller stayed busy for five seconds. This action did not start. Check history before trying again.',
  service_busy: 'The worker is updating this record. Check history before retrying.',
}
const transportErrors: Record<string, string> = {
  node_timeout: 'The Lightning node did not respond within the 15-second transport timeout.',
  node_tls_error: 'TLS verification or negotiation with the Lightning node failed.',
  node_dns_error: 'The Lightning node hostname could not be resolved.',
  node_connection_refused: 'The Lightning node refused the connection.',
  node_connection_error: 'The connection to the Lightning node was interrupted.',
  node_http_error: 'The Lightning node returned an unsuccessful HTTP status.',
  node_rpc_rejected: 'The Lightning node rejected the RPC request. Its RPC code is shown below; the node did not supply a supported admission reason.',
  node_content_type_invalid: 'The Lightning node returned an unexpected content type.',
  node_reply_too_large: 'The Lightning node response exceeded the 1 MiB limit.',
  node_reply_json_invalid: 'The Lightning node returned malformed JSON.',
  node_reply_shape_invalid: 'The Lightning node returned an unexpected response structure.',
  node_admission_code_unknown: 'The Lightning node returned an unsupported admission error code.',
  node_client_error: 'An unexpected error occurred inside the node transport client.',
  action_execution_failed: 'StartOS could not execute the controller action or obtain its result.',
  action_reply_invalid: 'The controller action returned an unreadable or incomplete response.',
}
export function refused(reason: unknown, diagnostic?: any): Error {
  const code = typeof reason === 'string' && (Object.hasOwn(errors, reason) || Object.hasOwn(transportErrors, reason)) ? reason : 'service_request_refused'
  const message = errors[code] || transportErrors[code] || 'An unclassified controller error occurred. The source locations and diagnostic reference below identify where investigation must continue.'
  const details = [`Code: ${code}`]
  if (diagnostic && typeof diagnostic === 'object') {
    for (const key of ['node', 'role', 'operation', 'stage']) {
      if (typeof diagnostic[key] === 'string' && /^[a-z][a-z0-9_-]{0,40}$/.test(diagnostic[key])) details.push(`${key}: ${diagnostic[key]}`)
    }
    const asset = diagnostic.node === 'btc-cln-v1' ? 'BTC ' : diagnostic.node === 'xbt-privkeyio-v1' ? 'XBT ' : ''
    for (const [key, label] of [['amount_msat', 'Requested amount'], ['minimum_msat', 'Minimum safe incoming amount'], ['maximum_msat', 'Maximum approved amount']]) if (Number.isSafeInteger(diagnostic[key]) && diagnostic[key] > 0) details.push(`${label}: ${diagnostic[key] / 1000} ${asset}sats${key === 'minimum_msat' ? ` (use at least ${Math.ceil(diagnostic[key] / 1000)} whole sats)` : ''}`)
    for (const key of ['http_status', 'rpc_code']) if (Number.isSafeInteger(diagnostic[key])) details.push(`${key}: ${diagnostic[key]}`)
    if (typeof diagnostic.reference === 'string' && /^[a-f0-9]{16}$/.test(diagnostic.reference)) details.push(`Reference: ${diagnostic.reference}`)
    if (Array.isArray(diagnostic.locations)) for (const loc of diagnostic.locations.slice(0, 6)) {
      if (typeof loc === 'string' && /^[a-z_]+\.py:[0-9]{1,6}$/.test(loc)) details.push(`Source: ${loc}`)
    }
    if (diagnostic.saved === false) details.push('Diagnostic file could not be saved.')
  }
  return new Error(`${message}\n${details.join('\n')}\nCheck history before retrying: this error alone does not prove whether a remote operation completed. Do not repeat a payment.`)
}
export async function executeRequest(request: Record<string, unknown>, exec: (argv: string[], options?: { input: string }) => Promise<any>) {
    const initialize = request.action === 'initialize'
    if (initialize && (request.confirmed !== true || Object.keys(request).sort().join() !== 'action,confirmed')) throw refused('confirmation_required')
    let res: any
    try { res = await exec([entrypoint, '--mainnet', directory, initialize ? 'init' : 'action'], initialize ? undefined : { input: JSON.stringify(request) }) } catch { throw refused('action_execution_failed') }
    let reply: any
    try { reply = JSON.parse(String(res.stdout)) } catch { throw refused('action_reply_invalid') }
    if (res.exitCode !== 0 || !reply || !Object.hasOwn(reply, 'result')) throw refused(reply?.reason || 'action_reply_invalid', reply?.diagnostic)
    return reply.result
}
export const invoke: Call = async (effects, request) => {
  if (request.action === 'initialize') {
    if (request.confirmed !== true || Object.keys(request).sort().join() !== 'action,confirmed') throw refused('confirmation_required')
    await verifyInitialization(effects)
  }
  return sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'migrated-swap-action', sub => executeRequest(request, (argv, options) => sub.exec(argv, options)))
}
const text = (name: string, masked = false) => sdk.Value.text({ name, masked, required: true, default: null, placeholder: null })
const confirmed = () => sdk.Value.toggle({ name: 'Confirm the exact reviewed terms', default: false })
const meta = (name: string, group = 'Swaps') => async () => ({ name, group,
  description: 'BTC/XBT swaps using saved bounded grants and a reviewed Neoxa price.',
  warning: 'Incoming deadline protection can force-close the actual receiving channel if a swap cannot resolve in time.',
  allowedStatuses: 'only-running' as const, visibility: 'enabled' as const })
const hex = (v: unknown): v is string => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v)
const number = (v: unknown): v is number => Number.isSafeInteger(v) && (v as number) >= 0
const states: Record<string, string> = {
  setting_up: 'Setting up payment invoice', waiting_for_retirement: 'Expired unpaid — retiring unused authority',
  resolving: 'Resolving payment', incoming_held: 'Incoming payment held', outgoing_uncertain: 'Outgoing payment outcome uncertain',
  outgoing_complete: 'Recipient paid — resolving incoming payment', outgoing_failed: 'Recipient payment failed — resolving incoming payment',
  close_outcome_pending: 'Channel close outcome pending', onchain_retirement_review: 'On-chain retirement review required',
  onchain_settled: 'Settled on-chain', onchain_failed: 'Failed on-chain',
  ready_for_confirmation: 'Ready for confirmation', authorizing: 'Authorizing', publishing: 'Publishing payment invoice',
  waiting_for_payment: 'Awaiting payment', outgoing_pending: 'Recipient payment pending', settled: 'Settled', failed: 'Failed',
  cancelled: 'Cancelled before approval', expired: 'Expired', onchain_recovery: 'On-chain recovery needs inspection',
}
function orderedSwaps(swaps: any[]): any[] {
  return swaps.filter(r => hex(r?.confirmation)).slice().sort((a, b) =>
    (number(b.expires_at) ? b.expires_at : 0) - (number(a.expires_at) ? a.expires_at : 0) || a.confirmation.localeCompare(b.confirmation))
}
export function display(report: any, title = 'Swap Status', showGrants = false): T.ActionResult & { version: '1' } {
  const rows: any[] = []
  const add = (name: string, value: string, copyable = false, qr = false, masked = false) => rows.push({ name,
    description: null, type: 'single', value, copyable, launchable: false, qr, masked })
  if (!report || typeof report !== 'object') throw refused(null)
  const all = Array.isArray(report.swaps) ? orderedSwaps(report.swaps) : null
  const active = all?.filter((r: any) => r.terminal !== true) || []
  const list = all ? (active.length ? active : all.slice(0, 1)) : [report]
  for (const row of list) {
    if (hex(row?.confirmation)) add('Swap ID', row.confirmation, true)
    if (row?.direction === 'forward' || row?.direction === 'reverse') add('Direction', row.direction === 'forward' ? 'BTC → XBT' : 'XBT → BTC')
    if (typeof row?.state === 'string') add('State', Object.hasOwn(states, row.state) ? states[row.state] : 'Needs inspection')
    for (const [key, label] of Object.entries({ btc_msat: 'BTC millisats', xbt_msat: 'XBT millisats', routing_fee_msat: 'Outgoing routing fee (millisats)',
      markup_bps: 'Markup (basis points)', outgoing_hops: 'Outgoing hops', outgoing_delay_blocks: 'Outgoing delay (blocks)' })) {
      if (number(row?.[key])) add(label, String(row[key]))
    }
    if (number(row?.expires_at) && row.expires_at <= 2 ** 35) add('Pay before (UTC)', new Date(row.expires_at * 1000).toISOString())
    if (typeof row?.blocked === 'boolean') add('Execution blocked', String(row.blocked))
    if (typeof row?.invoice === 'string' && /^ln[a-z0-9]{100,20000}$/.test(row.invoice)) add('Payment invoice', row.invoice, true, true, true)
  }
  for (const key of ['worker_fresh', 'paused', 'restored', 'paired', 'local_paired', 'existing_swaps_unchanged', 'initialized']) {
    if (typeof report[key] === 'boolean') add(key.replaceAll('_', ' '), String(report[key]))
  }
  if (showGrants) for (const direction of ['forward', 'reverse']) for (const kind of ['incoming', 'outgoing']) {
    const grant = report.grants?.[direction]?.[kind]
    if (!grant) continue
    const prefix = (direction === 'forward' ? 'BTC → XBT' : 'XBT → BTC') + ' / ' + kind + ' grant / '
    if (typeof grant.expired === 'boolean') add(prefix + 'expired', String(grant.expired))
    for (const key of ['max_swaps', 'max_amount_msat', 'total_amount_msat', 'max_fee_msat', 'max_delay_blocks', 'max_hops', 'min_cltv_delta', 'max_cltv_delta']) {
      if (number(grant.limits?.[key])) add(prefix + key, String(grant.limits[key]))
    }
  }
  if (all) add('Saved swaps', `${all.length} — open Swap History to select an older swap`)
  return { version: '1', title, message: 'New quotes allow 10 minutes total to review, confirm and pay. Check Pay before (UTC). Existing deadlines are unchanged. Swap Status shows active swaps, or the latest swap when none are active. Swap History shows one selected swap. Pay once; statuses come from saved records.',
    result: { type: 'group', value: rows } }
}
function credential(raw: unknown): any {
  try {
    if (typeof raw !== 'string' || raw.length > 65536) throw Error()
    const v = JSON.parse(raw)
    if (!v || Object.keys(v).sort().join() !== 'grant,rune' || typeof v.rune !== 'string' || !v.grant) throw Error()
    return v
  } catch { throw new Error('Paste the complete bounded credential returned by the coordinator.') }
}
export function createActions(call: Call = invoke) {
  let actions = sdk.Actions.of().addAction(localSetup)
  const add = (a: any) => { actions = actions.addAction(a) }
  add(sdk.Action.withInput('migrated-initialize', async () => ({ ...await meta('Initialize Swap Controller', 'Swap Setup')(), allowedStatuses: 'only-stopped' as const }),
    sdk.InputSpec.of({ confirmed: confirmed() }), async () => ({ confirmed: false }),
    async ({ effects, input }) => {
      if (input.confirmed !== true) throw refused('confirmation_required')
      return display(await call(effects, { action: 'initialize', confirmed: true }))
    }))
  add(sdk.Action.withInput('legacy-history', async () => ({ ...await meta('Legacy Swap History', 'Swap Setup')(),
    allowedStatuses: 'any' as const, warning: null, description: 'Read the retained legacy history. Does not resume old swaps or import credentials.' }),
    sdk.InputSpec.of({}), async () => {}, async ({ effects }) => {
      const rows: any[] = []
      if (await present('execution')) {
        const report = await inspectLegacy(effects, 'history')
        if (!Array.isArray(report.records)) throw refused(null)
        for (const direction of ['forward','reverse']) for (const phase of ['settled','failed','cancelled','expired']) {
          const count = report.records.filter((r: any) => r.direction === direction && r.phase === phase).length
          rows.push({ name: (direction === 'forward' ? 'BTC → XBT' : 'XBT → BTC') + ' / ' + phase,
            value: String(count), type: 'single', description: null, copyable: false, masked: false, launchable: false, qr: false })
        }
        const warnings = report.inert_temporary_files_preserved?.pilot_attention_status
        if (Number.isSafeInteger(warnings) && warnings > 0) rows.push({ name: 'Retained historical worker warning files',
          value: String(warnings), type: 'single', description: null, copyable: false, masked: false, launchable: false, qr: false })
      }
      return { version: '1' as const, title: 'Legacy Swap History',
        message: 'Retained local history only. Old credentials and restore barriers remain unchanged. No legacy authority is imported; these labels are not new settlement proofs.',
        result: { type: 'group' as const, value: rows } }
    }))
  for (const direction of ['forward', 'reverse'] as const) {
    const label = direction === 'forward' ? 'BTC to XBT' : 'XBT to BTC'
    const prefill = async ({ effects }: { effects: T.Effects }) => {
      const report = await call(effects, { action: 'status' })
      const matches = (Array.isArray(report?.swaps) ? report.swaps : []).filter((r: any) => r.direction === direction && r.state === 'ready_for_confirmation' && hex(r.confirmation))
      return { cid: matches.length === 1 ? matches[0].confirmation : null, confirmed: false }
    }
    add(sdk.Action.withInput('migrated-pair-' + direction, meta('Pair ' + label + ' Grants', 'Swap Setup'), sdk.InputSpec.of({
      incomingUrl: text('Incoming coordinator HTTPS URL'), incomingCa: sdk.Value.textarea({ name: 'Incoming root CA PEM', required: true, default: null, maxLength: 65536 }),
      incomingCredential: text('Incoming bounded credential', true), outgoingUrl: text('Outgoing coordinator HTTPS URL'),
      outgoingCa: sdk.Value.textarea({ name: 'Outgoing root CA PEM', required: true, default: null, maxLength: 65536 }),
      outgoingCredential: text('Outgoing bounded credential', true), confirmed: confirmed(),
    }), async () => ({ confirmed: false }), async ({ effects, input: i }) => {
      if (i.confirmed !== true) throw refused('confirmation_required')
      const binding = { direction, nodes: {
        incoming: { url: i.incomingUrl.trim(), ca_pem: i.incomingCa.trim(), ...credential(i.incomingCredential) },
        outgoing: { url: i.outgoingUrl.trim(), ca_pem: i.outgoingCa.trim(), ...credential(i.outgoingCredential) },
      } }
      return display(await call(effects, { action: 'pair', binding, confirmed: i.confirmed }), 'Grants Paired')
    }))
    add(sdk.Action.withInput('migrated-new-' + direction, meta('New ' + label + ' Swap'), sdk.InputSpec.of({
      invoice: text('Recipient invoice with private routing hints if needed', true),
    }), async () => {}, async ({ effects, input }) => display(await call(effects, { action: 'prepare', direction, invoice: input.invoice.trim() }), 'Review ' + label + ' Swap')))
    for (const mode of ['confirm', 'cancel'] as const) add(sdk.Action.withInput('migrated-' + mode + '-' + direction,
      meta((mode === 'confirm' ? 'Confirm ' : 'Cancel Unapproved ') + label + ' Swap'), sdk.InputSpec.of({ cid: text('Reviewed swap ID'), confirmed: confirmed() }), prefill,
      async ({ effects, input }) => {
        if (input.confirmed !== true || !hex(input.cid)) throw refused('confirmation_required')
        const report = await call(effects, { action: 'status' })
        if (!Array.isArray(report?.swaps) || !report.swaps.some((r: any) => r.confirmation === input.cid && r.direction === direction)) throw refused('confirmation_required')
        return display(await call(effects, { action: mode, cid: input.cid, confirmation: input.cid, ...(mode === 'confirm' ? { confirmed: true } : {}) }))
      }))
  }
  add(sdk.Action.withInput('migrated-status', meta('Swap Status'), sdk.InputSpec.of({}), async () => {},
    async ({ effects }) => display(await call(effects, { action: 'status' }))))
  add(sdk.Action.withInput('migrated-history', meta('Swap History'), sdk.InputSpec.of({
    cid: sdk.Value.dynamicSelect(async ({ effects }) => {
      const report = await call(effects, { action: 'history' })
      const swaps = orderedSwaps(report.swaps || [])
      return { name: 'Swap', default: swaps[0]?.confirmation || null,
        values: Object.fromEntries(swaps.map(r => [r.confirmation,
          `${r.direction === 'forward' ? 'BTC → XBT' : 'XBT → BTC'} · ${Object.hasOwn(states, r.state) ? states[r.state] : 'Needs inspection'} · ${number(r.expires_at) && r.expires_at <= 2 ** 35 ? new Date(r.expires_at * 1000).toISOString() : 'Unknown deadline'} · ${r.confirmation.slice(0, 12)}`])) }
    }),
  }), async () => {}, async ({ effects, input }) => {
    const report = await call(effects, { action: 'history' })
    const row = (report.swaps || []).find((r: any) => hex(input.cid) && r.confirmation === input.cid)
    if (!row) throw new Error('The selected swap is not in saved history. Reopen Swap History and select a record.')
    return display(row, 'Swap Details')
  }))
  add(sdk.Action.withInput('migrated-grants', meta('Grant Status', 'Swap Setup'), sdk.InputSpec.of({}), async () => {},
    async ({ effects }) => {
      const report = await call(effects, { action: 'status' })
      return display({ grants: report.grants, local_paired: report.local_paired }, 'Grant Status', true)
    }))
  add(sdk.Action.withInput('migrated-invoice', meta('Payment Invoice'), sdk.InputSpec.of({ cid: text('Confirmed swap ID') }), async ({ effects }) => {
      const report = await call(effects, { action: 'status' })
      const waiting = (report.swaps || []).filter((s: any) => s.state === 'waiting_for_payment' && hex(s.confirmation))
      return { cid: waiting.length === 1 ? waiting[0].confirmation : null }
    },
    async ({ effects, input }) => {
      if (!hex(input.cid)) throw refused(null)
      return display(await call(effects, { action: 'invoice', cid: input.cid }), 'Pay This Invoice Once')
    }))
  add(sdk.Action.withInput('migrated-settings', meta('Swap Settings', 'Swap Setup'), sdk.InputSpec.of({
    paused: sdk.Value.toggle({ name: 'Pause new swaps (approved recovery continues)', default: false }),
    markupBps: sdk.Value.number({ name: 'Markup (basis points; 100 = 1%)', required: true, default: 0, integer: true, min: 0, max: 500 }),
  }), async ({ effects }) => {
    const current = await call(effects, { action: 'status' })
    return { paused: current.paused === true, markupBps: number(current.markup_bps) ? current.markup_bps : 0 }
  }, async ({ effects, input }) => display(await call(effects, { action: 'configure', paused: input.paused, markup_bps: input.markupBps }), 'Swap Settings')))
  return actions
}
export const actions = createActions().addAction(discoverySettings).addAction(walletReceive)
