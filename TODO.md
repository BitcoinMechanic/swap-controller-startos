# Release validation remaining

- [x] Build the x86_64 s9pk on the packaging VM.
- [x] Install on StartOS and verify pairing, periodic health and restart with both real nodes.
- [x] Verify backup/restore returns to unpaired status, then re-pair successfully.
- [x] Create the packaging repository before publishing a release.
- [x] Run all four restricted HTTPS controller recovery scenarios against packaged nodes.
- [x] Repeat both directions with the send acknowledgement discarded before controller receipt.
- [x] Run both directions in an isolated controller container, including unavailable networking during pending recovery.

- [x] Run all four isolated-container scenarios using the packaged regtest executor.
- [ ] Validate lifecycle worker recovery with all four funded regtest scenarios.
- [ ] Build/install 0.1.0:1 and verify dormant worker, restart, backup and restore barrier.

The installed service remains read-only. Packaged execution is regtest-only and
registered through a dormant lifecycle daemon; there is no live execution or quote API.
