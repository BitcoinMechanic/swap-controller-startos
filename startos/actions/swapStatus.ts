import { sdk } from '../sdk'
import { i18n } from '../i18n'
import { mounts, rootDir } from '../utils'

const states: Record<string, string> = {
  ready: i18n('Connections verified'), waiting: i18n('Waiting for a fresh observation'), missing: i18n('Setup required'),
  unavailable: i18n('Unavailable; inspect setup or connectivity'), fresh: i18n('Running; recent heartbeat'),
  stale: i18n('Heartbeat is stale'), saved: i18n('Saved for the current pairing'), changed: i18n('Pairing changed; save fresh credentials'),
  ready_to_prepare: i18n('Ready to check a recipient invoice'), blocked: i18n('Needs attention before a new swap'),
}
const reasons: Record<string, string> = {
  connections_need_attention: i18n('Check Connection Status and coordinator pairing.'),
  inspection_setup_required: i18n('Save inspection credentials under Swap Setup.'),
  worker_not_fresh: i18n('Wait for the worker heartbeat; inspect Worker Status if it stays stale.'),
  backup_in_progress: i18n('A backup is in progress. Wait for it to finish.'),
  finish_current_swap_first: i18n('Finish the active swap, or cancel its unapproved draft, before starting another.'),
  records_need_attention: i18n('Open both swap histories and preserve records needing attention.'),
  legacy_pilot_unfinished: i18n('Open Legacy Pilot Status to finish the older swap.'),
  pair_nodes_first: i18n('Pair Coordinator Nodes under Swap Setup.'),
  grants_not_paired: i18n('Pair the two grants for this direction under Swap Setup.'),
  grant_pairing_changed: i18n('The saved grant pairing no longer matches. Check setup before pairing grants again.'),
  grant_pairing_unavailable: i18n('The saved grant pairing could not be read. Preserve its records.'),
  btc_grant_blocked: i18n('BTC grant needs attention; see its details below.'),
  xbt_grant_blocked: i18n('XBT grant needs attention; see its details below.'),
  btc_grant_unavailable: i18n('BTC grant could not be read. Check connectivity and the saved credential.'),
  xbt_grant_unavailable: i18n('XBT grant could not be read. Check connectivity and the saved credential.'),
  market_grant_mismatch: i18n('Both grants must use the same pricing mode. Finish current swaps before explicitly replacing grants.'),
  market_budget_exhausted: i18n('The market grant amount budget is exhausted. Finish current swaps before explicitly renewing and pairing grants.'),
  grant_modes_differ: i18n('The paired grants use different direct/routed modes.'),
  grant_timing_limits_differ: i18n('The paired grants have different route timing limits.'),
  configuration_changed: i18n('Setup changed or the observations took too long. Run Swap Status again.'),
  grant_replaced: i18n('This grant has been replaced; pair the current credential.'),
  grant_paused: i18n('New enrollments are paused.'),
  gate_not_ready: i18n('The required coordinator gate is not ready. Check activation and any requested restart.'),
  grant_exhausted: i18n('No swap slots remain. Finish existing swaps, then explicitly renew and pair grants.'),
  grant_expired: i18n('Enrollment has expired. Finish existing swaps, then explicitly renew and pair grants.'),
}
const number = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 ? String(v) : i18n('Unknown')
const date = (v: unknown) => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0 && v <= 8640000000000
  ? new Date(v * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC') : i18n('Unknown')
const lookup = (map: Record<string, string>, key: unknown, fallback: string) => typeof key === 'string' && Object.hasOwn(map, key) ? map[key] : fallback
const reasonText = (v: unknown) => Array.isArray(v) ? v.map(code => lookup(reasons, code, i18n('Needs inspection'))).join(' ') : i18n('Needs inspection')

export const swapStatus = sdk.Action.withInput('swap-status', async () => ({
  name: i18n('Swap Status'), group: i18n('Swaps'),
  description: i18n('Check connections, worker, saved grants, expiry, remaining slots and setup blockers in both directions.'),
  warning: null, allowedStatuses: 'only-running', visibility: 'enabled',
}), sdk.InputSpec.of({}), async () => {}, async ({ effects }) =>
  sdk.SubContainer.withTemp(effects, { imageId: 'controller' }, mounts, 'swap-status', async sub => {
    const res = await sub.exec(['python3', '/app/swap_status.py', rootDir])
    let report: any
    try { report = JSON.parse(String(res.stdout)) } catch { throw new Error(i18n('Swap Status is unavailable. Check Connection Status; existing records are preserved.')) }
    if (res.exitCode !== 0 || report?.read_only !== true || !report.directions) throw new Error(i18n('Swap Status is unavailable. Check Connection Status; existing records are preserved.'))
    const rows: Array<{ name: string; value: string }> = []
    const add = (name: string, value: string) => rows.push({ name, value })
    add(i18n('Checked at'), date(report.checked_at))
    add(i18n('Coordinator connections'), lookup(states, report.pairing, i18n('Unknown')))
    add(i18n('Inspection credentials'), lookup(states, report.inspection, i18n('Unknown')))
    add(i18n('Swap worker'), lookup(states, report.worker, i18n('Unknown')))
    if (report.restore_barrier_present === true) add(i18n('Restore protection'), i18n('Old execution remains blocked. Fresh setup does not reactivate old records.'))
    for (const [key, label, asset] of [['forward', 'BTC → XBT', 'XBT'], ['reverse', 'XBT → BTC', 'BTC']]) {
      const direction = report.directions[key]
      if (!direction || typeof direction !== 'object') { add(label, i18n('Unknown')); continue }
      add(label, lookup(states, direction.state, i18n('Unknown')))
      add(label + ' — ' + i18n('Active swaps'), number(direction.active))
      if (direction.blockers?.length) add(label + ' — ' + i18n('Next step'), reasonText(direction.blockers))
      for (const role of ['btc', 'xbt']) {
        const grant = direction.nodes?.[role]
        if (!grant) continue
        const prefix = label + ' / ' + role.toUpperCase()
        if (grant.state !== 'available') { add(prefix, i18n('Grant unavailable')); continue }
        add(prefix + ' — ' + i18n('Remaining slots'), number(grant.remaining))
        add(prefix + ' — ' + i18n('Enrollment expires'), date(grant.expires_at))
        add(prefix + ' — ' + i18n('Mode'), grant.routed === true ? i18n('Routed') : i18n('Direct'))
        add(prefix + ' — ' + i18n('Routing fee cap'), number(grant.max_fee_msat) + ' ' + asset + ' msat')
        add(prefix + ' — ' + i18n('Route delay cap (blocks)'), number(grant.max_delay_blocks))
        add(prefix + ' — ' + i18n('Maximum hops'), number(grant.max_hops))
        if(grant.market_priced===true) {
          add(prefix + ' — Market pricing', 'Neoxa')
          add(prefix + ' — Maximum BTC sats per swap', number(grant.max_btc_sats))
          add(prefix + ' — Maximum XBT sats per swap', number(grant.max_xbt_sats))
          add(prefix + ' — Remaining BTC budget (millisats)', number(grant.remaining_btc_msat))
          add(prefix + ' — Remaining XBT budget (millisats)', number(grant.remaining_xbt_msat))
        }
        if (grant.blockers?.length) add(prefix + ' — ' + i18n('Next step'), reasonText(grant.blockers))
      }
    }
    return { version: '1', title: i18n('Swap Status'),
      message: i18n('Read-only observations; no payment or route search. A recipient invoice is still needed to check its route, liquidity, fee and timing. Expiry or pause stops new enrollments; already enrolled swaps retain their recovery rights.'),
      result: { type: 'group', value: rows.map(row => ({ ...row, description: null, type: 'single', masked: false, copyable: false, qr: false })) } }
  }))
