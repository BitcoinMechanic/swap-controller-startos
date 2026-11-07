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
