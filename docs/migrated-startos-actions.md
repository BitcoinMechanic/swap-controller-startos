# StartOS action integration candidate — 0100

The SDK modules are separate candidate entry points. They are deliberately
absent from the installed registry. They use the exact accepted 0098a runtime
commands and preserve its explicit regtest restrictions, schemas and grants.
No source path or RPC method is supplied by a form. JSON requests travel on
private stdin; unknown failures do not expose their raw reply or input.

## Candidate flow

1. Initialize local grant administration explicitly on each coordinator. Review
   incoming or outgoing bounds; currently eligible channels are selected by the
   coordinator. There is no SCID field. Confirm the exact review and export its
   masked, copyable bounded credential. Grant status lists saved reviews.
2. Initialize fresh controller state explicitly while stopped. An ordinary
   start runs the existing service journal; it never initializes or replaces it.
3. Pair each direction using both scoped credentials, HTTPS URLs and root CAs.
   Confirmation defaults off. Credential export is a separate explicit action.
4. Paste the recipient invoice. Review the amounts, markup, fee, hops, delay
   and expiration. Confirmation is bound to the exact swap ID and direction.
5. The automatic service enrolls the confirmed swap and publishes its payment
   invoice. The Payment Invoice action displays that saved invoice as masked,
   copyable text and QR data. Pay it once. Status and history read saved records.

Settings expose a markup of 0–500 basis points, default zero, and admission
pause. Existing settings prefill the form. Pause leaves approved recovery
running. Grant and swap confirmation toggles default off. A missing or restored
journal is never silently recreated by startup.

Incoming deadline protection can force-close the actual receiving channel if
its HTLC cannot resolve in time. That existing authority is explained during
review; this candidate does not broaden it. Backup/restore remains recovery
and draining, not a way to preserve live channels during migration.

## Evidence and limits

`test-migration-actions.cjs` executes compiled SDK Actions and actual SDK input
validators. It checks private command input, exact confirmation, direction,
fixed paths, limits, safe errors, credentials and display.
`test-migration-process.py` runs four local synthetic TLS/service cases through
the same handlers, including pause, restart and a single send. These use no
real daemons or funds.

`test_startos_actions.py` wraps the unchanged 0098 funded fixture. Its adapter
replaces only StartOS subcontainer effects and fixed filesystem locations. It
runs the same exported request adapters and daemon command. Four reviewed
grants and six funded cases exercise real BTC/XBT daemons, native CLNrest TLS,
runes, signed invoices, exact balances and unchanged open channels. The market
reader is synthetic and outside the runtime. Existing 0098a raw receipts and
engine/runtime inventories retain their original provenance.

The runner compiles all three package source trees from their locks in isolated
contexts. The test harness alone receives Node and the compiled handlers.
Compiled engines and sealed Python runtimes are reused and hash-checked.
It does not build or install an s9pk, simulate a StartOS host, claim host daemon
readiness, or validate live Neoxa access.

## Remaining activation work

Live chain admission needs an explicit, tested protocol-policy change across
identity, invoices, grants, contracts and plugins. Package startup currently
rejects migrated coordinator state on the live XBT node. Bootstrap, startup and
registered actions must be integrated together before a candidate installer.
The full node/UI, restored-wallet fence and in-place channel continuity must
then be accepted on dedicated StartOS nodes. Existing installed direct/routed
swap services remain available throughout this source-only milestone.

SDK recipes used: https://docs.start9.com/packaging/0.4.0.x/recipes.html
(configuration actions and multi-daemon recipes).
