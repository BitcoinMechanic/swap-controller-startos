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

- [ ] Run the packaged forward quote flow: review an XBT invoice, explicitly approve its BTC price, pay the published BTC invoice, and verify isolated worker settlement.
