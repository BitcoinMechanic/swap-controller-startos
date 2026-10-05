# Swap Controller — read-only pairing pilot

StartOS 0.4 package, SDK 2.0.9, package ID `swap-controller`, version 0.1.0:1.
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
# Separate controller container fixture

All eight same-container HTTPS scenarios passed on the packaging VM, including
discarded submission replies. `scripts/test-separated-controller.py` adds an
execution-container boundary around those same controller algorithms.

Build the small existing controller image (no CLN rebuild), then run:

```bash
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all --disconnect-recovery
```

The host driver creates a temporary **internal** Docker network, with no
published ports, and one node-fixture container holding the two chains, the
coordinators and the payer/receiver nodes. Each controller step runs in a fresh
separate container using the controller image's Python runtime. The regtest
runner and pinned source are read-only test mounts, not installed execution
features of the production image. Node images are resolved to image IDs before
the test begins. Generated CLNRest certificates include the node container's
private interface IP, retaining full hostname verification.

Controller mounts contain only its private journal/credentials and read-only
Python source. It receives no node data directories, HSM keys, node databases,
RPC sockets, CLN binaries, Bitcoin binaries or Docker socket. Its root
filesystem is read-only, all Linux capabilities are dropped, and privilege
escalation is disabled. Runtime assertions check for unexpected binaries and
node paths. Only the host launches containers; no nested Docker is used.

The original harness still launches local CLIs for node setup and independent
balance/HTLC assertions. A private host mailbox replaces its controller child
invocations. It transfers only one controller journal, serializes calls and
refuses copying results over a concurrently changed original journal. A child
crash still copies back its persisted submission intent before the next
recovery step. This copying is a test adapter, not a production multi-writer
or distributed locking design.

With `--disconnect-recovery`, the first pending recovery runs in a controller
container with `--network none`. It must fail without altering its journal or
RPC audit. A new container on the internal network then resumes from that
same state. All original settlement/refund and one-attempt assertions remain.
This models all coordinator connectivity being unavailable during recovery;
it does not simulate a one-way partition during an in-flight HTTP mutation.
The earlier discarded-reply scenarios cover the separate submission-boundary
uncertainty. `partition.log` records disposable diagnostics if the outage
check fails. Logs and test data remain under the printed temporary directory;
some fixture files are owned by the container user.

No StartOS installation, live credential rotation, or mainnet RPC is involved.
This checks execution isolation and regtest recovery across a private Docker
network; it does not yet run each coordinator on a separate host or enable
the installed monitor to execute swaps.

## Packaged regtest executor (not enabled by StartOS)

The image now includes `/app/executor.py`, a separate regtest-only execution
component, and `/opt/swap` from immutable source commit
`81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe`. The default image command remains the read-only pairing monitor. StartOS also
runs a dormant lifecycle worker, without the disposable-regtest opt-in. There is no live execution action or new
network listener. No upgrade installation is required for this test.

An execution directory holds a private initial intent, exact HTTPS connections,
controller journal, expiring per-swap permit, and launch record. Preparation has
no RPC mutations. Authorization requires confirmation and the SHA256 digest of
the whole initial intent (including connections and source pin). The worker
persists launch intent before spawning the pinned controller. If it dies before
the controller leaves `prepared`, it refuses another launch and requires
inspection. Once submission is recorded, recovery does not depend on an unexpired
permit; it retains the original connections and journal. Locks serialize all
steps for one execution directory. Terminal repeats do not contact nodes.

This component deliberately supports the current direct-channel regtest fixtures.
It is not a generic importer of live swap journals or an on-chain/deadline worker.
Live network names are rejected before transport, even if the disposable opt-in
is set. The read-only probe client is unchanged. Backing up, migrating and
restoring active execution directories is not implemented; do not place them in
the installed monitor's data volume. The harness creates disposable directories.

The separate-container harness can now test the packaged implementation:

```bash
python3 tests/test_executor.py -v &&
docker buildx build --builder startos-builder --load \
  -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor
```

In this mode the execution container mounts only its private state directory and
a small test adapter; executor code, HTTPS transport and pinned controllers come
from the image. The adapter prepares/authorizes the initial fixture once, proves
that recovery-only cannot originate it, and copies journals back solely for the
existing balance assertions. Subsequent adapter invocations cannot overwrite the
executor's durable journal. The original harness mode remains available.

The CLI additionally provides `run` to poll execution subdirectories every five
seconds. Only individually authorized prepared records can start; begun records
recover without new permission. Errors are isolated per job. The lifecycle wrapper now has its own StartOS daemon. It is dormant on the live
installation; only the disposable fixture opts into regtest execution.

## Worker lifecycle, version 0.1.0:1

StartOS registers an independent worker daemon alongside the read-only monitor.
It polls `/data/execution/jobs`, but has no disposable-regtest environment opt-in,
so it never executes those jobs on the installed service. The new Worker Status
action reports a freshness-checked heartbeat and filtered per-job phases. Neither
status nor backup history exposes connection credentials, invoices or preimages.
The monitor's pairing and generation checks remain unchanged.

In regtest, the same lifecycle cycle calls the packaged executor. Jobs retain
individual authorization and journals across worker restarts. A shared lifecycle
lock spans each managed job step, including its child process; backup and restore
hooks cannot run across an active step. Missing or invalid records remain visible
for inspection, and one bad job does not stop the worker loop.

Backup refuses prepared, pending, uncertain or unreadable jobs. A successful
backup contains only filtered terminal history, excluding `execution/jobs` and
heartbeat/lock files. The pre-backup hook sets a pause barrier, and the post-backup
hook removes it. An interrupted backup can leave the worker paused; this is not
a reason to reset a payment record. The post-restore hook writes a permanent
restore barrier before invalidating read-only pairing. There is deliberately no
"resume restored swaps" action in this version. Even a restored authorized
prepared record cannot start. Re-pairing does not clear that barrier.

This does not implement recovery of active swaps from stale backups. Such backups
are refused. Execution remains regtest-only, and live network credentials are
still rejected by its transport. Pairing credentials remain read-only.

Validate before installing:

```bash
python3 tests/test_lifecycle.py -v &&
python3 tests/test_executor.py -v &&
npm run check && npm run build && node scripts/check-bundle.cjs &&
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker
```

The fixture uses explicit crash flags for fault injection; normal recovery steps
run the lifecycle cycle in fresh controller containers. It exercises the same
cycle as the long-running daemon, not a real StartOS container restart. Actual
StartOS restart and backup/restore validation follows a successful funded test.


## Synthetic stale-journal inspection (regtest only)

`--stale-restore` adds two deliberately stale copies: an authorized `prepared`
journal captured before launch and an `outgoing_started` journal captured after
submission. These are fault-injection snapshots containing disposable credentials,
NOT StartOS backups. Package backups still exclude jobs and reject active jobs.
Both copies receive the real lifecycle restore barrier. Direct execution,
recovery-only execution and worker cycles must all leave them blocked, including
when the stale prepared copy still has its original authorization.

A test-only observer checks both coordinator identities over verified HTTPS,
the decoded outgoing invoice, original gate hash/binding, reverse gate terms,
exact single outgoing record, amount, destination and applicable invoice binding.
Held gates must retain the original committed incoming HTLC. Missing, duplicate,
changed or inconsistent evidence is refused. Completion requires the matching
preimage, which is never returned in the filtered report. The observer has a
read-method allowlist and never rewrites the stale journal, grants authority,
resolves a gate, or clears a restore barrier.

The retained ORIGINAL journal alone proceeds through the existing executor.
The fixture requires stale copies to observe both pending and terminal outcomes,
and retains the existing one-submission, one-resolution and final-balance checks.
It does not demonstrate recovery after loss of the only current journal, active
backup support, cross-host ownership transfer, or mainnet restore recovery.
The installed service and package version remain unchanged.

Run on the packaging VM (the current lifecycle image already contains everything
needed; the new observer is mounted from the test directory):

```bash
python3 tests/test_stale_restore.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker --stale-restore
```

The test writes only disposable regtest files; do not supply live pairing files
or copy a real active journal into this harness.


## Lost current journal (resolution-only regtest)

`--lost-journal` is a separate fault-injection mode, requiring
`--packaged-executor --lifecycle-worker` and excluding `--stale-restore`.
It retains an authorized pre-submission synthetic snapshot, makes the original
outgoing attempt, then **deletes the entire current executor directory** after
the submission crash. Subsequent fresh controller containers use the stale
prepared snapshot and remote evidence, never the deleted journal. Node-fixture
state mirrors still exist for balance/assertion compatibility; they are output
views only and are not consumed by the resolver.

The test-only `lost_journal.py` resolver is mounted from `tests/remote`, not
installed in the controller image or exposed as a StartOS action. Its caller
must opt into disposable regtest and supply the exact saved intent digest.
Both coordinator identities, invoice, incoming gate and single outgoing attempt
are checked before resolution. The observed payment id/groupid/partid is pinned
in a separate reconciliation receipt. Forward held quotes additionally bind the
outgoing invoice and amount to `xbt-spend-info`, and verify the incoming fixture
amount/expiry. This is restricted to the existing direct-channel regtest
fixtures; it adds no live timing, deadline, on-chain, routing or ownership policy.

The node harness issues separate resolution credentials: read methods on both
coordinators and gate resolution methods only on the incoming coordinator.
Actual HTTPS requests prove these runes reject `sendpay`, `pay`, `withdraw` and
`createrune`, including after coordinator restart. The resolver independently
rejects all payment-submission methods. Existing broader fixture credentials
remain in the synthetic snapshot but are never used by the resolver.

Pending, missing, ambiguous and inconsistent outcomes never authorize resolution.
A complete outgoing record requires its matching preimage; definite failure
permits failure of the exact bound incoming HTLC. A durable resolution-intent
receipt is written before the RPC. If its reply is lost, a later invocation may
reconcile an already-terminal gate, but must not repeat a mutation while its
outcome remains unknown. The original stale state and its restore barrier stay
unchanged. The ordinary executor and lifecycle worker remain blocked on it.

This establishes a controlled recovery experiment, not supported active-job
StartOS backups or production restoration. In particular, the harness guarantees
that the original executor is gone; it does not establish distributed ownership
against another surviving controller. Package backups still exclude job journals
and credentials. The installed package, read-only monitor and version are unchanged.

```bash
python3 tests/test_lost_journal.py -v &&
python3 tests/test_stale_restore.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker --lost-journal
```

The four funded cases must still verify one outgoing submission, one gate
resolution, the original attempt identity, correct final balances and no pending
HTLCs. Lost resolution replies and refusal to retry an uncertain resolution are
covered by unit tests; the funded cases exercise normal resolution acknowledgments.


## Lost final resolution reply (regtest)

Add `--drop-resolution-reply` to `--lost-journal`. The resolution transport exits
with code 89 after a gate RPC has returned and its audit entry has been fsynced,
but before returning the reply to the resolver. The reconciliation receipt must
still be `resolution_intent`; the fixture mirror must still be pending and lack
a preimage. This simulates loss at the transport-to-controller boundary, not a
server-side packet drop.

The host verifies that crash point in a separate network-disabled container,
then launches a fresh recovery container with the fault disabled. Recovery must
read the coordinator's terminal gate and finish the original reconciliation.
Another network-disabled check requires an identical RPC audit, immutable stale
snapshot, unchanged attempt/binding, retained restore block, absent original
executor directory and a terminal reconciliation receipt. Each scenario must
exercise exactly one such crash; merely completing without reaching it fails.
The existing balance, original-payment and pending-HTLC assertions still apply.

The new checker contains no RPC calls. Unit tests exercise real Python process
exit for all four gate methods and reject changed snapshots, duplicate audit
mutations and premature terminal checkpoints. Funded validation uses the same
fault in the isolated controller against the packaged CLN nodes.

```bash
python3 tests/test_resolution_reply.py -v &&
python3 tests/test_lost_journal.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply
```

No service image rebuild or StartOS upgrade is required. All changes remain in
the test harness; installed live execution and restore behavior are unchanged.

## Packaged resolution-only recovery (regtest)

The image now includes `/app/recovery.py` and `/app/recovery_inspection.py`.
The command requires `BTC_XBT_DISPOSABLE_CONTAINER=1`, a saved intent digest,
separate resolution-only credentials, and `--confirm-resolution-only`. Both
saved connections must be regtest networks. It retains the restore barrier,
never submits an outgoing payment, and does not alter the original snapshot.
This is a disposable-fixture command, not a production recovery workflow or
an ownership handoff. Forward recovery still requires the exact tested fixture
terms; it is not a general recovery tool for arbitrary swaps.

Only filtered phase/status fields go to stdout; errors withhold RPC details.
Private compatibility results (including a successful preimage) are written
with mode 0600 at `jobs/swap/recovery-result.json`. Recovery records its own
mutation audit at the snapshot manager's `recovery-audit.jsonl`. The test adapter
copies new audit entries into the fixture audit and propagates exit 89 when
`DROP_RESOLUTION_REPLY=1` deliberately discards a resolution reply.

`--packaged-recovery` on the separated-container driver requires `--lost-journal`.
It invokes `/app/recovery.py` in a fresh process instead of importing the mounted
resolver, including after a lost reply. The original executor journal remains
absent, and the stale executable state remains blocked. The installed monitor,
lifecycle worker, StartOS actions and live credentials remain unchanged.

Rebuild the disposable controller image and run:

```sh
python3 tests/test_packaged_recovery.py -v &&
python3 tests/test_container_boundary.py -v &&
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery
```

No StartOS reinstall is required for this disposable-image validation.

## Old-controller revocation fixture

`--owner-fence` extends the packaged lost-journal recovery test. Before returning
from the first submission crash, the node-side fixture administrator revokes the
original execution rune on each coordinator using `blacklistrune`. The controller
never receives administrative RPC access. Both node identities and credential
bindings are checked before revocation, and replacement recovery waits for both
revocations to be verified.

The fixture retains the original executable journal under `old-owner/jobs/swap`,
with its original credentials, launch record and permit. It has no local restore
barrier. Both coordinators subsequently restart, and the fixture verifies their
blacklists again. Original and derived credentials must receive HTTP 401/403;
network errors or parameter-validation errors do not count. An unrelated read-only
credential and the separate recovery credential must still work on each node.

Before each replacement recovery step, the old packaged executor is run again.
It must fail without changing its payment state, intent, credentials, permit,
launch record or RPC audit. The replacement then uses the packaged resolution-only
command. Existing lost-reply, balance, single-attempt and restore-barrier assertions
remain in force. The active execution directory is removed as before, but the
retained old copy makes this explicitly a surviving-old-journal test.

This checks revocation of these known credentials only. It does not cancel RPCs
accepted before revocation, establish a distributed ownership lease, or cover
unknown independent credentials. The fixture deliberately pauses after submission
before revocation. It is not a production takeover procedure. If a revocation or
verification fails, the run stops without authorizing the replacement; it does
not undo an already completed revocation on the other node.

On the packaging VM, reuse the controller image built for patch 0009:

```sh
python3 tests/test_owner_fence.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 tests/test_packaged_recovery.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery --owner-fence
```

No image rebuild, StartOS installation or live credential change is involved.

## Accepted submission with lost reply before revocation

`--drop-submission-reply` requires `--owner-fence`. With both disposable-fixture
opt-ins enabled, the packaged executor enables a one-shot fault only for the
initial `--crash-after-sendpay` invocation. After the HTTPS adapter receives the
coordinator's successful sendpay response and fsyncs its mutation audit, it writes
`submission-reply-lost.json` and exits 88 before returning to the controller
algorithm. The original journal remains `outgoing_started` without a preimage.
The ordinary acknowledged-submission crash cannot satisfy the marker assertion.

The node-side administrator verifies this marker in the retained old journal
before revoking credentials. Both coordinators then restart. Recovery must find
the one accepted outgoing attempt using separate resolution-only credentials,
while the old executable journal and revoked credentials remain available but
are refused. The existing dropped final-resolution reply test can run in the
same scenario. Original attempt, channel balances, no remaining HTLCs, one gate
resolution and retained restore barriers are still checked.

This models a response discarded inside the adapter after coordinator acceptance,
not arbitrary packet loss or an HTTP request still executing during revocation.
It does not enable live execution or provide a production ownership handoff.

Rebuild the disposable image because the packaged executor and adapter changed:

```sh
python3 tests/test_submission_reply.py -v &&
python3 tests/test_executor.py -v &&
python3 tests/test_container_boundary.py -v &&
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery \
  --owner-fence --drop-submission-reply
```

No StartOS installation or live credential change is required.

## Partial coordinator revocation (disposable fixture)

`--partial-fence` requires `--owner-fence`. The node-side fixture validates both
coordinator identities, revokes the first original credential, then temporarily
renames the second coordinator's Unix administration socket before trying its
revocation. The actual CLI request must fail. Lightning and HTTPS keep running;
this is loss of the administration endpoint, not a complete node/network outage.
The first credential must remain revoked and the second original credential must
still work over HTTPS. No completed fence receipt is published.

A fresh controller container then invokes the replacement adapter twice. Both
calls must stop at the missing two-coordinator fence receipt before any recovery
RPC. Journals, credentials, restore state and RPC audits must remain byte-for-byte
unchanged. A missing unrelated file or an arbitrary failure is not accepted as
proof that the intended barrier worked.

Only after that check does the node fixture restore the same socket inode. It
checks that exactly the first original rune is revoked, then runs the normal full
revocation verification. Both coordinators subsequently restart and the existing
four recovery scenarios continue, including discarded submission and resolution
replies. No payment is resubmitted. The socket manipulation is confined to the
regtest node directories under `/results` inside the disposable node container.

This is test orchestration, not a production takeover action. A production
protocol for partial revocation and requests already in flight is still needed.
Reuse the disposable image built for patch 0011:

```sh
python3 tests/test_partial_fence.py -v &&
python3 tests/test_owner_fence.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery \
  --owner-fence --drop-submission-reply --partial-fence
```

No image rebuild, StartOS installation or live credential change is required.

## Packaged regtest recovery admission

The `recovery_workflow.py` entry point records explicit operator confirmation
separately for BTC regtest and XBT regtest. It binds the record to the immutable
swap intent and the exact replacement credential set. Confirmation verifies the
replacement node identity over HTTPS, rejects an old rune that still works, and
requires authentication refusal for replacement `sendpay`, `pay`, `withdraw`,
`createrune`, and `blacklistrune` probes. An outage, parameter error or HTTP 500
cannot count as credential rejection. Probes use empty parameters in disposable
regtest only; they are not a general production rune-policy verifier.

The first confirmation survives an interrupted second confirmation. Resolution
requires both confirmations and fresh verification of both nodes. Each subsequent
step rechecks access; saved readiness is not perpetual authorization. Status
contains no credentials, endpoints or raw errors. The restore barrier remains in
place and this workflow cannot submit a new outgoing payment.

The administrator still revokes the old parent runes outside the controller.
Confirmation is explicit operator input plus access checks, not a signed proof of
revocation or a distributed ownership lease. The controller receives no admin
credentials. The existing lower-level regtest resolver remains for regression
coverage; this admission record is not an OS security boundary against a process
with direct access to the same credentials.

`--owner-fence --packaged-recovery` now drives the packaged admission command in
fresh controller processes before resolution. With `--partial-fence`, the test
also persists the first confirmation while the second RPC socket is unavailable,
checks that the second confirmation fails and resolution refuses twice, then
restores the socket and completes fencing. CLNRest also needs that socket, so the
second old credential is checked before the outage and after socket restoration.

Rebuild the test image before this test:

```bash
docker buildx build --builder startos-builder --load -t swap-controller:regtest .
python3 tests/test_recovery_workflow.py -v
python3 tests/test_packaged_recovery.py -v
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery \
  --owner-fence --drop-submission-reply --partial-fence
```

This changes the disposable test image only. No StartOS installation, live
execution, automatic revocation or new StartOS action is introduced here.

## StartOS recovery actions (0.1.0:2)

Three Recovery actions now use the packaged recovery boundary:

- **Recovery Status** inspects local jobs and shows the job ID, immutable digest,
  saved coordinator confirmations and reconciliation stage. It makes no node RPC
  calls. Saved confirmations never imply current authorization or readiness.
- **Confirm Recovery Revocation (Regtest)** verifies and records one coordinator
  for an existing disposable job. Supply the reviewed digest and both separately
  issued resolution-only runes. Both rune fields are masked and have no saved
  prefill. Reuse the identical replacement credential pair across confirmations.
- **Recover Existing Swap (Regtest)** runs one reviewed step only after both
  confirmations and fresh access checks. Pending or uncertain outcomes never
  authorize a new outgoing attempt, automatic refund or removal of the restore
  barrier.

The normal StartOS installation supports status inspection only. The two
execution actions return an explicit regtest-only message before job lookup,
record changes or RPC. There is no UI switch that enables execution. No live rune
needs to be entered to validate this release. An empty jobs list is expected on
the installed read-only monitor; these actions do not import or create jobs.

Requests go to a fixed Python entry point as JSON on standard input, never as
shell commands or credential-bearing command arguments. The backend constrains
job IDs and paths, checks the reviewed digest, and takes endpoints and identities
from the existing immutable record. Submitted runes stay in memory; the action
does not persist them or change read-only pairing. Execution jobs remain excluded
from backups, and restore still blocks automatic execution.

The funded owner-fence tests now invoke `/app/recovery_actions.py` in fresh
containers, using the same JSON protocol as the StartOS handlers. Partial-outage
checks also enter through this action boundary. TypeScript tests verify the form
masking, default confirmations, fixed command and private stdin. Python tests
cover local status filtering, path and input rejection, production refusal,
partial admission and settlement through the action handler.

Validate and rebuild the disposable image before packaging:

```bash
python3 tests/test_recovery_actions.py -v
python3 tests/test_recovery_workflow.py -v
python3 tests/test_packaged_recovery.py -v
npm run check && npm run build && node scripts/check-bundle.cjs
docker buildx build --builder startos-builder --load -t swap-controller:regtest .
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker \
  --lost-journal --drop-resolution-reply --packaged-recovery \
  --owner-fence --drop-submission-reply --partial-fence
```

After the funded tests pass, build/install 0.1.0:2 and inspect Recovery Status,
Connection Status and Worker Status. Pairing and the restore barrier must persist
across upgrade and restart. No new live payment or recovery permission is added.
