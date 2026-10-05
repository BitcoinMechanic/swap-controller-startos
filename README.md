# Swap Controller — read-only pairing pilot

StartOS 0.4 package, SDK 2.0.9, package ID `swap-controller`, version 0.1.0:0.
This initial package verifies BTC and XBT coordinator connections. It is not
a swap executor: it has no wallet, signing keys, quote API or inbound listener.
The intended packaging repository is BitcoinMechanic/swap-controller-startos;
create it before publishing. The CLN project remains the protocol upstream.

## Architecture and authority

One Python monitor polls the two saved HTTPS endpoints every 30 seconds.
Each call uses the node's dedicated restricted rune. The client only offers
getinfo and listpeerchannels, sends no parameters, rejects redirects and HTTP,
limits response size to 1 MiB, verifies TLS against the supplied CA, and checks
the expected node ID and chain before reading channels. The monitor does not
inspect rune restrictions itself; use the dedicated credentials created in the
BTC/XBT packages. Even a broader supplied rune does not add methods to this
client, but storing a broader credential would still be unnecessary authority.

Both nodes may be on remote StartOS boxes. No package dependencies or wallet
mounts are required. Configure HTTPS base URLs without query strings or embedded
credentials. The BTC upstream connection string may use clnrest+https and embed
a rune: manually reduce it to https://HOST:PORT before entering it here.
Reachability from the packaging VM does not guarantee reachability from the
StartOS box chosen for this controller; validate after installation.

## Pairing and persistence

Pair Coordinator Nodes accepts each HTTPS URL, expected public node ID,
read-only rune and public root CA PEM. Copy the CAs from authenticated StartOS
sessions. Runes are masked input fields and are passed to the helper on stdin,
not command-line arguments. Confirmation is required. Replacing different saved
connection details additionally requires the replacement toggle. Both new
connections must verify before the saved pair changes. Failures preserve the
previous pair. Repeating identical pairing preserves the generation.

/data on the main volume holds mode-0600 pairing.json (including credentials),
status.json and a lock. Writes use atomic replacement and fsync. Pairing and
probe cycles share a nonblocking exclusive lock; if an action overlaps a probe,
wait for the cycle to finish and repeat it. No node-mutating RPC is performed.
The service emits only a fixed private-safe event if a cycle cannot run.

Connection Status reports filtered node health. A report older than 120 seconds,
from a future clock or from another pairing generation cannot mark the service
ready. Healthy connections can have zero channels; ready does not mean liquid,
routable, synchronized with an exchange, or ready to execute swaps.

## Backup and restore

Backups omit pairing.json, status.json, locks and temporary files. Restore also
removes any existing pairing/status on the destination, requiring explicit
re-pairing. Keep the restricted runes and public CA files available separately
or recover them through each node's credential action. Restoring this package
does not revoke or alter credentials on either node. No swap state exists in
this release.

## Build and verify

```sh
python3 tests/test_controller.py -v
npm ci --ignore-scripts
npm run check
npm run build
node scripts/check-bundle.cjs
BUILDX_BUILDER=startos-builder make x86
```

Tests include a real local TLS server, untrusted CA and wrong identity rejection,
credential rejection, failed replacement preservation, private file handling,
exclusive locking, expiry and generation checks. Wrapper checks cover daemon
health, no interfaces and restore invalidation. Docker/StartOS installation and
backup/restore require the packaging VM and actual host; local tests alone do
not verify those paths. x86_64 is the initial supported package architecture.
