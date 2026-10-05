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
