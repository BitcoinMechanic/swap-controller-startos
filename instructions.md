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


## Read-only direct live candidate inspection

`assets/live_preflight.py` is a packaged CLI module, not a StartOS action or
worker entry point. It checks a proposed direct-channel candidate in either
live direction using fresh HTTPS observations. No new installed action or
package version is introduced by this development checkpoint.

The command is `python3 /app/live_preflight.py /data inspect`, with one JSON
object on standard input containing `candidate` and `credentials`. It loads
the existing monitor pairing for endpoint, CA and node identity; the two
separate inspection runes are provided as `credentials.btc` and
`credentials.xbt`. Nothing is saved. Do not put credentials in shell arguments,
logs or pasted output. Dedicated credential provisioning is still pending;
do not broaden the existing monitor credentials or substitute admin runes.

Required candidate fields are `policy_digest`, `profile`, `btc_amount_msat`,
`xbt_amount_msat`, `invoice`, `incoming_channel`, `outgoing_channel`,
`route_delay_blocks`, `quote_expires_at`, and `incoming_expiry`. Amounts and
heights must be integers. Channel identifiers are exact short-channel IDs.
Use the current live policy digest and either `live-pilot-v1` or
`reverse-live-v1`. The incoming expiry is a proposed absolute height on the
incoming chain; it is not evidence of an actual held HTLC.

The independent inspection transport only permits `getinfo`, `listfunds`,
`listpeerchannels`, `decode(string)` and `listsendpays(payment_hash)`.
It uses verified HTTPS, disables redirects/proxies and makes no retries.
Both coordinator identities and networks are checked before sensitive reads
and again at completion. The pairing must remain unchanged. Reports expose
only fixed reason codes, numeric summaries and digests, never credentials,
endpoints, invoices, payment hashes or raw remote failures.

Inspection verifies the node-decoded signed invoice's currency, recipient,
amount, secret/hash shape, expiry and final CLTV; the outgoing channel must
lead directly to that recipient. Both exact channels must be connected and
normal with no pending HTLCs, enough incoming/outgoing liquidity, and an amount
above the conservative current-fee dust threshold. Both wallets require at
least 50,000 confirmed unreserved sats. Any existing payment attempt for the
hash is refused. Fresh heights feed the proposed policy's same-chain remaining
block calculation, with a 120-second observation bound and quote lifetime.

`preflight_matches` is an observation only, never an execution approval.
There is no routing discovery or multi-hop validation in this module. The
snapshot is not atomic and cannot reserve liquidity, guarantee future fees or
relative chain progress, or authenticate an incoming HTLC that does not yet
exist. It must be repeated and extended before any future live submission.
Gate admission, deadline protection, on-chain recovery and execution authority
remain separate requirements. All live readiness blockers and restore barriers
remain unchanged; this inspector cannot remove them.

Run `python3 tests/test_live_preflight.py -v` and
`python3 tests/test_live_policy.py -v` to validate the inspector and its policy.
These tests use injected RPC fixtures, not funded live nodes.


## Live preflight actions (0.1.0:11)

First install BTC 26.6.8:10 and XBT 0.1.0:16, then explicitly create each node's
**Inspection Credential** under Coordinator Preparation. The credentials only
allow invoice decoding, node/channel/reserve inspection and payment-history
reads. They do not replace the monitor or gate-observer credentials.

Under Readiness, use **Inspect BTC to XBT Candidate** or **Inspect XBT to BTC
Candidate**. Paste both masked inspection credentials, the recipient invoice,
and the incoming and direct outgoing coordinator channel short IDs. They are
passed through stdin and never saved by the controller or prefilled on repeat.
The existing pairing supplies the endpoints, certificates and expected IDs.
No additional configuration file is created or backed up.

For BTC to XBT, the recipient invoice must request 2,000 XBT sats; the proposed
BTC price is fixed at 1,000 sats. For XBT to BTC, the recipient invoice must
request 1,500 BTC sats; enter a proposed whole-satoshi XBT price between 1 and
500,000 sats and a direct BTC delay between 40 and 576 blocks (default 40).
These pilot prices are not a market exchange rate. Use an invoice issued by
the peer on the chosen outgoing channel; routed invoices are unsupported.

The action builds a candidate using the current policy digest, a 120-second
quote lifetime, and a proposed incoming expiry based on the observed incoming
chain height. Forward uses 300 BTC blocks; reverse uses the selected BTC delay
plus 174 XBT blocks. The inspector checks the remaining margin again using
later heights; slow observations can therefore reject the candidate. No actual
incoming invoice, quote registration, HTLC or payment is created.

A false inspection result includes a privacy-safe reason code. Insufficient
reserves, insufficient directional channel liquidity, a small amount trimmed
at current fees, a reused payment hash or a stale observation are real blockers;
do not relax the policy just to make the report pass. A true result remains
non-authorizing and does not verify a held HTLC or deadline protection. Live
readiness blockers and the restore barrier remain unchanged. Do not pay any
invoice as part of this inspection procedure.

This release adds a UI for the previously CLI-only inspector. Validate using
`python3 tests/test_preflight_actions.py -v`, the existing preflight and policy
tests, and `node scripts/test-preflight-actions.cjs`. Real-node verification is
a separate installation check; mocked RPC tests do not establish live readiness.


## Isolated deadline RPC boundary (development checkpoint)

`deadline_boundary.py` adapts the pinned forward and reverse deadline guards to
a separate, regtest-only HTTPS transport. It is not registered with the installed
worker or exposed as an action. There is no live activation or version bump.
The existing executor and monitor transports are unchanged.

The adapter requires disposable-regtest opt-in, both expected regtest identities,
and an immutable specification of the incoming funding output, channel, committed
HTLC, payment hash, amount and expiry. It also requires a unique pending outgoing
attempt matching its hash, group ID, part ID and amount sent. The only mutation
permitted by this transport is `close` of that exact incoming channel with a
one-second unilateral timeout. It cannot send, fail or release payments.

The pinned guards' 30-block regtest threshold is used without changing the live
72-BTC-block / 144-XBT-block policies. The adapter writes its own private
`deadline.json` before close submission. A lost close reply is reconciled from
CLN channel state; a closing/on-chain channel is not closed again. Changed
funding/HTLC bindings, stale reads, restore barriers and live networks are refused.
The bridge does not spawn a local CLI or require node volumes or sockets.

Unit fixtures exercise both pinned guards through this adapter. They test
intent-before-close persistence, crashes, lost replies, original funding and
HTLC binding, pending-attempt checks and mutation refusal. These are injected
RPC tests, not funded-channel or on-chain recovery tests. The result explicitly
reports `onchain_claim_verified: false`.

Force-closing alone is not proof of recovery. Packaging preimage claims,
confirmations, fee management, terminal reconciliation and protection during
controller/network outages remain necessary before live execution. In
particular, a controller-polled guard cannot act while that controller is down.
The pinned reverse state flag required by its guard is only a fixture input;
it does not attest that the packaged service can claim funds on-chain.

Validate the exact image contents with:

```bash
docker buildx build --builder startos-builder --load -t swap-controller:deadline-boundary .
docker run --rm --network none --entrypoint python3 \
  -v "$PWD/tests:/tests:ro" swap-controller:deadline-boundary \
  /tests/test_deadline_boundary.py -v
```

This test mounts only read-only test source, creates disposable local journals,
and does not contact nodes, close real channels or alter installed services.


## Funded packaged BTC deadline fixture

`test-funded-deadline.py` exercises the packaged deadline adapter against funded
BTC/XBT regtest channels over verified HTTPS. It runs two independent disposable
fixtures: a normal close response and process exit after the server has closed
the channel but before the adapter receives its response. Fresh controller
containers reconcile the saved intent without another close request.

The forward fixture keeps the original XBT attempt pending and holds XBT height
fixed while BTC advances. At 31 remaining BTC blocks the channel stays open; at
30 blocks the packaged guard closes the exact pinned BTC channel. The private
journal must retain the original channel funding, HTLC and outgoing attempt.
The fixture then confirms the unresolved commitment, lets the XBT recipient
settle, releases the BTC hook with the learned preimage, and checks the confirmed
HTLC-success witness, payer settlement and CSV sweep to the operator wallet.

This tests packaged deadline decisions and close RPCs. Funding, submission,
recipient cooperation, gate release and claim/sweep verification are performed
by the node fixture using local RPC. They are not packaged recovery operations.
The lost-response fixture stores the response separately for its on-chain
verifier; the adapter only sees its original intent and fresh node state.
No claim of protection during controller downtime or an independent chain stall
is made. Reverse funded deadlines and packaged claim recovery remain pending.

The controller container gets only its disposable state and read-only test
helpers, with no CLN binaries, node volumes, RPC sockets or Docker socket.
Its dedicated runes allow deadline observations and, on BTC, the close method;
exact channel targeting is enforced by the adapter rather than a rune parameter
restriction. The fixture checks server-side rejection of payment, withdrawal,
credential creation and BTC gate resolution with these runes. Broad fixture
credentials are removed from the controller volume before it runs.

CLN omits `partid` for part zero. The adapter now accepts only that documented
missing-field representation as zero; all other attempt fields stay required,
and explicit malformed or mismatched part IDs remain rejected.

On the packaging VM, after applying this patch:

```bash
docker buildx build --builder startos-builder --load -t swap-controller:deadline-boundary . &&
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind
```

The launcher checks the packaged adapter first, then runs both funded scenarios.
An optional final `normal` or `lost-reply` selects one fixture for troubleshooting.
Logs and private fixture evidence remain under the printed disposable directory.
No StartOS version bump or installation is needed; live execution remains disabled.


## Reverse funded packaged deadline fixture

The funded deadline launcher now includes `reverse-normal` and
`reverse-lost-reply`, or `reverse` to run both. The existing `all` selection
runs all four directions/response cases. Forward normal and lost-reply runs
passed at checkpoint `07eaa6d` with fixture-assisted claim verification.

The reverse fixture uses the pinned durable reverse quote gate and an ordinary
XBT payer invoice. It holds the original 100,000-sat BTC recipient payment
pending while advancing only XBT. The packaged reverse guard must leave the
bound XBT channel open at 31 blocks and close it at 30 blocks. Its durable
intent must retain the full funding pin. Fresh containers reconcile both saved
and deliberately discarded close responses without issuing another close.
The adapter can close only the incoming role; the outgoing role's rune also
rejects the close method at the server.

After the XBT commitment confirms with the unresolved 200,000-sat HTLC, the
fixture resumes the BTC recipient, obtains the original attempt's preimage,
and resolves the original reverse gate binding. The pinned on-chain verifier
checks the XBT HTLC-success witness, original payer settlement, CSV maturity and
confirmed wallet sweep. BTC height must remain fixed, BTC balances must reflect
the agreed amount, and the original outgoing attempt must remain unique.

Run just the two new cases on the packaging VM, reusing the controller image
built for the previous funded test:

```bash
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind reverse
```

This change is limited to mounted test helpers and documentation. No service
image rebuild or StartOS installation is needed. Node-fixture RPC still performs
submission, gate resolution and claim verification. Packaged claim recovery,
controller-downtime protection and live execution are not enabled by this test.


## Packaged post-close gate recovery (regtest only)

`deadline_recovery.py` adds an explicit resolver for the saved deadline record.
It is separate from the normal-channel stale-journal resolver and does not relax
that resolver's channel checks. Both funded deadline directions now use it to
recover the outgoing preimage and resolve the bound incoming gate after close.

Before a release it requires the matching immutable deadline specification,
source pin and original durable close intent, verified identities on both
regtest nodes, the original unique completed outgoing attempt, a preimage that
hashes to the original payment hash, matching gate binding/amount/expiry, and
the original funding-pinned channel reported ONCHAIN by CLN. Pending outgoing
payments remain pending. Failed outgoing payments are refused; this helper
cannot refund, submit, close, withdraw or clear restore barriers.

The resolver saves release intent before the gate RPC. A lost release reply is
reconciled only if the gate is durably resolved; a still-held gate after an
uncertain release stops for inspection instead of repeating the mutation.
Separate receipts record the attempt and phase without storing the preimage.
The original deadline journal remains unchanged. Terminal reconciliation also
works after the verified sweep without another release.

The test replaces close credentials with separately issued release-only runes
before invoking fresh recovery containers. The fixture checks server-side
rejection of send, withdrawal, close and gate-failure methods with those runes.
The normal modes retain close/release replies; lost-reply modes discard both
responses before the respective packaged algorithm receives them.

The package recovers the preimage and releases the gate to CLN's on-chain
machinery. It does not construct transactions or independently prove that a
claim/sweep confirmed: those checks remain in the pinned node fixture. The
recipient cooperation and controlled chain advancement are also fixture tasks.
There is no live execution, unattended watcher or downtime guarantee here.

Rebuild the test image and run all four cases on the packaging VM:

```bash
docker buildx build --builder startos-builder --load -t swap-controller:deadline-boundary . &&
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all
```

No StartOS version bump or installation is required. The installed monitor and
its read-only credentials are unchanged.


## Packaged read-only regtest claim verification

`assets/claim_verification.py` inspects the completed post-close claim using
separate read-only HTTPS credentials. It starts from the saved funding outpoint,
payment hash, amount and original outgoing attempt. The fixture does not supply
claim or sweep transaction IDs to the verifier.

The verifier parses raw transactions locally, recomputes transaction IDs, links
the commitment to the funding output, checks the HTLC-success preimage and P2WSH
witness, and follows the delayed output into a confirmed wallet sweep. It checks
the pinned non-lease CSV script, sequence and reported confirmation-height gap.
The bounded fixture requires the delayed output and wallet return to retain more
than 80 percent of their respective input amounts. This is a test constraint,
not a general fee policy or support for arbitrary channel scripts.

Confirmation heights and wallet ownership are supplied by the authenticated CLN
node. This is not an independent chain proof, signature/script interpreter or
reorg protection. Each run reads the evidence again; an earlier success is not
reused when confirmations disappear. The observation explicitly reports
`confirmation_source: paired_cln` and `independent_chain_proof: false`.

Incoming inspection credentials permit only `getinfo`, `listtransactions` and
`listfunds`; outgoing credentials permit only `getinfo` and `listsendpays`.
The verifier container receives no close or gate-resolution credential. The
fixture checks that these credentials reject spending, closing and resolution.
Only the private observation file is written; deadline and recovery journals
remain unchanged. Reports omit raw transactions, preimages and credentials.

The funded harness now checks a pending observation before claim confirmation,
then compares the packaged verifier's discovered transaction IDs with the
independent fixture result after the CSV sweep. A fresh verifier repeats the
terminal inspection. Run all four cases on the packaging VM after rebuilding:

```sh
docker buildx build --builder startos-builder --load \
  -t swap-controller:deadline-boundary .
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all
```

All four funded verification cases passed at checkpoint 833ddae. This adds no StartOS
action, live execution authority, restore-barrier exception or package version
change. The verifier is restricted to disposable regtest fixtures.


## Funded regtest sweep confirmation rollback

The deadline fixture now invalidates the incoming-chain sweep block after a
successful packaged inspection. The earlier commitment and HTLC-success remain
confirmed. It mines coinbase-only replacement blocks past the old tip so CLN
can detect the changed predecessor. It waits for both nodes to observe that
branch and for the sweep to lose confirmation before running two fresh verifier
containers. Both must report `awaiting_csv_sweep`, `verified: false`, and no
retained proof in the saved observation.

The fixture then mines a replacement block containing the same sweep. Two fresh
inspections must recover verification. It checks that the replacement block hash
is different, the outgoing chain height and original payment attempt are
unchanged, and the close audit, release audit, deadline journal, claim receipt
and gate journal remain byte-for-byte unchanged through rollback and recovery.
Backend invalidation/mining is fixture-only; the packaged verifier has no such
RPC authority and receives no close or resolution credentials.

Run the existing four-case harness on the packaging VM:

```sh
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all
```

This patch changes fixture tests and documentation, so the controller image from
the claim-verification checkpoint can be reused. All four funded sweep rollback cases passed at checkpoint 5dda471. The test covers loss and replacement of the sweep confirmation only;
it does not establish deep-reorg safety, independent chain proofs, confirmation
finality, or recovery after the commitment/HTLC-success itself is disconnected.
Live execution remains disabled, and the installed StartOS package is unchanged.


## Funded regtest HTLC-success confirmation rollback

After the sweep-only rollback, the fixture now disconnects the HTLC-success
block and its descendants while retaining the original commitment. It mines an
empty replacement branch past the old tip, waits for CLN to report both success
and sweep as unconfirmed, and runs two fresh read-only verifiers. Both must
replace the saved success with `awaiting_htlc_success` and no retained proof.

The fixture selects the original raw HTLC-success transaction into a new block
before the original expiry. Inspection then reports `awaiting_csv_sweep`. Near
the new CSV boundary, backend `testmempoolaccept` must reject the original sweep
with `non-BIP68-final`. At maturity the fixture mines that same sweep and two
fresh verifier processes must report the new success/sweep confirmation heights.
The commitment ID/height, payment attempt and protected mutation journals remain
unchanged. Raw transaction selection and chain administration are fixture-only;
the controller continues to receive separate read-only credentials.

Run the four-case deadline harness using the existing controller image:

```sh
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all
```

No image rebuild or StartOS installation is required for this fixture extension.
All four funded claim rollback cases passed at checkpoint 53bed2b.
This checks paired-node observation recovery and
CSV maturity after success rollback; it does not establish commitment rollback
recovery, independent chain proofs, finality or live execution readiness.


## Funded regtest commitment confirmation rollback

The fixture now repeats the deeper rollback with the commitment block removed
as well. It builds an empty replacement branch past the old tip and waits for
CLN to report the original commitment, success and sweep as unconfirmed. Two
fresh read-only verifier containers must report `awaiting_commitment`, with no
retained success proof.

It then mines the original commitment by itself, requires
`awaiting_htlc_success`, and mines the original success in the following block
before the original expiry. The existing CSV-boundary test rejects a premature
sweep and reconfirms that same sweep at its new maturity. Verification must
recover using the three new confirmation heights and the original transaction
IDs. The original outgoing attempt, gate journal, deadline/claim records and
close/release audits must remain unchanged throughout.

Backend invalidation and raw transaction selection remain disposable fixture
operations. No mutation credentials are given to the verifier. This validates
observation recovery when the same commitment returns; it does not test a
conflicting commitment, revoked state, funding rollback or arbitrary-depth
reorgs. Confirmation evidence remains paired-node data, not independent proofs.

Run the existing four-case deadline harness with `all`. No Docker rebuild or
StartOS installation is needed. All four funded commitment rollback cases passed
at checkpoint fb291f5; live execution remains disabled.


## Fresh verification failures clear earlier success

Before a new admitted regtest inspection loads its private request or contacts
CLN, the verifier holds the job lock and atomically replaces
`chain-verification.json` with `verification_in_progress`, `verified: false`,
and no proof. Request loading, credential construction, journal validation,
identity checks, RPC failures, malformed evidence and expired observations are
inside this boundary. Ordinary failures save `verification_unavailable` with
no private error details; an abrupt process exit leaves the unverified
in-progress record. A later successful inspection must rebuild the proof from
fresh read-only evidence.

The disposable runner removes its previous output before beginning inspection.
The funded harness now starts one verifier with Docker networking disabled and
another that exits after durable invalidation, then uses fresh containers to
recover verification. Both faults must clear saved success without changing the
original deadline/claim records, gate journal or close/release audits. Existing
sweep, claim and commitment rollback checks still follow these cases.

This is invalidation when an inspection starts, not a background freshness
monitor or a time-to-live guarantee. A saved success is evidence from the last
completed inspection, not a continuously current chain assertion. Failure to
acquire the job lock or persist invalidation prevents inspection from proceeding;
callers must treat a failed invocation as unavailable, not reuse an earlier file
as fresh evidence. Read-only inspection does not clear restore barriers or
provide spending, closing or gate-resolution authority. Live execution remains
disabled; confirmation evidence still comes from the paired node.

Rebuild the controller test image because packaged verifier code changed:

```sh
docker buildx build --builder startos-builder --load \
  -t swap-controller:deadline-boundary . &&
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all
```

Local verification and harness tests pass, including abrupt child-process exit.
All four funded outage/interruption cases passed at checkpoint 591b411.
No StartOS installation is required for this regtest checkpoint.


## Funded controller absence across the deadline threshold

The optional `--controller-downtime` fixture mode first runs the packaged guard
at 31 incoming-chain blocks remaining. That one-shot controller exits normally.
The host then confirms that its named container is absent, including stopped
containers, before the fixture mines to 30 and then 27 blocks remaining with no
controller step in between. This models missed controller polling, not a crash
inside a running close RPC or a persistent StartOS worker restart.

At both heights the original incoming channel must still be normal with the
same funding pin and committed HTLC. The original outgoing attempt must remain
pending, the payer process must remain pending, and the outgoing chain height
must not advance. Every controller control-file byte and the gate journal must
remain unchanged during this interval. The host confirms controller absence
again before a fresh container observes the 27-block margin and closes only the
original channel.

The normal and lost-reply cases in both directions then use the existing
reconciliation, packaged gate recovery, confirmed claim/CSV sweep verification,
verifier outage/interruption and three rollback checks. They must retain one
outgoing attempt, one close and one gate release. The original threshold tests
remain available by omitting the flag; they still close at 30 blocks.

```sh
python3 scripts/test-funded-deadline.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all --controller-downtime
```

This changes fixture code only: reuse the controller image rebuilt for stale
verification. No image rebuild or StartOS installation is required. Local harness
tests pass; all four funded downtime cases passed at checkpoint 4794a60.
Four blocks of absence on one regtest chain is not a wall-clock outage budget,
a cross-chain timing guarantee or evidence of protection after arbitrary
controller downtime. Independent chain proofs and live deadline protection
remain outstanding; live execution stays disabled.


## Packaged quote recovery after settlement during controller absence

The quote harness accepts `--settle-during-downtime`. A disposable holding hook
on the recipient keeps the worker's original outgoing payment pending. After the
packaged worker exits with `outgoing_started`, the host confirms that its named
container is absent. The fixture advances only the incoming chain to 27 blocks
remaining and lets the recipient resume ordinary invoice settlement.

Before another worker starts, the outgoing attempt must be complete with its
original identity and a matching preimage, while the original incoming HTLC and
payer remain pending. The gate journal and all controller control files must be
byte-for-byte unchanged, with no preimage in the saved pending state. A fresh
packaged worker must obtain the outcome remotely and release the bound incoming
HTLC. All four balances must match the agreed amounts, every channel must remain
normal with no pending HTLCs, and the audit must contain one send and one release,
with no close or failure mutation. A further worker must not add any RPC audit
entries.

Each direction also runs an interruption after the release RPC was acknowledged
but before the executor saved its terminal checkpoint. The existing pinned crash
flags must return their exact expected exit codes. A new worker reconciles the
resolved gate without repeating release. This tests a missing terminal checkpoint,
not a transport-level discarded release response.

```sh
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all --settle-during-downtime
```

The `all` direction runs forward and reverse, each with ordinary recovery and
interrupted release checkpoint recovery. Omit the flag for the existing quote
happy path. This is fixture-only work; reuse the current controller image. No
StartOS installation or image rebuild is required. Four local fault/boundary tests
pass; all four funded settlement-during-downtime cases passed at e212af5.

The quote worker currently has no integrated deadline watcher. These cases test
its terminal outcome recovery; the separate deadline harness covers closing when
the outgoing attempt is still pending. They do not establish automatic dispatch
between those paths, arbitrary outage tolerance or live execution readiness.
Live execution remains disabled.


## Packaged quote failure during controller absence

The quote harness also accepts `--fail-during-downtime`, mutually exclusive with
`--settle-during-downtime`. Both directions hold the original outgoing HTLC until
the packaged worker exits. With its container absent, the fixture advances only
the incoming chain to 27 blocks remaining and makes the recipient fail the held
outgoing HTLC. The original attempt must report definitive failure without a
preimage. Its identity and amount must match the saved pending attempt.

Before recovery, the incoming payment must remain held on the original normal
channel. Controller files and the gate journal remain unchanged. A fresh worker
must fail that bound incoming HTLC once, without another send, release or close.
The payer must report failure, the recipient invoice must remain unpaid, all four
channel balances must return to their initial values, and no HTLCs may remain.
Repeating the terminal worker must leave the RPC audit unchanged.

```sh
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all --fail-during-downtime
```

This runs two funded scenarios, one per direction. It does not inject an
interrupted failure checkpoint or discarded failure response. The separate
settlement scenarios retain their existing interruption coverage. This patch is
fixture-only; reuse the current controller image without a StartOS installation.
Local harness tests pass; both funded failure-during-downtime cases passed at 100a38f.
The quote worker still has no integrated deadline watcher. Live execution remains
disabled.


## Opt-in regtest quote supervisor

Explicitly enrolled disposable quote jobs now use one worker entry point for
outcome selection. A pending original attempt runs the existing bounded deadline
guard; a complete or definitively failed attempt uses the ordinary executor's
independent outcome and gate checks. The guard rechecks pending status before
closing, so a racing terminal outcome refuses the close and waits for a new
cycle. Unknown, ambiguous, changed or stale observations never authorize action.

Enrollment is a private fixture-provisioned `supervisor.json` after the original
outgoing attempt exists. It contains an intent digest, exact deadline spec and
separate restricted close/claim connections. The worker checks quote handoff,
node identities, amounts and incoming binding, then durably fixes the enrollment
digest. Normal unenrolled jobs retain their existing path. This is not automatic
provisioning or protection from the instant of submission.

Once `deadline.json` exists, every supervisor cycle uses post-close recovery.
The ordinary executor refuses that job. Pending outcomes remain pending; a
completed outgoing attempt can release the bound gate through the existing
claim adapter. A failed outgoing attempt after close remains refused: this does
not implement an on-chain timeout/refund proof. Restore and backup barriers are
retained. The executor's original pending checkpoint is preserved after gate
recovery, so it must not be interpreted as fully verified on-chain settlement.

The funded quote fixture can enroll both directions and drive the same packaged
worker through pending protection and post-close gate recovery:

```sh
docker buildx build --builder startos-builder --load \
  -t swap-controller:deadline-boundary . &&
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all --pending-during-downtime
```

The fixture verifies no close above the threshold, controller absence down to a
27-block margin, one close, then original preimage recovery and one gate release
after the commitment is on-chain. Repeated workers preserve close/claim records.
At checkpoint 4685c1a this stopped at gate resolution. The integrated claim/sweep
extension below adds confirmation and wallet-output verification.

Add `--supervise` to the existing `--settle-during-downtime` or
`--fail-during-downtime` commands to exercise terminal selection through this
supervisor. Eleven local supervisor tests pass alongside the existing quote,
executor, lifecycle and deadline tests. Funded pending and failure supervisor
scenarios passed in both directions at checkpoint 4685c1a.
Rebuild the disposable controller image; no StartOS installation is required.
Live execution remains disabled.


## Supervised quote recovery through confirmed wallet funds

The existing `--pending-during-downtime` quote scenarios now continue beyond gate
resolution. In each direction, the same packaged quote worker selects deadline
close, preserves the pending outgoing attempt and later recovers its preimage.
The pinned claim fixture confirms the original commitment before permitting
recipient settlement. It then verifies the HTLC-success witness, the payer's
on-chain preimage recovery, CSV maturity and the confirmed unspent wallet output
from the coordinator's sweep. A disposable incoming wallet reserve funds on-chain
fees; this is not a live reserve policy.

A separate fresh verifier container receives only a dedicated verification
directory containing copied close/claim records and restricted inspection
credentials. It cannot mount the execution directory, close credentials, claim
credentials or node files. The fixture verifies the inspection runes reject
writes. The packaged verifier must report unverified before claim confirmation,
then link the same HTLC-success and sweep transaction IDs independently observed
by the fixture. Repeating verification must preserve mutation audits, gate state
and close/claim records. Stale output is removed before a new inspection.

The scenario also checks payer completion with the same preimage, the paid
recipient invoice, outgoing channel balances and no outgoing pending HTLCs. The
incoming channel has closed: its recovery is measured through confirmed claim
and sweep outputs, not an assertion that all four off-chain balances were restored.
Exactly one outgoing send, one close and one incoming release must remain.

```sh
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all --pending-during-downtime
```

Reuse the controller image from checkpoint 4685c1a; these changes are fixture-only
and need no rebuild or StartOS installation. Six local quote harness tests and
twelve claim-verifier tests pass; funded integrated claim/sweep validation is
complete in both directions at e6fdd95. Earlier supervisor pending and failure cases passed in both directions.
The verifier still relies on paired CLN observations; fixture comparison does not
turn its report into an independent chain proof. Live timing/fee policy and live execution remain outstanding; the following
section adds opt-in pre-submission enrollment for regtest.


## Supervision bound before regtest submission

Supervised quote fixtures now provision a private plan while the incoming HTLC
is committed and before the first outgoing worker launch. The plan fixes node
identities, incoming funding/channel/HTLC/expiry, amounts and separate restricted
close and claim credentials. A requirement marker is committed before the plan;
a missing plan cannot silently fall back to an ordinary unsupervised import.
The executor embeds the plan digest in its original immutable intent.

Under its existing execution/lifecycle lock, the executor checks the plan and
quote handoff, verifies both credential sets over HTTPS, checks the exact normal
incoming channel and committed non-trimmed HTLC, requires a held gate and more
than the 30-block regtest guard margin, and refuses any existing outgoing attempt.
It saves an arming receipt before the launch marker and child. Restore, changed
configuration or unavailable credentials refuse submission. Uncertain child
launches retain their original no-resend behavior.

The remote node assigns outgoing group/part IDs. A subsequent cycle attaches only
the unique matching original attempt to the already-armed plan. Missing or
ambiguous attempts refuse progress; they never trigger another submission.
Removing the plan or arming receipt after launch prevents recovery from silently
adopting replacement configuration. Existing post-launch enrolled checkpoints
remain supported, and ordinary explicitly unenrolled regtest fixtures retain
their existing path.

All supervised funded scenarios now prove missing-plan refusal before restoring
the plan and launching. Run the complete milestone matrix against a rebuilt
controller image:

```sh
docker buildx build --builder startos-builder --load \
  -t swap-controller:deadline-boundary . &&
for outcome in pending settle fail; do
  python3 scripts/test-quote-flow.py \
    btc-cln:swap-preparation xbt-cln:recovery-test \
    swap-controller:deadline-boundary ../bitcoind all \
    --"${outcome}"-during-downtime --supervise || break
done
```

This covers two pending-to-confirmed-sweep cases, four settlement cases including
interrupted checkpoints, and two definitive-failure cases. Nine local arming tests
pass, with existing supervisor, executor and quote tests. Funded pre-submission
arming validation is pending. No StartOS installation is required.
This binds protection before spending; it does not guarantee an online worker,
a wall-clock recovery budget, or live cross-chain protection. Live execution
remains disabled.


## Server-side regtest protection credential targets

Protection credentials now restrict close requests at CLN authorization to the
original full channel ID and unilateraltimeout=1. Only the two named parameters
are accepted; missing, positional and additional parameters are refused. Read
methods remain available for identity and recovery inspection. Reverse recovery
credentials additionally require the original payment_hash and exactly three
parameters for reverse-release. The gate and packaged client still validate the
HTLC binding and preimage; the rune does not encode that binding.

The funded fixture uses checkrune to verify both permitted and rejected request
shapes without submitting deliberately incorrect mutations. The real close and
release paths then run over restricted HTTPS. Both supervised quote directions
must still reach confirmed claims and mature wallet sweeps. Local tests cover
invalid targets, restriction injection, role separation and live-network refusal.
Funded validation of these new restrictions is pending.

This is not a complete live execution credential design. The pinned forward
xbt-release RPC takes only a preimage, so a rune created before the secret is
known cannot restrict that release by payment hash. Forward recovery retains its
existing method-only regtest rune and client-side binding checks. A hash-bound
forward release interface is required before a corresponding live credential can
be issued. No live credentials are issued by this module, no live limits are
claimed enforced, and installed pairing/readiness/restore barriers are unchanged.

On the packaging VM, rebuild the controller and run:

```bash
docker buildx build --builder startos-builder --load \
  -t swap-controller:deadline-boundary . &&
python3 scripts/test-quote-flow.py \
  btc-cln:swap-preparation xbt-cln:recovery-test \
  swap-controller:deadline-boundary ../bitcoind all \
  --pending-during-downtime --supervise
```


## Forward post-close recovery through a bound release RPC

Post-close forward ClaimRemote now sends xbt-release-bound with the original
payment_hash and its verified preimage. It never falls back to xbt-release. The
BTC image must include the new hash-checking adapter. A missing method or an old
method-only recovery credential refuses recovery; it does not authorize a resend,
refund, second close or automatic credential replacement. Retain the existing
journal and use a reviewed recovery procedure for any old pending job.

The dedicated forward recovery rune permits exactly two named parameters and
only its assigned payment_hash. The legacy release method is excluded. As with
reverse recovery, the controller separately verifies the original attempt, channel
and gate binding. Ordinary quote settlement still uses its existing execution
credential and legacy RPC; this change is specifically for post-close recovery.
There are still no live execution credentials or live payments enabled.

Both supervised quote and standalone deadline fixtures select the packaged BTC
adapter while retaining the pinned source and original journal location. Their
checkrune checks cover correct and incorrect target/parameter shapes, and HTTPS
probes reject the legacy release method. Mutation audits and discarded-release
reply injection now count the new RPC. Funded validation is pending.

Rebuild btc-cln:swap-preparation in the BTC packaging repository and
swap-controller:deadline-boundary in this repository. Then run the supervised
pending quote flow in both directions, followed by the funded deadline harness
for normal and lost-reply cases in both directions. No StartOS update is needed.

## Explicit single forward pilot (0.1.0:12 candidate)

The new **Forward Pilot** actions implement one separately authorized direct
swap: **1,000 BTC sats → 2,000 XBT sats**. This is a fixed operator-approved price,
not a market quote. The existing regtest actions and generic read-only readiness
report remain separate. Installing the package does not start a swap.

Use BTC Swap Preparation 26.6.8:11 and XBT Core Lightning 0.1.0:17 with this
controller. Both nodes must be paired over verified HTTPS. BTC's existing
`live-pilot-v1` gate must be active and unused. Each coordinator needs at least
50,000 sats of confirmed, unreserved wallet funds for on-chain protection. The
selected channels must be connected, normal, free of other HTLCs, and have
sufficient directional liquidity. The current fee/dust check can refuse a
1,000-sat incoming HTLC; never lower that check to force admission.

1. Create a fresh 2,000-sat XBT invoice on the customer directly connected to the
   XBT coordinator, with at least 33 minutes remaining.
2. Run **Prepare Forward Pilot** with that invoice, the LND → BTC and XBT →
   customer short channel IDs, and the two inspection credentials. Review the
   returned contract, including both node IDs and funding/channel pins. This
   action has no spending authority and publishes no BTC invoice.
3. Run **Authorize BTC Forward Pilot** and **Authorize XBT Forward Pilot** on
   their respective node packages, pasting the same contract and explicitly
   confirming it. Copy each returned restricted credential and compare its
   pilot ID with the reviewed ID.
4. Within the 30-minute admission window, run **Approve Forward Pilot and
   Publish BTC Invoice** with that pilot ID, both execution credentials and
   explicit confirmation. Approval requires a fresh persistent-worker heartbeat.
5. Pay the returned **1,000-sat BTC invoice from LND** promptly. Its quote window
   is 120 seconds. Do not pay an expired invoice or recreate a payment after an
   uncertain result. Inspect **Forward Pilot Status** while the worker proceeds.

The worker waits for the exact committed incoming BTC HTLC before submitting
one 2,000-sat outgoing attempt (group 1, part 0) on the pinned direct XBT channel.
Incoming admission requires 288–2016 BTC blocks; the published invoice requests
300. The outgoing route uses 40 XBT blocks. If the outgoing attempt remains
pending at 72 incoming BTC blocks, protection requests one exact-channel force
close. These are independent chains: block counts do not guarantee relative
wall-clock progress or safety under arbitrary stalls/reorganizations.

A completed original outgoing attempt supplies the hash-verified preimage for
the bound BTC release. Definitive failure before a close fails the held incoming
gate. Persistent node/controller intents precede mutations. Lost replies are
reconciled; missing or contradictory evidence raises attention without repeating
send, close or release. This may require manual investigation. There is one
pilot slot, not a reusable swap exchange or an automatic retry facility.

`settled` means the original outgoing attempt completed, the incoming gate was
resolved, and both channel HTLC sets cleared. `failed` means the original
outgoing attempt failed and the incoming gate failed without a requested close.
`onchain_recovery` means the bound gate was released after closing; it explicitly
does **not** certify a confirmed claim or mature wallet sweep. CLN performs
on-chain recovery. The funded fixture separately checks those transactions.

Active execution blocks backup and replacement pairing. Execution credentials
are excluded from backups. Controller restore changes a pilot authority epoch;
old records cannot resume. The old generic `restored.json` guard is retained.
A fresh pilot after a previous read-only controller restore requires a new
pairing, new contract nonce, unused node-side authority slots, an unused BTC
gate and fresh explicit approval. Coordinator restore barriers still refuse
authorization. Never delete journals or restore markers to make a swap proceed.

Candidate validation: Python tests, TypeScript builds, and action/bundle checks
have passed locally. The new funded matrix (normal, lost replies, failure,
pending close through claim/CSV sweep) must still run on the packaging VM.
It uses disposable regtest nodes and fixture-only network labels, never live
credentials. No live payment has been initiated by preparing this release.


## Saved inspection setup and channel discovery (0.1.0:14)

Use **Save Swap Inspection Credentials** once with the two dedicated inspection
credentials. They are stored privately, excluded from backups, and invalidated
when pairing details or restore state change. This does not grant payment authority.

**Find Swap Channels** takes a 2,000-sat XBT invoice and lists eligible incoming
BTC channels and direct XBT channels to its recipient. Disconnected channels,
pending HTLCs, insufficient liquidity, and currently trimmed amounts are excluded.

For an unused pilot slot, **Prepare BTC to XBT Swap** reuses saved inspection
credentials. Leave channel fields blank only when exactly one eligible channel
exists for each side. If several qualify, use Find Swap Channels and explicitly
select the payer's incoming channel or the recipient's outgoing channel. Fresh
inspection and all existing contract checks run again before preparation.

**Swap Status** displays readable stages and amounts. The original advanced
preparation action remains available. Node contract authorization and final
approval are still required. This release does not enable repeat swaps, reset a
completed pilot, increase limits, or automate node authorization. Existing settled
records are preserved. No live payment is needed to test discovery.

## Repeat swaps with saved authorization

The repeat flow preserves the original pilot and gives each new swap its own durable record. Save inspection credentials once, then pair bounded grants from both coordinators using **Pair Repeat Swap Grants**. Use **New BTC to XBT Swap** with only a fresh 2,000-sat XBT invoice; **Confirm Swap** fills the reviewed ID automatically and requires explicit confirmation. Pay the resulting 1,000-sat BTC invoice once. **Swap History** retains results and invoices. Payer routing fees are additional.

Each coordinator grant pins one channel, allows 1–10 enrollments (default 5), and expires for new enrollment after 24 hours. Failed and expired enrollments consume slots. Pausing or expiry preserves recovery authority for enrolled contracts. Renewing the budget is an explicit coordinator action after all previous work is terminal. Restart the BTC coordinator once after enabling repeat mode if its action requests it.

Only one swap may be active. Unapproved drafts may be cancelled. Unpaid published invoices are retired only after their deadline and fresh checks prove no incoming HTLC was accepted and no outgoing payment exists. Uncertain or on-chain recovery blocks another swap; a recovery status is not proof of a confirmed claim or sweep. Restore barriers remain enforced, and backups exclude reusable credentials and execution authority.

This is still the fixed 1,000 BTC sat → 2,000 XBT sat direct-channel flow, not a general exchange-rate interface. Local unit and action checks accompany the change; run the disposable funded repeat scenarios before installing the candidate packages.
