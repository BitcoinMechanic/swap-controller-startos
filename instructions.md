# Swap Controller

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
