## Migration runtime validation (0098)

This source checkpoint adds isolated regtest runtime-image validation. It does
not change your installed service or provide a new installer. Normal restarts
and image replacement are tested with the original data and open channels.
No backup restore or force-close exercise is part of this step.

# Swap Controller

## Migration development branch

0066 prepares and tests compatibility with the new XBT engine. It produces no
installer and requires no changes to your installed services or saved grants.
Continue using the checkpointed packages while this port is tested. Pairing the
new engine for real swaps will be enabled in a later, validated package.

## Market pricing (0064)

After the packaging VM reports READY, finish any active swap and install all
three packages: BTC 26.6.9:9, XBT 0.1.0:27 and Swap Controller 0.1.0:24.
Existing fixed-price grants and historical records retain their original terms.

To use Neoxa pricing for a direction, explicitly create new grants on **both**
coordinators under **Swap Grants**. Enable **Neoxa market-priced swaps**, choose
matching per-swap and total BTC/XBT limits, and enable **New grant** when replacing
an existing grant. Finish all existing swaps first. Leave the channel field empty
for the normal routed flow. Follow any requested coordinator restart, then pair
both credentials for that direction in **Swap Controller → Swap Setup**.
Do not delete old swap records or reuse an old grant as market authority.

In **Swap Setup → Market Pricing**, operator markup defaults to **0%**. The field
uses basis points: 0 = 0%, 100 = 1%, 500 = 5%. Changes affect new quotes only.
The coordinator's selected outgoing routing fee is included before markup and
whole-satoshi rounding. Fees paid by your sending wallet are additional. The
exchange bid/ask spread still applies at 0% markup. No exchange account or
trading credentials are needed; this does not place a trade on Neoxa.

Use a fresh recipient invoice for a whole number of sats, within the saved
grant limits, with at least 33 minutes remaining and final CLTV at most 40.
Include private routing hints where needed. Then:

- **New BTC to XBT Swap** → review → **Confirm BTC to XBT Swap** → pay BTC.
- **New XBT to BTC Swap** → review → **Confirm XBT to BTC Swap** → pay XBT.

Review both amounts, the Neoxa price, markup, route fee and expiry. A quote lasts
**two minutes from the price fetch**, including review and payment. Confirm and
pay before that deadline. An expired unapproved draft can be cancelled and a
new quote requested; it is never silently repriced. No SCID entry is needed in
the controller. One payment part and one outgoing attempt are supported.

BTC → XBT uses available Neoxa asks; XBT → BTC uses bids. Quotes require enough
ordinary order-book depth and fresh, plausible ticker data. Synthetic AMM levels
are excluded. Stale/unavailable data or insufficient depth stops new market
quotes; there is no fallback to the old fixed rate. This price is a reference
for the swap, not an exchange fill guarantee or an automatically hedged trade.

Default market limits are 10,000 BTC sats / 500,000 XBT sats per swap and
50,000 BTC sats / 2,500,000 XBT sats over the grant. Outgoing fees count toward
these limits. Choose smaller limits if appropriate. Approval reserves one slot
and the quoted amounts; failed or expired approved swaps do not replenish them.
Grant enrollment still expires after 24 hours. Pause/expiry blocks new swaps
while preserving the recovery rights of already enrolled swaps.

**Swap Status** is read-only and shows expiry, slots, market limits and remaining
amount budgets. It does not guarantee a route. Route limits remain four hops,
10 sats of the outgoing asset, and 80 blocks forward / 288 blocks for new
reverse grants. Existing direct/fixed grants keep their old caps and fixed
1,000 BTC → 2,000 XBT or 3,000 XBT → 1,500 BTC amounts.

Check the appropriate swap history and recipient receipt after paying. If a
reply is uncertain, preserve the record and let the worker recover the original
attempt; do not repeat the payment. Accepted payments keep their saved amounts,
route and incoming-channel binding across restarts and price-source outages.
On-chain recovery requires separate claim/sweep verification; it is not a
settled result. Historical pilot tools remain hidden unless an older record
requires inspection. **Advanced / Recovery → Worker Status** remains available.



## Pair your nodes

1. Start the package. Health waits for pairing.
2. Open **Pair Coordinator Nodes**.
3. For each node, enter its HTTPS base URL, expected node ID, dedicated read-only
   rune, and complete public root CA certificate in PEM format.
4. Confirm saving. Leave replacement disabled for the first pair.
5. Run the action. Both identities and networks must verify before saving.
6. Check **Connection Status** and the **Coordinator Connections** health check.

Use the dedicated rune from each coordinator's **Create or Show Read-only
Controller Credential** action. Never use a general administrative rune.
For the BTC CLNrest connection string, change clnrest+https:// to https:// and
remove the query string (including any embedded rune). Enter the restricted
rune only in its separate masked field. Do not paste credentials into logs or
support messages. Obtain each CA from its authenticated StartOS session; do not
disable TLS verification.

The service refreshes health roughly every 30 seconds. A stale result cannot
keep it green. Zero channels is allowed for connection monitoring; green does
not establish sufficient liquidity, a payment route or swap readiness.

To change saved connection details, enter the full pair and enable the explicit
replacement toggle. A failed verification preserves the old pair. If a probe
holds the action lock, wait for it to finish and repeat the action. Repeating
pairing never sends a payment.

## Backups and recovery

Backups refuse unresolved execution. They preserve filtered terminal history,
while excluding pairing credentials, inspection credentials, grant pairing and
executable swap records. After restoring, re-pair the nodes and restore the
required setup explicitly. Old execution remains blocked; new pairing does not
reactivate old authority. A source/build checkpoint is not a wallet backup.

If a backup was interrupted, inspect **Swap Status** and **Worker Status** before
continuing. Keep original node and controller records needed by an enrolled swap.
Controller restoration does not revoke credentials retained on an original node.
Store restricted credentials and public CA files securely, or retrieve them
through the coordinator's actions where supported.

The package has no web UI, inbound API or additional Lightning node. Development
fixtures and historical release details are documented in README.md.

## Recipient hints and preparation errors

A recipient invoice needs a route the paying coordinator can discover. Private
hints being enabled does not guarantee a hint was included: CLN can omit a
channel whose peer appears to be a dead end. If preparation reports an unavailable
coordinator request while Swap Status says ready, check the recipient invoice's
actual hint count and channel readiness. A read-only route/history diagnostic
can distinguish a route refusal from an authority or transport problem. Keep
existing grants and records while diagnosing.

On a customer CLN node, explicitly selecting eligible local channels in the
invoice's exposeprivatechannels array can provide hints omitted by the boolean
setting. Select from current connected, normal channels with sufficient receiving
liquidity and verify that the new invoice contains a hint; never invent a SCID.
This does not widen grant authority or route limits. The forward preparation UI
can still mask a specific route refusal as a generic coordinator-request error.

## Migration development checkpoint

0084 adds a separate regtest controller service for invoice → confirm → pay,
automatic recovery and saved history. It does not add or change installed StartOS
actions yet. Run only the supplied packaging-VM fixture; do not point it at an
installed node or copy existing payment state into it. After the funded service
matrix passes, the next integration work is the full XBT node package and UI.

The candidate keeps old swaps attached to their original grants when a pairing
is renewed. Pause affects new swaps; an enrolled swap continues recovery. A
markup change affects new quotes only, with 0% as the default. Status and history
are cached read-only views. An approved swap cannot be cancelled as a draft.

## 0092 local grant administration and restore boundary

The migrated service uses the same price snapshot adapter with configurable
0–500 basis-point margin, default 0. Saved swaps are not repriced during recovery.
This patch adds shared parent restore-marker protection to service execution,
while keeping cached history readable. It remains a regtest-only migration
service; installed legacy actions are unchanged.

The matching engine adds automatically selected, locally reviewed bounded grants
and exact confirmation recovery. The disposable fixture creates and renews those
grants through fresh CLI processes, then runs six funded forward/reverse service
cases with lost replies, pending restart, failure and exact balances. It fences
stopped journals afterward and verifies refusal before RPC with history retained.
Historical funded receipts retain their original sources and image provenance;
they are not represented as reruns of this changed runtime. The VM must pass the
new matrix before this checkpoint is accepted. No installer is produced.

The operator has reported successful fresh 0090b XBT node/UI payments and
post-restart payments on two StartOS boxes. Coordinator mainnet activation,
deadline/on-chain protection and coordinator-aware backups remain subsequent
work. Mainnet guards are intentionally retained until that work is validated.

## 0094 deadline protection candidate

The separate regtest coordinator supports explicitly reviewed incoming grants
with `close_before_blocks: 72` and format `swap-bounded-authority-v2`. Existing
grants and credentials retain their previous authority. Before its single send,
the worker arms the exact committed incoming HTLC and rechecks the contract's
remaining-block margin. The node's customer facade watches armed enrollments
locally, including while the controller or outgoing coordinator is unavailable.
It records the exact close intent before dispatching `close` once. A lost reply
or crash between intent and dispatch never authorizes a second close or payment.
Admission expiry and pause do not stop enrolled recovery; restore markers do.

A narrow engine patch allows an already committed final HTLC to reach its held
gate after a closing-channel restart. New arrivals and replayed forwarding HTLCs
remain refused there; a recovery hook that continues cannot invoke a core invoice.
The original important-plugin shutdown/crash fix and the XBT package’s 0088b
recovery fee-weight patch are retained in the new build. A saved preimage
uses the core's existing persisted fulfillment and onchaind claim path.

The scoped `chain` operation checks fresh funding-spend, HTLC witness, expiry,
CSV sweep and wallet observations. It does not cache success. Confirmations and
wallet ownership are supplied by the paired CLN; the funded fixture separately
checks confirmed backend blocks and reconciles every satoshi. A close/release
reply is never settlement proof. On-chain cases remain `onchain_recovery`,
nonterminal and unavailable for grant renewal or another swap in that journal.
Automatic retirement of closed-channel history is a separate later change.

The 0094 runner requires original-binary replay controls, six patched funded
cases in both directions (claim, timeout, closing restart), and the prior six
service cases on the new engines. Local unit checks are not funded acceptance.
The existing 0093 evidence keeps its original provenance. This is a source/test
candidate only: no installer, production activation, coordinator backup support,
legacy-channel migration, multipart payments or automatic payment retries.


### Migration recovery retirement (0096)

The migration source adds explicit local review and confirmation after a
protected channel has fully resolved on chain. The coordinator verifies the
original funding and claim or timeout; the controller separately verifies the
original outgoing payment. History and used grant budgets remain saved. New
grants use eligible open channels and require their own confirmation. Existing
runes do not gain retirement authority. The packaging VM's 0096 regtest matrix
is required for acceptance; no installer or installed-node action is included.


## StartOS action integration candidate (0100)

`startos/migration/` contains a separate set of real SDK forms and handlers for
the accepted packaged swap runtime. It is not registered by `startos/index.ts`
and does not change the installed menus, node startup, restore policy or UI.
The protocol and candidate commands still require disposable regtest.

The 0100 runner compiles the handlers and exercises them around the unchanged
0098a runtime images. Its six funded cases cover both directions, failure,
ordinary restart and executable-image replacement on the same volumes, with
original channels, credentials, HTLCs and payment attempts retained. The
StartOS effects boundary is a subprocess fixture: this is not host installation
or upgrade acceptance. No installer is produced.

See the controller repository's `docs/migrated-startos-actions.md` for the
candidate action flow, retained boundaries and remaining activation work.
