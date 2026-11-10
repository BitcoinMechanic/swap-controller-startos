# Swap Controller transition candidate

Candidate **#privkeyio:0.1.0:3** supports a checked same-volume update from
legacy **0.1.0:24**, or an ordinary update from dedicated candidate
**#privkeyio:0.1.0:0**, **#privkeyio:0.1.0:1**, or **#privkeyio:0.1.0:2**. Use the installer bound to the build receipt. Keep the
existing service and volume; do not uninstall, restore, or remove markers.

The legacy transition checks local terminal contracts, current pairing and restore
bindings, publication amounts, and retained incoming channel bindings. It refuses
pending/unknown records, on-chain recovery, old executor jobs, contradictory state,
and root restore fences. A recognized legacy execution barrier remains intact.
Complete atomic-write heartbeat/status leftovers are preserved, including historical
worker warning reports. Their filenames alone never waive validation; incomplete
writes, restore/authority records and unknown contents remain refused.
Per-swap `attention.json` files are preserved after checking their exact boolean
status schema. Validation reports failures across all records without exposing
private details. These local records are not independent proof of cross-chain settlement.

All old records and credentials stay in place as non-executable history. The
new worker never reads them or imports their authority. **Legacy Swap History**
shows retained phase counts. Backups continue to exclude legacy credentials and
execution authority; restoring a backup fences the new controller.

After the legacy update, complete **Initialize Swap Controller** while stopped,
then start the service. Initialization creates an empty, separate journal. Pair
freshly reviewed bounded incoming/outgoing credentials for each direction using
the coordinator HTTPS address and its StartOS root CA. Pairing checks both nodes
before replacing the saved pairing. Never paste credentials into chat or logs.

New swap: recipient invoice → review Neoxa price and limits → explicit confirmation
→ pay the published incoming invoice once. The automatic worker preserves the
saved price, exact incoming HTLC and one outgoing attempt across restarts. Pricing
uses the existing fixed Neoxa market/depth policy; markup defaults to 0% and is
configurable. An unavailable market prevents new quotes, not saved recovery.


Ordinary restart/update is not backup restoration. Restored wallets remain fenced
from new coordinator authority; recovery may force-close channels. Incoming
swap deadline protection can also close the actual receiving channel if a swap
cannot resolve before its approved deadline. Grants require explicit review.

0102 validation distinguishes synthetic mainnet policy checks, actual funded
regtest fixtures, packed-file checks, and pending StartOS host acceptance. It
does not claim a mainnet swap or StartOS host test from a regtest receipt.

Public Neoxa pricing requests identify themselves as `SwapController/0.1.0`; Python’s default client name is rejected by the exchange. Version `#privkeyio:0.1.0:4` fixes this request header. Updating from `:3` retains the existing pairing and journals; do not initialize or pair again. If a review previously failed with pricing unavailable, create a new review after updating.

Give new quotes a 10-minute payment window while preserving old deadlines. Retire expired unpaid swaps promptly with compatible node packages. Show setup and retirement states and prefill the sole waiting payment invoice. Existing journals and pairing are preserved.

For the expiry-workflow release, update the BTC and XBT coordinator packages before the controller. Existing unpaid expired swaps are then retired automatically after fresh unused-authority checks; no journal deletion or initialization is needed. Retired enrollments still consume their original grant slot and budget. A later swap may require a newly reviewed grant. Ten-minute pricing applies only to new controller quotes; never pay a previously expired invoice.

The development customer quote API is not enabled by this installed package.
Version 2 offers can be explicitly accepted by the development buyer, but do not
send a buyer payment. This API is not enabled on production installations. Continue to
use the existing explicit review and confirmation workflow for installed swaps.


## Automatic setup with Lightning nodes on this server

Update BTC Core Lightning to **#privkeyio:26.6.10:5**, XBT Core Lightning to
**#privkeyio:26.6.9:8**, and Swap Controller to **#privkeyio:0.1.0:9**. Keep the
existing services and volumes. Local Node Setup is enabled by default.

On each node, enable **Trust Local Swap Controller** once. The nodes initialize
their coordinators and automatically create and renew scoped grants when suitable
channel liquidity is available. Individual grant approvals are unnecessary. The controller resolves the
actual StartOS bridge HTTPS ports, obtains the StartOS root CA and imports the
restricted grants locally. It pairs BTC→XBT and XBT→BTC together after authenticating
all four grants and both node identities. No URL, certificate or credential copying
is required. Local Lightning Nodes shows missing setup and connection status.

Automatically renewed grants are picked up without manual approval. Node trust
can be revoked to pause managed grants. The existing 64-record journal ceiling
still requires safe maintenance when reached; history is never erased automatically. Existing swaps keep their
original pairings. Changes wait until active swaps finish. A manual pairing is never
replaced automatically: manual users can disable Local Node Setup and continue using
the existing Pair Grants actions. Automatic setup never initializes missing controller
journals, removes restore fences, opens channels or increases grant budgets.

Connection publications are mounted read-only from the nodes' `public` volumes.
Those mounts are excluded from controller backups, and the generated configuration
is private, ephemeral container data. Only the existing read-only HTTPS pairing
checks run before a single atomic commit. Repeated unchanged setup does not write
or repeatedly probe nodes while payment recovery is running. A failure leaves both
directions unchanged and retries setup later. New swap admission still checks current
grant authority. Disabling automatic setup preserves existing pairing and recovery.

The package now uses Start SDK 3.1.1 for the supported StartOS root-CA API; action
result metadata and dependency declarations are updated accordingly.

## Add this controller to a phone wallet

1. In Interfaces, configure an HTTPS address for Wallet Discovery, and/or add an
   onion address using the Tor service. Enable the addresses you want to use.
2. In Swap Setup → Wallet Discovery, enable publishing and enter the public name,
   your XBT Lightning public key, and those endpoint origins (no path).
3. In the wallet’s dots menu → Swap controllers → Add controller, enter or scan
   an address. Check the returned identity and save. Onion connections use Tor.
4. Search directory accepts the same discovery address, or an independent
   directory implementing the protocol. There is no bundled public directory yet.

This interface allows discovery only. It cannot execute swaps or administer the
controller, and saving it does not grant payment authority. Disabling publishing
makes discovery return unavailable without affecting existing swaps.

For HTTPS access on your LAN, trust your StartOS root certificate on the client.
The interface supports HTTP/1.1; its onion address is served through the Tor
service. The Local Lightning Nodes check remains waiting until both nodes have
approved incoming and outgoing grants. Discovery works independently of grants.

## Quote limits (0.1.0:10)

New quotes allow up to 1,000,000 XBT sats and 10,000 BTC sats, subject to node
grant limits and channel liquidity. Amount-limit and insufficient-order-book-depth
errors are distinct from malformed exchange data. Price freshness, spread, depth,
slippage and exact arithmetic checks are unchanged. Previously saved quotes retain
their original amounts and deadlines.

The staged controller runtime must include `migration/market_quote.py`,
`migration/service_market.py` and `migration/service_cli.py`; use
`scripts/stage-market-runtime.py` to update their integrity hashes before packaging.

## Worker contention (0.1.0:11)

Actions wait up to five seconds for the journal lock before starting. Brief worker
updates no longer fail immediately. A lock timeout explicitly says the action did
not start; failures after dispatch remain uncertain and are never automatically
replayed. History and existing recovery records are preserved.

## On-chain reserve (0.1.0:12)

The controller requires at least 25,000 confirmed, unreserved on-chain sats on
each node, for both swap directions. This replaces the earlier 50,000-sat check.
Unconfirmed deposits and reserved outputs do not count. The policy is separate
from CLN anchor-channel fee reserves and applies to private channels too. It
does not change channel balances, fee estimates, protection deadlines or saved
swap terms. A shortfall now produces a specific reserve error.

Runtime staging also includes the package-owned `migration/cross_contract.py`;
`scripts/stage-market-runtime.py` updates its integrity hash during packaging.

Recipient invoice timelocks (including 80 blocks) are supported within the 576-block total route budget. The final hop must match the invoice, and the incoming swap deadline includes the outgoing route delay plus the existing recovery margin. Invoice timing, amount, timelock and route refusals have specific messages; unknown or uncertain failures remain private.
An unknown recipient with no routing hints is reported separately: create a fresh invoice in the connected receiving wallet with private-channel route hints enabled. Increasing fees does not supply missing routes.

New automatically managed outgoing grants allow up to 576 blocks of route delay. Existing idle managed grants refresh automatically; active recovery and manual grants are preserved. Incoming swap deadlines still cover the actual outgoing route delay plus 144 recovery blocks and 6 propagation blocks. Fee and hop limits are unchanged.

Routine Lightning-node health messages, readiness and restart-status updates do not restart the Swap Controller. Discovery watches whether each node package exists; it still reacts to installation/removal, approved-grant publication changes and changes to its HTTPS bridge address. Existing approved swaps retain their saved recovery state.

Action failures now show transport/admission codes, the affected node and operation, response stage, HTTP/RPC codes when available, and a diagnostic reference. Unclassified local failures retain source locations for investigation. The latest 64 sanitized reports are retained in `mainnet-swaps-v1/action-diagnostics` on the data volume; credentials, invoices, raw response bodies and exception messages are excluded. A failed response does not prove a remote mutation failed: check history before retrying and never repeat a payment solely because an action errored.

Incoming admission errors now identify dust safety, liquidity, disconnected/busy channels and peer policy/hint failures. For dust failures the error shows the incoming amount and minimum, in the incoming node's units (BTC for BTC→XBT, XBT for XBT→BTC). Conversion can make a seemingly large XBT invoice quote too few BTC sats; increase the recipient amount to satisfy the live channel safety minimum.

Swap Status shows active swaps first, or the latest swap if none remain active. It no longer repeats the entire history and every grant limit. Swap History provides a newest-first selector and shows all details for the selected record; records are retained. Grant Status under Swap Setup contains the grant limits. This presentation-only update does not change payment deadlines or worker state.

Local node discovery now uses SDK daemon reconciliation instead of top-level restart subscriptions. Changed grants, credentials or bridge addresses restart only the local pairing helper; missing publications remove that helper until discovery recovers. The payment/recovery worker and wallet discovery server retain their processes. Pairing remains identity-verified and cannot replace saved authority during an active swap. Configuration is written privately only when the pairing helper starts. An explicit package update or user restart still restarts the controller.

Explain quoted grant amount limits with requested and maximum amounts in the correct asset.

### Wallet swap receiving

Wallet Receive API is a separate authenticated interface (port 9081; TLS via StartOS or HTTP over Tor). Public Wallet Discovery remains read-only. Under Swap Setup, enable Wallet Receive Access and copy its private key into the phone wallet’s saved-controller settings together with the Wallet Receive API origin. Enable the desired API address under Interfaces. Keys can be rotated or disabled without interrupting existing swap recovery; they are excluded from backups.

Lightning → Receive → Swaps creates a fixed-amount XBT invoice in the phone and requests a BTC invoice for the sender. The amount entered is XBT sats to receive. The caller explicitly requests automatic preparation/confirmation by creating the invoice; the API cannot pay arbitrary invoices, change settings, expose journals or administer nodes. Existing grant, fee, reserve, liquidity, worker, restore and expiry checks apply. A wallet key is private access to create BTC-to-XBT receive swaps, not a public directory credential.

POST /v1/receive accepts only action (create/status), original XBT invoice, environment and expected provider XBT public key. Repeated create requests reuse the exact original contract; status never creates or confirms a swap. Pending publication returns status without an invoice. The worker continues settlement/expiry when the phone closes. The phone validates the returned BTC invoice’s payment hash, amount, features and expiry before showing a QR. API errors retain finite diagnostic codes and source references; invoice/preimage/rune details are not logged.

This first integration retains the controller’s existing one-active-forward-swap and bounded history limits. Swaps require a fixed receive amount; ordinary XBT receiving still supports amountless invoices. The original XBT description is not copied into the controller’s BTC invoice. Public unauthenticated quote access and automatic XBT-to-BTC sending are not included.

Quote admission errors distinguish expired/replaced grants, channel recovery, and unfinished earlier enrollments. Automatic local trust refreshes idle authority; a recovery requirement must be resolved before new swaps. These errors do not authorize repeating a payment.
