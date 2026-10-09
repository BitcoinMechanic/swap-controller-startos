import { sdk } from '../sdk'
import { mounts, rootDir } from '../utils'
const text=(name:string,masked=false)=>sdk.Value.text({name,masked,required:true,default:null,placeholder:null})
const meta=(name:string,description:string)=>async()=>({name,description,group:name.startsWith('Pair ')?'Swap Setup':'Swaps',
  warning:'Fixed rate: 3,000 XBT sats for 1,500 BTC sats. Payer routing fees are additional. Deadline protection may force-close the original receiving XBT channel and incur on-chain fees.',
  allowedStatuses:'only-running' as const,visibility:'enabled' as const})
const errors:Record<string,string>={grant_timing_limits_differ:'The reverse grants have different timing limits. Explicitly replace both grants with the same version and pair their credentials.',contract_exceeds_grant_timing:'This reviewed swap exceeds the current grant timing limit. Preserve any enrolled swaps; cancel only an unapproved draft before reviewing again.',route_planning_refused:'The BTC grant cannot plan a new route. Check whether it expired, was paused or was replaced.',route_outside_grant:'The BTC route starts on a channel outside the saved grant. Finish existing swaps before explicitly replacing the BTC grant and pairing it.',bounded_route_unavailable:'No BTC route fits the saved grant: at most 10 sats routing fee and 4 hops, with the saved grant timing limit. New grants allow 288 blocks; existing grants retain 80 or 144. Check the recipient route hints and grant timing limit.',invalid_recipient_invoice:'Use a 1,500-sat BTC BOLT11 invoice with final CLTV at most 40 blocks.',reverse_grant_required:'Use separate reverse grants from both coordinators. Forward grants cannot authorize BTC spending.',incoming_channel_outside_grant:'A XBT channel can receive this swap, but it is outside the saved grant. Finish current swaps, replace the XBT routed grant with the channel field empty, then pair the new credential.',incoming_amount_trimmed:'Approved XBT channels fail the current fee/dust protection check for this 3,000-sat swap. More receiving liquidity does not resolve this check.',incoming_channels_busy:'Approved XBT channels still have pending HTLCs. Wait for them to finish.',incoming_channels_not_ready:'No approved XBT channel is ready. Check channel state, connection, pending HTLCs and fee limits. After adding or closing channels, replace the XBT routed grant and pair its new credential.',grant_modes_differ:'Both grants must use the same direct or routed mode.',no_eligible_incoming_channels:'No approved XBT channel currently has receiving liquidity.',repeat_setup_required:'Pair the XBT and BTC repeat grants first.',repeat_pairing_changed:'The coordinator pairing or restore state changed. Re-pair grants before creating a swap.',session_inactive_or_restart_required:'A grant is paused or replaced, or the XBT coordinator needs a restart to activate repeat mode.',session_expired_or_exhausted:'A grant expired or has no slots left. Finish current swaps, explicitly create new grants on the coordinators, then pair them.',legacy_pilot_unfinished:'Finish the original pilot before creating a repeat swap.',finish_current_swap_first:'Finish the current swap or cancel its unapproved draft first.',review_changed:'The reviewed swap changed. Open Confirm XBT to BTC Swap again.',no_swap_to_confirm:'No unapproved swap is available. Use New XBT to BTC Swap first.',inspection_setup_required:'Save the two inspection credentials once before preparing swaps.',recipient_invoice_needs_33_minutes:'Create a fresh 1,500-sat BTC invoice with at least 33 minutes remaining and final CLTV at most 40 blocks.',backup_in_progress:'A backup is in progress. Wait for it to finish.',restored_pilot_authority_blocked:'Restored authority is blocked. Preserve the records for recovery.'}
Object.assign(errors, {
  "confirmed_reserve_insufficient": "A coordinator has less than 50,000 sats of confirmed, unreserved on-chain funds on its own chain. Add the required reserve before preparing a new swap.",
  "reserve_unavailable": "A coordinator on-chain reserve could not be checked. Check inspection credentials and node connectivity.",
  "inspection_setup_changed_pair_again": "Inspection credentials belong to an older pairing or restore state. Save fresh inspection credentials under Swap Setup.",
  "only_unapproved_draft_can_be_cancelled": "This swap has already entered authorization or payment. Open its history and let the existing attempt recover; it cannot be cancelled as a draft.",
  "session_channel_changed": "The approved channel set changed after review. Preserve any enrolled swap; cancel only an unapproved draft before reviewing again.",
  "enrollment_may_have_started": "Authorization may already have started. Open this swap history and let the worker recover the existing record.",
  "swap_already_approved": "This swap was already approved. Open its history for the existing payment invoice and outcome.",
  "session_rpc_refused_or_uncertain": "A coordinator request was refused or its reply was unavailable. Open Swap Status and this swap history; do not repeat a payment.",
  "session_identity_changed": "A coordinator identity no longer matches the saved pairing. Check Connection Status and preserve existing records.",
  "worker_heartbeat_required": "The worker heartbeat is not fresh enough to approve this swap. Check Worker Status and try confirmation after it recovers.",
  "admission_expired": "The review window expired. Cancel only an unapproved draft, then prepare a fresh recipient invoice.",
  "recipient_invoice_expiring": "The recipient invoice is too close to expiry. Preserve any enrolled swap and check its history before creating another.",
  "invalid_grant_timing": "A reverse grant returned an unsupported or inconsistent timing limit. Check the installed versions and saved pairing.",
  "pair_nodes_first": "Pair Coordinator Nodes under Swap Setup before creating grants or swaps.",
  "explicit_approval_required": "Review the swap and turn on its explicit confirmation before continuing.",
  "selected_channel_unavailable": "A selected channel is unavailable. Check the coordinator channel state and the saved grant."
})
const stages:Record<string,string>={review:'Ready for confirmation',authorizing:'Authorizing this swap',publishing:'Creating XBT invoice',waiting_for_xbt:'Awaiting XBT payment',send_intent:'Paying recipient',release_intent:'Completing settlement',settled:'Settled',fail_intent:'Returning incoming payment',failed:'Failed',expired:'Expired without payment',retire_intent:'Retiring unpaid invoice',cancelled:'Cancelled before approval',close_intent:'Protecting funds on-chain',onchain_recovery:'On-chain recovery — verification required'}
for (const limit of [80,144,288]) errors['bounded_route_unavailable_'+limit]='No BTC route fits this saved grant: '+limit+' blocks maximum total delay, 10 sats maximum routing fee and 4 hops. Check the recipient route hints. The grant has not changed.'
async function request(effects:any,mode:string,input:object={}) {
  return sdk.SubContainer.withTemp(effects,{imageId:'controller'},mounts,'repeat-swaps',async sub=>{
    const res=await sub.exec(['python3','/app/reverse_swaps.py',rootDir,mode],{input:JSON.stringify(input)})
    let report:any
    try{report=JSON.parse(String(res.stdout))}catch{throw new Error('Swap action interrupted. Open XBT to BTC Swap History before retrying; do not repeat a payment.')}
    if(res.exitCode!==0)throw new Error((typeof report?.reason === 'string' && Object.hasOwn(errors, report.reason) ? errors[report.reason] : '') || 'Swap could not proceed. Open Swap Status, then XBT to BTC Swap History. Existing records are preserved; private details withheld.')
    return report
  })
}
function display(mode:string,report:any){
  const labels:Record<string,string>={routed:'Routed swap',routing_fee_msat:'Coordinator routing fee (BTC millisats)',pilot_id:'Swap ID',phase:'Status',xbt_sats:'You pay (XBT sats)',btc_sats:'Recipient receives (BTC sats)',recipient:'Recipient node',incoming_channel:'XBT incoming channel',outgoing_channel:'BTC outgoing channel',invoice:'XBT invoice to pay',quote_expires_at:'Pay before (Unix time)',admission_until:'Confirm before (Unix time)',remaining:'Remaining swap slots',max_delay_blocks:'Maximum BTC route delay (blocks)',route_delay_blocks:'Planned BTC route delay (blocks)',expires_at:'Grant expiry (Unix time)',setup_saved:'Setup saved',restore_blocked:'Blocked after restore',needs_attention:'Needs attention'}
  const rows=Array.isArray(report.swaps)?report.swaps:[report]
  return {version:'1' as const,title:mode==='status'?'XBT to BTC Swap History':'XBT → BTC Swap',
    message:mode==='prepare'?'Review the recipient and amounts, then open Confirm XBT to BTC Swap. No XBT invoice has been published yet.':mode==='configure'?'Grants paired. Use New XBT to BTC Swap with a fresh recipient invoice.':mode==='approve'?'Pay the XBT invoice once before its deadline. If publication is still in progress, open XBT to BTC Swap History. Keep all three services running.':'Completed swaps remain in history. On-chain recovery requires claim and sweep verification; it does not mean settlement is complete.',
    result:{type:'group' as const,value:rows.flatMap((row:any)=>Object.entries(row).filter(([k])=>k in labels).map(([k,v])=>({
      name:labels[k],description:rows.length>1?'Swap '+String(row.pilot_id).slice(0,12):null,type:'single' as const,
      value:k==='phase'?(stages[String(v)]||'Needs inspection'):String(v),masked:['recipient','invoice'].includes(k),copyable:k==='invoice'||k==='pilot_id',qr:k==='invoice',
    })))}}
}
export const pairReverseGrants=sdk.Action.withInput('pair-reverse-grants',meta('Pair XBT to BTC Grants','One-time setup: paste the bounded grant from each coordinator. These are payment/protection credentials, separate from read-only inspection credentials.'),sdk.InputSpec.of({
  xbtCredential:text('XBT controller grant credential',true),btcCredential:text('BTC controller grant credential',true),
  confirmed:sdk.Value.toggle({name:'Save these bounded grants for explicitly confirmed swaps',default:false}),
}),async()=>{},async({effects,input})=>display('configure',await request(effects,'configure',input)))
export const newReverseSwap=sdk.Action.withInput('new-reverse-swap',meta('New XBT to BTC Swap','Paste a fresh 1,500-sat BTC invoice. Your grants supply the approved channels. Review before confirming.'),sdk.InputSpec.of({
  invoice:text('1,500-sat BTC recipient invoice (33+ minutes remaining, final CLTV at most 40)',true),
}),async()=>{},async({effects,input})=>display('prepare',await request(effects,'prepare',input)))
const confirmation=sdk.InputSpec.of({
  pilotId:text('Reviewed swap ID (filled automatically — do not change)'),
  confirmed:sdk.Value.toggle({name:'Confirm this exact swap: pay 3,000 XBT sats, deliver 1,500 BTC sats, with deadline protection',default:false}),
})
export const confirmReverseSwap=sdk.Action.withInput('confirm-reverse-swap',meta('Confirm XBT to BTC Swap','Authorizes both coordinators for the reviewed swap and publishes its XBT invoice. No per-swap credential copying.'),confirmation,
  async({effects})=>({...await request(effects,'approval-input'),confirmed:false}),
  async({effects,input})=>display('approve',await request(effects,'approve',input)))
export const reverseSwapStatus=sdk.Action.withInput('reverse-swap-status',meta('XBT to BTC Swap History','Review saved swap states and payment invoices. Includes swaps in this direction; older single-pilot records have Legacy Pilot Status.'),sdk.InputSpec.of({}),async()=>{},
  async({effects})=>display('status',await request(effects,'status')))
export const cancelReverseDraft=sdk.Action.withInput('cancel-reverse-draft',meta('Cancel Unapproved XBT to BTC Swap','Only an unapproved draft can be cancelled. Enrollment and payment records cannot be reset.'),sdk.InputSpec.of({pilotId:text('Draft swap ID (filled automatically — do not change)'),confirmed:sdk.Value.toggle({name:'Cancel this unapproved draft',default:false})}),
  async({effects})=>({...await request(effects,'approval-input'),confirmed:false}),
  async({effects,input})=>display('cancel',await request(effects,'cancel',input)))
