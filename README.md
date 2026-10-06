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

## Backup pause reporting (0.1.0:3)

Old backups included `execution/backup-paused.json`, which could leave Worker
Status reporting a pause after restoration had finished. This release excludes
that transient marker from new backups. Restore commits `restored.json` first,
then removes any inherited pause marker from the restored snapshot.

For an already-restored installation upgraded from an older release, the status
reader recognizes the legacy marker and reports `backup_paused: false` with
`backup_pause_state: legacy_restored_marker`. This means the old marker is not
evidence of a currently running backup. It is not deleted during status checks
or normal startup. Both marker-existence execution guards and the restore barrier
are preserved. No permissions or pairing settings change on upgrade.

New backup operations write a versioned pause marker and report
`backup_paused: true` / `backup_pause_state: paused`, including when the restore
barrier exists. Completion removes that marker. Unreadable or unknown marker
contents continue to report paused. Ordinary status with no pause marker reports
`backup_pause_state: none`.

Validate this narrow lifecycle change with:

```bash
python3 tests/test_lifecycle.py -v
python3 tests/test_recovery_actions.py -v
npm run check && npm run build && node scripts/check-bundle.cjs
BUILDX_BUILDER=startos-builder make x86
```

After installing 0.1.0:3, Worker Status should show `restored_block: true`,
`worker_mode: restored`, and a fresh worker. For an existing inherited marker,
expect `backup_paused: false` and `backup_pause_state: legacy_restored_marker`.
Recovery Status remains blocked and pairing remains ready. No new restore or
funded swap test is needed to check this reporting correction.

## Packaged forward quote flow (disposable regtest)

The image now includes `/app/quote_workflow.py`. It accepts a recipient invoice
and an explicitly supplied BTC price, uses the pinned `swap_service.create`
validation, and persists a review before any quote registration or signing.
An explicit approval must match the full saved review digest. It registers the
unchanged terms and signs the BTC invoice through verified HTTPS using the
pinned publication code. A repeated approval returns the saved invoice.

The disposable workflow is:

1. `prepare`: save the validated recipient invoice, price, direct channel,
   expiry, coordinator identities and connection binding; return review fields.
2. `approve`: require the review digest and `confirmed: true`, recheck readiness,
   and publish the BTC invoice. Approval authorizes this exact swap to proceed
   when its incoming BTC HTLC becomes committed, before the quote expires.
3. The lifecycle worker waits for the quote gate's exact committed HTLC,
   validates the held amount, invoice, expiry and binding, then imports the
   resulting state into the existing executor and authorizes that intent.
4. The existing executor submits once and reconciles the original outgoing
   attempt. Fresh worker invocations report terminal state without another send
   or gate resolution. `btc_released` records gate release; the test independently
   verifies payer completion, recipient payment and all four channel balances.

Each CLI invocation takes `MANAGER MODE JOB`. Preparation reads JSON containing
`connections`, `xbt_invoice` and `btc_sats` from stdin. Approval reads `digest`
and `confirmed` from stdin. Status/step do not read a request. The fixture manages
these private inputs; do not supply installed pairing files or live credentials.

The new adapter permits quote reads plus BTC `xbt-register` and `signinvoice`.
It does not add these methods to the read-only pairing client or the execution
transport. Both coordinator identities are checked before quote RPCs; live
networks and missing disposable-container opt-in are refused. Endpoint changes,
changed review terms, an existing outgoing attempt, unsuitable direct-channel
liquidity, or an expired quote prevent publication/start.

Limits: BTC price at most 1,000,000 sats and XBT amount at most 1,000,000,000 msat;
fixed-amount BOLT11; one direct XBT hop; controlled regtest chains only. Price is
operator-supplied, not a market quote. The direct outgoing hop has zero routing
fee; the BTC payer's routing fee is outside the quoted price. The existing live
pricing, routing, deadline/on-chain and coordinator activation work is not
implemented by this adapter. No new StartOS action or public quote listener is
registered, and no live node credential changes are needed.

Private reviews, quote secrets and approval records live inside excluded
`execution/jobs` directories. Unresolved quotes refuse backup. Restore and backup
pause barriers are checked at preparation, publication, handoff and execution.
A failed/incomplete preparation remains blocked for inspection in the disposable
fixture. A lost registration/signing reply requires explicit publication resume
with the original saved terms; the worker cannot create a different quote.

On the packaging VM, run the new funded scenario (no full four-way recovery
rerun is needed for this step):

```bash
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind
```

The launcher first runs quote-policy tests against the actual pinned modules
inside the controller image. It then funds disposable nodes and starts a fresh
isolated controller container for each preparation, approval and worker step.
The controller has no CLN binaries, node volumes, RPC sockets, source-module
mounts or Docker socket. The node fixture creates the recipient invoice and pays
the published BTC invoice; it no longer supplies a prepared executor state.
The run checks repeated preparation/publication, waiting before BTC payment,
exactly one outgoing attempt and release, matching preimages, balances and
terminal repetition. Docker execution is verified on the packaging VM; local
unit tests alone are not evidence of a funded pass.

## StartOS quote actions (0.1.0:4)

The Quotes action group now has four actions:

- **Quote Status** lists local saved quote summaries. It performs no RPC and
  does not return invoices, runes, endpoint URLs, certificates or preimages.
- **Review Saved Quote** reads one quote ID and returns its recipient, BTC price,
  XBT amount, expiry and copyable review code. This does not grant approval.
- **Prepare BTC to XBT Quote (Regtest)** takes a new quote ID, recipient XBT
  regtest invoice and explicit BTC price. It saves terms without publication.
- **Approve and Publish BTC Invoice (Regtest)** takes the saved quote ID, exact
  review code and an explicit confirmation, defaulting to off. Approval permits
  the worker to send the reviewed XBT amount after the matching incoming BTC
  payment commits before expiry. The BTC regtest invoice is masked and copyable.

Preparation and approval remain blocked on installed live services, before any
job lookup, record creation or RPC. The UI cannot enable the disposable-regtest
flag or submit coordinator addresses/runes. Quote Status and Review Saved Quote
remain local inspection actions even after restore. A saved BTC release is not
independent proof of the payer's final settlement.

Disposable tests provision `execution/regtest-quote-nodes.json` privately. This
is separate from the read-only monitor pairing and is not configured by these
forms. The action loads only that fixed file for preparation; approval uses the
connections already bound to the reviewed quote. This file is excluded from
backups and removed during restore, after the independent restore barrier is
written. Job directories containing quote approvals/credentials remain excluded.
No live credential changes or manual edits to the installed service are needed.

`scripts/test-quote-flow.py` now runs preparation, local review and approval
through `/app/quote_actions.py`, the same fixed command used by StartOS. It runs
the quote and action policy tests inside the built image, then the funded BTC
invoice flow. Local SDK-form tests verify the registered actions, private stdin,
unchecked approval, invoice presentation and filtered errors. The funded fixture
still uses isolated controller containers with no node binaries, node volumes,
RPC sockets, source-module mounts or Docker socket.

After the funded action test passes, build `0.1.0:4` with
`BUILDX_BUILDER=startos-builder make x86` and install the resulting package. On
the existing live installation, verify Quote Status reports an empty quote list,
`live_payment_enabled: false`, and the existing restore-barrier state. Verify the
four Quotes actions are present and Connection Status remains ready. Do not enter
live invoices or credentials into the regtest forms. A restart should preserve
pairing and the restore barrier; another backup/restore cycle is not needed for
this action-only installation check.

## Reverse quote actions (0.1.0:5)

`Prepare XBT to BTC Quote (Regtest)` accepts a new quote ID, a recipient BTC
regtest invoice, and an explicit XBT price. The pinned reverse gate currently
requires exactly 100,000 BTC recipient sats and 200,000 XBT payer sats. These
are disposable test amounts, not an exchange rate. Other amounts are refused.
The price field has no default; there is no automatic pricing.

Preparation verifies both coordinator identities over HTTPS, the BTC invoice,
its expiry and CLTV, the direct outgoing channel and liquidity, and the single
incoming XBT channel. It saves immutable terms without registering a gate,
signing an incoming invoice, or sending a payment. Quotes are limited to 15
minutes and expire at least 60 seconds before the recipient invoice.

Use `Review Saved Quote` to inspect the direction, recipient, XBT price, BTC
recipient amount, expiry and review code. The shared approval action is now
named `Approve and Publish Swap Invoice (Regtest)`. Its unchecked confirmation
and exact review code are required in both directions. Reverse approval
registers the saved terms and returns a verified XBT invoice; repeating it
returns the saved invoice without another registration or signature. Payer
routing fees are additional. Publication interrupted before saving its result
requires another explicit approval of the same terms; the worker cannot publish.

The worker waits for the exact committed XBT HTLC and validates the durable
reverse gate, original terms, expiry and channel binding before handing off
to the existing reverse executor. The executor pays BTC once and resolves XBT
with the same preimage. Local Quote Status and Review Saved Quote omit invoices,
credentials and preimages. The explicit approval result masks the incoming
invoice and offers copy/QR output.

This release remains regtest-only. The installed read-only pairing does not
provide execution authority. Live preparation/approval remain blocked before
RPC, and restore barriers and backup exclusions remain in place. Controlled
regtest block margins are not a live cross-chain timing policy.

Validation on the packaging VM, after building `swap-controller:regtest`:

```bash
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind reverse
```

The driver runs both quote policy suites and action tests against the pinned
modules in the image, then funds one reverse scenario through the packaged
actions. It checks repeated preparation/approval, no spending before approval
and committed incoming payment, all four balances, matching preimages, no
pending HTLCs, and a terminal worker repeat without another mutation.
The optional final direction defaults to `forward` for existing commands.

After the funded test passes, build/install 0.1.0:5. Verify the new reverse
preparation form and renamed shared approval action, local quote status, and
unchanged pairing and restore-barrier state. An intentionally unpaired service
may remain unpaired.

## Live Swap Readiness (0.1.0:6)

The `Live Swap Readiness` action reports fresh, read-only observations from the
existing HTTPS pairing. It accepts no inputs and does not create or replace
credentials, save status files, enable gates, authorize execution, or alter
restore barriers. If unpaired, it reports `pair_nodes_first` without RPC.

With pairing configured, both node identities and expected live networks must
match before either node's channels are read. The action uses only `getinfo`
and `listpeerchannels`, with the existing dedicated read-only credentials,
certificate verification, no proxy, no redirect and no retry transport.
It reports warnings, normal/connected channel counts and pending HTLC counts.
Pairing changes during a check, a backward clock change or a check exceeding
120 seconds invalidate the observations. No endpoints, runes, certificates,
node IDs, channel details, HTLC hashes or raw errors are included in the report.

`Node connections verified` describes successful identity/channel observations
without node warnings. It does not establish routes, liquidity, timing safety
or spending authority. `Ready for live swaps` and `Live payments enabled`
remain false in this release, even when both node connections are healthy.

The current credentials cannot inspect swap gate activation. The action reports
that requirement as `not_verified_with_read_only_credentials`; it does not
claim that a remote gate is absent or disabled. Dedicated execution credential
configuration, live amount/fee/expiry policy, cross-chain timing policy and
live execution are not supported by this controller release. These are package
limitations, not findings about the remote nodes. Regtest configuration and
arbitrary files cannot satisfy these requirements.

Build/install 0.1.0:6 and run `Live Swap Readiness`. An intentionally unpaired
installation should report `pair_nodes_first`, outstanding live requirements,
and its existing restore barrier. To inspect the nodes, pair through the
existing read-only `Pair Coordinator Nodes` action and rerun readiness.
Re-pairing does not clear the restore barrier. No funded swap test is required
for this read-only addition.

## BTC gate observation credential

The BTC package 26.6.8:9 adds **Create or Show BTC Gate Observation Credential**,
**BTC Gate Credential Status**, and **Revoke BTC Gate Observation Credential**.
This separate rune permits only parameterless `getinfo` and `xbt-pilot-info`.
The existing monitor rune remains limited to `getinfo` and `listpeerchannels`.
Creation requires coordinator preparation and an active BTC gate. Lost creation
replies remain blocked; revocation targets only this rune and its derivatives.

In Swap Controller 0.1.0:7, use **Pair BTC Gate Observation** and paste that rune.
The saved BTC HTTPS endpoint and CA are reused; both paired node identities are
verified before saving. The credential is bound to the pairing generation, omitted
from controller backups and removed on restore. Replacing node pairing requires
pairing gate observation again. Status never exports the rune or endpoint.

**Live Swap Readiness** freshly verifies the BTC identity and `live-pilot-v1` gate
profile/count. This reports BTC observation only: XBT gate activation remains
unverified, live execution stays disabled, and restore barriers remain intact.
A successful observation does not prove liquidity, fee or cross-chain timing policy.

## XBT reverse gate opt-in and observation

XBT Core Lightning 0.1.0:15 adds **XBT Swap Gate Status** and
**Enable Bounded XBT Swap Gate**. Prepare Coordinator must already succeed,
and a connected normal channel with no pending HTLCs is required for activation.
Activation saves a receipt bound to this node, its wallet secret hash and the
exact pinned Python source bundle. Restart the service explicitly, then check
status again. The wrapper loads immutable image code; the durable gate journal
is stored at `xbt/swap-gate/reverse_gate.quotes.json`.

The reverse gate uses `reverse-live-v1`: 1,500 BTC sats payout, incoming XBT from
1 to 500,000 sats, at most 30 BTC sats routing fee, and one active quote at a time.
These are existing pilot bounds, not a market price or a controller authorization.
The forward BTC gate's `live-pilot-v1` limits are different. Ordinary payments
with unregistered hashes continue normally. This action publishes no invoice,
registers no quote, creates no credential and submits no payment.

Activation is excluded from backups. Restore writes a gate barrier before
removing activation and running existing recovery checks. The gate journal is
preserved; an existing journal without its activation cannot be silently reused.
There is no automatic barrier reset or gate-disable action for unresolved swaps.

After the gate is active, use **Create or Show XBT Gate Observation Credential**.
It permits only parameterless `getinfo` and `reverse-pilot-info`; its status and
revocation actions are separate from the existing monitor credential.
In Swap Controller 0.1.0:8, **Pair XBT Gate Observation** reuses the saved XBT
HTTPS endpoint and CA, verifies both node identities and binds the new credential
to the current pairing generation. Existing BTC gate pairing is preserved.
Controller backups omit both observation credentials and restore removes them.

Live Swap Readiness verifies the two profiles independently. XBT observation
reports `gate_active`, not a remote quote count (that RPC does not expose one).
Verifying both profiles removes only the gate-verification blocker: live
execution, execution credentials, amount/fee/expiry policy, cross-chain timing
and any restored execution barrier remain separate outstanding requirements.

## Proposed live pilot policy review (0.1.0:9)

**Review Live Pilot Policy** in Readiness is a local, parameterless action. It
makes no RPC calls, saves no approval and moves no funds. It works while unpaired
and does not read credentials or remove the restore barrier. Readiness reports
`proposal_available_not_enforced` for amount/expiry and timing policy; both
policy blockers remain until a live executor enforces them.

The numeric validator in `assets/live_policy.py` is a reusable pure function,
not a live execution path. Its versioned digest commits to the complete proposal
and source pin `81ba4099a63e5a0e83f55cead53c54f2a1b3c1fe`. Passing it only means the
supplied numbers match; invoice signatures, authenticated identities, route
structure, HTLC binding, spendable/receivable liquidity, reserves and current-fee
trim checks still require separate validation. No existing RPC credential scope
or regtest executor guard changes.

| Limit | BTC to XBT | XBT to BTC |
| --- | --- | --- |
| Active gate profile | `live-pilot-v1` | `reverse-live-v1` |
| BTC amount | Exactly 1,000 sats incoming | Exactly 1,500 sats paid out |
| XBT amount | Exactly 2,000 sats paid out | 1–500,000 whole sats incoming |
| Outgoing routing fee | Zero, one direct hop | At most 30 BTC sats, at most 8 hops; direct hop has zero routing fee |
| Outgoing route delay | 40 XBT blocks | 40–576 BTC blocks; recipient final CLTV at most 144 |
| Incoming remaining CLTV | 288–2,016 BTC blocks | BTC route delay + 150, up to 2,016 XBT blocks |
| Proposed incoming invoice CLTV | 300 BTC blocks | BTC route delay + 174 XBT blocks |

These amounts describe bounded pilot profiles, not a market exchange rate. The
controller proposal tightens quote lifetime to at most 120 seconds and requires
at least 60 seconds between quote expiry and recipient invoice expiry. Supplied
observations must be at most 120 seconds old and not from the future. Integer
fields reject booleans, strings, fractions and negative values; amounts use msat.

Reverse timing follows pinned `reverse-timing-candidate-v2`: expected relative
pace 1, six BTC submission blocks, 144 XBT recovery blocks and 24 XBT quote-drift
blocks. Independent chains have no bounded relative progress, so these are risk
budgets, not an atomicity guarantee. Absolute heights from different chains are
never compared. Forward policy also requires the pinned BTC deadline protection
at 72 remaining BTC blocks; this release neither installs a live deadline worker
nor authorizes channel closes. Both profiles require 50,000 confirmed unreserved
sats per coordinator and enforceable, untrimmed HTLCs before spending.

Expired quotes or elapsed block deadlines never establish payment failure and
never authorize refunding an unresolved incoming HTLC or resending an outgoing
payment. Recovery must reconcile the original attempt. Live execution remains
unsupported; viewing this proposal cannot remove any readiness blocker.

Validation: `python3 tests/test_live_policy.py -v`, `npm run check`,
`npm run build` and `node scripts/check-bundle.cjs`. No funded scenario is claimed
for this policy-only addition.

## Policy-bound regtest quote admission (0.1.0:10)

New quote reviews commit to `regtest-quote-admission-v1`, its direction and its
policy digest. The existing review code includes that commitment. Preparation
and approval use the same strict numeric checks; fresh preflight runs before
registration/signing and again before executor import. Quote Review exposes the
admission policy version and digest. Repeated approval returns the saved invoice
without registering or signing it again.

The checks bind the requested BTC price (forward), decoded recipient amount,
controller amount and direct route amount. A different route amount would imply
an unapproved fee and is rejected. Both fixtures require one hop, zero outgoing
routing fees, delay 40, recipient final CLTV 1–40 and gate limits 100–2,000 blocks.
The actual held expiry is rechecked against the incoming chain's freshly read
height before executor import; the two chains' absolute heights are never compared.
Invoice, channel and HTLC binding checks remain in their existing adapters.

Regtest limits remain distinct from the live pilot proposal: forward allows
1–1,000,000 whole BTC sats and up to 1,000,000 XBT sats; reverse retains exactly
200,000 XBT sats for 100,000 BTC sats. Forward quote lifetime is at most 600
seconds and reverse at most 900 seconds. Both require 60 seconds of recipient
invoice expiry headroom and preflight duration 0–120 seconds. Strict integer,
quote-window and same-chain remaining-block helpers are shared with the live
policy validator. These regtest bounds do not activate or prove enforcement of
the different live amount and timing limits.

A pre-upgrade unpublished quote without a policy commitment cannot receive new
publication approval; retain it for inspection and prepare a new job with a new
invoice. Existing published quotes keep their previous approval, and attempts
already imported into the executor retain recovery semantics. Admission expiry
checks are not reapplied to an already-started attempt: expiry never proves
failure and does not authorize a refund or resend. No records are migrated or
silently reapproved. Live readiness blockers, RPC credentials and restore
barriers are unchanged.

The `test-quote-flow.py` launcher now runs the admission rejection suite inside
the controller image before its funded scenario. Run both `forward` and `reverse`
against disposable packaged nodes. The fixture checks the displayed policy
commitment, single payment, final balances and repeat terminal recovery. Unit
tests additionally reject altered terms and policy digests before publication,
slow/backwards clocks and insufficient held margins; they verify expired
already-started attempts still reach the existing reconciliation path.
