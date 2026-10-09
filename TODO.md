# Current bidirectional checkpoint

- [x] User reported routed live BTC → XBT settlement: 2,000,000 msat received,
  paid at 2026-10-09T01:41:09Z; LND payer had no direct coordinator channel.
- [x] Commit on the tower and sync packaging VM to BTC 1211fcd, XBT 50635bb,
  controller 910fc28; retain VM stashes.
- [x] Implement separate reverse grants, routed invoice review and confirmation,
  incoming funding/HTLC binding, one outgoing attempt and restart recovery.
- [x] Local reverse/forward unit, gate protocol, action and package checks.
- [x] Run the four forward routed funded scenarios on the packaging VM for 0056
  (2026-10-09, `/tmp/cln-forward-pilot-qk3j7ay8`).
- [x] Fix the reverse test fixture's temporary directory permissions (0056a).
- [x] Fix the XBT gate test container's import path; isolated-layout gate tests
  pass all 11 cases (0056b).
- [x] Audit the full reverse fixture flow; share mailbox stages and isolate
  direction-specific invoice adapters (0056c).
- [x] Add process/bridge preflight for normal, lost reply, failure and restart;
  simulate CLN/transport only, retain the funded VM gate.
- [x] User reported all four reverse routed funded scenarios passed (0056c).
- [x] User reported all three 0056c installers built and installed.
- [x] Explicitly enabled reverse grants; live preparation isolated a 120-block
  private-hint route exceeding the approved 80-block cap. No live reverse paid.
- [x] Implement 0057 separately versioned 144-block grants, preserved 80-block
  authority/recovery, specific safe route refusals and realistic hint regressions.
- [x] User reported 0057 READY: funded matrices passed and all three installers built.
- [x] Paired renewed grants; active BTC grant confirmed at 144 blocks, five slots.
- [x] Read-only live route diagnosis found three hops, 200 blocks and 2,002 msat;
  higher fees alone did not fit the 144-block cap. No reverse payment submitted.
- [x] Implement 0058 versioned 288-block grants and actual-cap refusal messages;
  preserve existing 80/144-block authority and original recovery margins.
- [x] Validate 0058 locally: 103 focused Python tests and all three package checks.
- [x] User reported 0058 READY: both updated funded matrices passed and all three
  installers built (2026-10-09).
- [x] Install the 0058 packages, explicitly renew and pair both reverse grants.
- [x] User confirmed live XBT → BTC: customer payment complete, controller settled,
  LND invoice SETTLED with 1,500 sats received (2026-10-09).
- [ ] Export 0059 from the packaging VM; commit/push on the tower; retain VM
  stashes and fast-forward the VM to the GitHub checkpoint.

Both live happy paths are user-confirmed. The 200-block/2,002-msat reverse route
was observed in read-only preflight; the final route was not separately supplied.
Live failure/restart and live on-chain recovery success are not claimed.
The checklist below is retained historical development evidence; its old package
versions and prior read-only/live-execution descriptions are not current status.

# Integrated forward pilot candidate

- [x] Implement one explicit fixed-price forward contract, node-side restricted authority and persistent worker.
- [x] Pass local Python, action, TypeScript and bundle checks.
- [ ] Run the four new funded candidate scenarios on the packaging VM.
- [ ] Build/install BTC 26.6.8:11, XBT 0.1.0:17 and controller 0.1.0:12 after funded validation.
- [ ] Review and explicitly approve one live 1,000 BTC sat → 2,000 XBT sat pilot; verify its actual outcome.

The evidence below describes earlier releases and regtest checkpoints. It does
not establish that this new candidate's funded matrix or a live swap has passed.

# Release validation remaining

- [x] Build the x86_64 s9pk on the packaging VM.
- [x] Install on StartOS and verify pairing, periodic health and restart with both real nodes.
- [x] Verify backup/restore returns to unpaired status, then re-pair successfully.
- [x] Create the packaging repository before publishing a release.
- [x] Run all four restricted HTTPS controller recovery scenarios against packaged nodes.
- [x] Repeat both directions with the send acknowledgement discarded before controller receipt.
- [x] Run both directions in an isolated controller container, including unavailable networking during pending recovery.

- [x] Run all four isolated-container scenarios using the packaged regtest executor.
- [x] Validate lifecycle worker recovery with all four funded regtest scenarios.
- [x] Build/install 0.1.0:1 and verify dormant worker, restart, backup and restore barrier.

The installed service remains read-only. Packaged execution is regtest-only and
registered through a dormant lifecycle daemon; there is no live execution or quote API.

- [x] Run all four synthetic stale-journal inspections alongside the retained original executor; do not clear restore barriers.

- [x] Run all four lost-current-journal regtests with separate resolution-only runes; keep restored snapshots blocked.

- [x] Run all four funded lost-journal scenarios with the final resolution reply discarded before the terminal checkpoint.

- [x] Repeat those four cases through the packaged, explicitly confirmed regtest recovery command.

- [x] Verify coordinator rune revocation fences a retained old executor in all four funded cases, including coordinator restarts.

- [x] Repeat fenced recovery with the original sendpay reply discarded before reaching the controller algorithm.

- [x] Verify partial coordinator revocation blocks a fresh replacement until the second administration endpoint returns and both revocations are confirmed.

- [x] Run all four funded scenarios through packaged recovery admission, including durable partial confirmation and fresh access verification.
- [x] Validate the StartOS action boundary with all four funded scenarios and install 0.1.0:2 for local recovery status, restart and unchanged pairing/barrier checks.

- [x] Install 0.1.0:3 and verify inherited pause reporting with the restore barrier intact.

- [x] Run the packaged forward quote flow: review an XBT invoice, explicitly approve its BTC price, pay the published BTC invoice, and verify isolated worker settlement.

- [x] Run the funded quote flow through the packaged StartOS action handler, then install 0.1.0:4 and verify local quote status, forms, pairing and preserved restore barrier.

- [x] Run the reverse quote flow through packaged actions, then install 0.1.0:5 and verify the reverse form, shared approval, local status and preserved pairing/barriers.

- [x] Install 0.1.0:6 and verify Live Swap Readiness with the existing pairing state; confirm live requirements and restore barrier remain explicit.

- [x] Install 0.1.0:7 and BTC 26.6.8:9; pair the separate gate observer and verify BTC-only readiness without changing live execution or restore barriers.

- [x] Install XBT 0.1.0:15 and controller 0.1.0:8; explicitly activate and observe the reverse gate while retaining execution barriers.

- [x] Install controller 0.1.0:9; review both proposed live policies and verify unchanged pairing, readiness blockers and restore barrier.

- [x] Run both funded regtest quote flows with policy-bound reviews, approval rejection and held timing checks.

- [x] Validate the read-only direct live candidate inspector on the packaging VM.
- [x] Install dedicated restricted inspection credential actions and controller preflight actions; verify a real read-only inspection.
- [x] Run packaged regtest deadline-boundary tests.
- [x] Run funded forward packaged HTTPS deadline close/reconciliation in normal and lost-reply cases, with fixture-assisted on-chain claim verification.
- [x] Run reverse funded packaged HTTPS deadline close/reconciliation in normal and lost-reply cases, with fixture-assisted on-chain claim verification.
- [x] Run all four funded deadline cases with packaged post-close gate recovery and discarded release replies.
- [x] Run all four funded cases with packaged read-only claim/sweep verification and independent fixture comparison.
- [x] Disconnect and reconfirm the sweep block in all four funded cases; revoke and recover read-only verification without repeated mutations.
- [x] Disconnect HTLC-success and sweep, reconfirm success and enforce its new CSV maturity in all four funded cases.
- [x] Disconnect and reconfirm the original commitment and descendants in all four funded cases without repeated controller mutations.
- [x] Run all four funded cases with prior verification invalidated during network outage and abrupt verifier exit, then recover through fresh read-only inspection.
- [x] Run all four funded cases with the controller absent as the incoming margin falls from 31 to 27 blocks, then resume exact-channel protection without duplicate mutations.
- [x] Run both packaged quote directions with the original outgoing attempt settling during controller absence, including interrupted release checkpoints and exact-once settlement.
- [x] Run both packaged quote directions with definitive outgoing failure during controller absence, exact-once incoming failure and restored balances.
- [x] Validate supervised pending close and definitive failure recovery in both directions.
- [x] Complete integrated supervised quote recovery through confirmed HTLC-success and CSV wallet sweep in both directions.
- [ ] Validate the full funded supervisor matrix with protection plans and credentials armed before outgoing submission.
- [ ] Retain explicit funded evidence for supervised complete outcomes, including interrupted release checkpoints.
- [ ] Establish confirmation/reorg handling beyond paired-node observations before live protection.
- [ ] Validate live deadline/on-chain protection, including controller downtime, before implementing live execution.

- [x] Validate server-side exact-channel close and reverse payment-hash restrictions through both funded supervised claim/sweep flows (94797d1).
- [ ] Validate the packaged hash-bound forward release interface with supervised claims and lost-release-reply recovery before any live credential work.


## Swap UX

- [x] Add saved read-only inspection credentials and recipient-bound channel discovery.
- [x] Preserve completed pilot records and present readable status stages.
- [ ] Replace single-pilot slots with durable per-swap records and bounded reusable node authorization.
- [ ] Consolidate authorization and approval into a repeatable invoice-and-confirm flow.

- Repeat UX candidate: saved bounded grants, invoice-only preparation, exact confirmation and retained history implemented; validate packaged funded repeat scenarios before installation.
