# Swap Controller

This first version monitors your BTC and XBT coordinator nodes using read-only
credentials. It cannot quote swaps, send payments or move funds. It can run on
either StartOS box as long as that box can reach both HTTPS endpoints.

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

## Backups

This version excludes connection credentials and health snapshots from backups.
After restoring it, pair the nodes again. Existing credentials on the nodes are
not revoked by controller restoration. Store your restricted runes and public
CA files securely or retrieve them through each node's actions.

There is no web UI, inbound API or additional Lightning node in this package.
# Developer test: swap recovery over HTTPS

The installed controller remains read-only. Its live pairing, restart,
restore-to-unpaired and re-pairing checks have passed.

On the packaging VM, the next test uses disposable regtest coins and the
existing BTC/XBT images. Keep this repository beside `btc-cln-startos`:

```bash
python3 tests/test_https_rpc.py -v &&
python3 tests/test_controller.py -v &&
bash scripts/test-remote-controller.sh \
  btc-cln:swap-preparation xbt-cln:recovery-test ../bitcoind all
```

This covers settlement and outgoing failure in both directions, coordinator
restarts with payments pending, and fresh controller recovery over HTTPS.
The test creates its own method-restricted credentials and certificates.
It does not use your saved pairing or either live wallet.

Then repeat with the submission reply discarded before the controller receives
it. Recovery must find the original attempt without submitting another:

```bash
bash scripts/test-remote-controller.sh \
  btc-cln:swap-preparation xbt-cln:recovery-test ../bitcoind all --drop-send-reply
```

No package rebuild or installation is required. Successful tests establish
regtest transport compatibility, not readiness for live swaps.
# Developer test: separate controller container

The eight HTTPS regtest scenarios have passed. Next, run the controller in its
own container, with no node wallets, RPC sockets or Lightning binaries mounted.
The test also removes its networking during one pending recovery, checks that
the saved payment state is unchanged, then resumes with connectivity restored.

On the packaging VM:

```bash
python3 tests/test_container_boundary.py -v &&
docker buildx build --builder startos-builder --load -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all --disconnect-recovery
```

The small controller image is built from the existing Dockerfile. No CLN image
rebuild is needed. Temporary containers use a private internal Docker network
without published ports. Both coordinators remain together in the node-test
container; the controller is separate and communicates with them over HTTPS.
The launcher removes its containers and network on exit and retains disposable
logs in the printed directory. Leave the installed StartOS packages as they are.

### Packaged executor validation

The next development test puts the executor and its pinned swap logic inside the
controller image. Your installed StartOS controller continues monitoring only.
Do not install an update or enter new credentials for this test.

On the packaging VM:

```bash
python3 tests/test_executor.py -v &&
docker buildx build --builder startos-builder --load \
  -t swap-controller:regtest . &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor
```

The fixture authorizes one exact swap and retains its execution record across
container restarts. Recovery-only must refuse to start an unsubmitted swap.
After submission, network outages must preserve the journal; reconnecting must
settle or fail the original attempt without resending. These are disposable
regtest payments. Live execution and active-swap backup/restore remain disabled.

### Worker lifecycle (0.1.0:1)

This version adds **Worker Status** and an **Execution Worker** health check.
On your live installation the worker is running but execution remains disabled.
The existing read-only coordinator connections are unchanged.

A restore permanently blocks execution in this version, including any old
prepared authorization. You may re-pair the read-only monitor, but this does not
remove the execution block. No action enables live payments.

Backups refuse unresolved execution records. Only filtered terminal history is
backed up; execution credentials and authorizations are excluded. An interrupted
backup may leave execution paused and require inspection.

First run the lifecycle tests on the packaging VM:

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

After these pass, build the s9pk and verify Worker Status, restart, backup and
restore on StartOS. A restored worker should report execution blocked while
read-only pairing remains available.


### Stale-record inspection test

The optional `--stale-restore` regtest uses two synthetic copies of an old job,
from before and after its payment submission. It checks that each copy can
observe the original payment through restricted HTTPS while remaining blocked
from execution. Only the retained original journal settles the swap.

These copies are not StartOS backups. This is not yet a way to resume swaps after
losing their current journal. Re-pairing still restores read-only monitoring only;
no restore block is removed. No s9pk rebuild or installation is needed for this
test-only change.

On the packaging VM:

```bash
python3 tests/test_stale_restore.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker --stale-restore
```


### Lost current journal test

The next disposable test removes the current execution journal after its payment
has been submitted. A separate resolver uses the older prepared snapshot and
both coordinators' records to finish the existing swap. Its separate credentials
cannot send payments. The snapshot remains blocked from ordinary execution.

This does not enable restoring active swaps on StartOS. It tests direct regtest
channels with the original executor removed. No installed service changes,
rebuild or live credentials are needed.

On the packaging VM:

```bash
python3 tests/test_lost_journal.py -v &&
python3 tests/test_stale_restore.py -v &&
python3 tests/test_container_boundary.py -v &&
python3 scripts/test-separated-controller.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:regtest ../bitcoind all \
  --disconnect-recovery --packaged-executor --lifecycle-worker --lost-journal
```

All four success/failure cases should confirm that the original journal is
absent, the restore block remains, and only the original payment was submitted.


### Lost settlement/refund reply test

This extends the lost-journal test by terminating recovery immediately after the
coordinator accepts its settlement or refund request, before the controller can
record completion. A new container must recognize the existing outcome without
sending that request again. Both directions and both outcomes are tested.

On the packaging VM, using the existing images:

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

Each case should report that the resolution reply was discarded, a fresh
container reconciled the terminal gate, and the audit remained unchanged.
No rebuild or StartOS installation is needed.

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
