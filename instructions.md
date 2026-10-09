# Swap Controller

## Swapping with 0060

After the packaging-VM script reports READY, install BTC 26.6.9:7, XBT 0.1.0:25
and Swap Controller 0.1.0:22. Keep your saved pairing, inspection credentials and
grants. This update does not require grant renewal or a new test payment.
Finish any active swap before updating the installed services.

In **Swap Controller → Swaps**, open **Swap Status** to check connections,
remaining grant slots, expiry and any setup problem. It is read-only. A ready
status means you can check an invoice; it does not guarantee a usable route.
Then use the actions for the direction you want:

- **New BTC to XBT Swap** → **Confirm BTC to XBT Swap** → pay the BTC invoice.
  Check **BTC to XBT Swap History** and the XBT recipient receipt.
- **New XBT to BTC Swap** → **Confirm XBT to BTC Swap** → pay the XBT invoice.
  Check **XBT to BTC Swap History** and the BTC recipient receipt.

The fixed prices remain 1,000 BTC sats → 2,000 XBT sats and 3,000 XBT sats →
1,500 BTC sats. Payer routing fees are additional. Use a fresh recipient invoice
with at least 33 minutes remaining and final CLTV at most 40. Include private
routing hints where needed. Review the recipient, amounts and route fee before
confirming. Only an unapproved draft can be cancelled.

**Swap Setup** contains node pairing, inspection credentials, **Pair BTC to XBT
Grants** and **Pair XBT to BTC Grants**. On each coordinator, **Swap Grants**
contains **Enable BTC to XBT Grant**, **Enable XBT to BTC Grant** and each
direction's pause control. Keep replacement off to retrieve a grant using its
original settings. Expiry or pause stops new enrollment while already enrolled
swaps retain recovery rights. Explicitly replace grants only after existing
swaps finish; this update does not widen an old grant or replenish its slots.

The controller normally shows 15 actions. Regtest and old pilot-creation tools
are hidden from the menu. **Legacy Pilot Status** and its approval action appear
if an older record exists. Legacy quote/recovery inspection appears when its
records exist. **Advanced / Recovery → Worker Status** remains available.
Coordinator single-pilot authorization is retained under **Advanced / Legacy**
for an older reviewed contract. Current direct swaps still work through the
normal swap actions and existing direct grants.

If an action reports an uncertain response, check Swap Status and that swap's
history. Keep the existing record and let its worker recover the original
attempt. Do not pay a second time because a reply was lost. On-chain recovery
requires separate claim/sweep verification; it is not a settled result.


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
