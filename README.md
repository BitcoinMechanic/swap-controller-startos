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
# Experimental HTTPS controller recovery tests

The read-only 0.1.0:0 package passed live pairing, restart, backup/restore
invalidation and re-pairing. The tests below are the next integration stage;
they do not enable execution in the installed package.

`tests/remote/https_rpc.py` is a separate experimental transport. It is not
copied into the Docker image and accepts only `regtest` and `xbt-regtest`.
The production probe remains parameterless and read-only. Each experimental
RPC checks the expected node identity/network over verified HTTPS first.
Method and parameter allowlists reject unrelated calls before transport;
separate method-restricted runes enforce authorization on CLN itself. These
runes are not amount-limited spending capabilities. The pinned controllers
remain responsible for quote, payment, HTLC and amount validation.

Requests use a trusted CA, hostname verification, no environment proxies,
no redirects, bounded bodies and timeouts, and no automatic retries. Errors
preserve the controller's RPC exception class without raw response details.
A timeout or HTTP failure is never proof that a payment failed.

Place this checkout beside `btc-cln-startos` (its existing `tests/image_pair.py`
provides packaged node launch configuration), with the tested BTC and XBT
images loaded. On the packaging VM:

```bash
python3 tests/test_https_rpc.py -v &&
python3 tests/test_controller.py -v &&
bash scripts/test-remote-controller.sh \
  btc-cln:swap-preparation xbt-cln:recovery-test ../bitcoind all
```

The four scenarios are forward/reverse success and outgoing rejection.
Pinned source `81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe` supplies the
controller algorithms and original recovery assertions. The harness copies
that source into its disposable results directory and changes only the two
fixture child-command entry points. The child replaces `RPC.call` with HTTPS
and forbids subprocess execution; it has no CLI fallback. Harness setup and
independent assertions still use local CLIs. Every coordinator controller RPC,
including durable gate reads/resolution, crosses CLNRest HTTPS.

Both coordinators restart with payments pending. TLS endpoints, certificates,
node identities and restricted runes are retained. Existing fixtures verify
hook replay, one outgoing attempt, settlement/refund balances and no pending
HTLCs. A method-only audit additionally verifies one remote `sendpay` and one
terminal gate resolution. The harness checks CLN rejects `newaddr` and
`createrune` with the experimental runes.

Exercise an acknowledgement lost at the transport/controller boundary:

```bash
bash scripts/test-remote-controller.sh \
  btc-cln:swap-preparation xbt-cln:recovery-test ../bitcoind all --drop-send-reply
```

This intentionally exits the child after the remote `sendpay` response arrives
but before returning it to the controller. It tests recovery of the persisted
submission intent, not arbitrary TCP packet loss. Pending and completed
attempts must reconcile without another submission in a fresh process.

Docker uses `--network none` and mounts only the disposable test backend,
image binaries, fixture sources and results. No live credentials or wallet
volumes are mounted. Both chains run within one isolated container: this does
not yet prove inter-box partitions, live timing, routed payments, controller
backup of active swaps, or production remote execution. No s9pk rebuild or
live credential change is needed for these tests.
