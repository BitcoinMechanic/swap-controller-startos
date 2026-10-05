# Release validation remaining

- [x] Build the x86_64 s9pk on the packaging VM.
- [x] Install on StartOS and verify pairing, periodic health and restart with both real nodes.
- [x] Verify backup/restore returns to unpaired status, then re-pair successfully.
- [x] Create the packaging repository before publishing a release.
- [x] Run all four restricted HTTPS controller recovery scenarios against packaged nodes.
- [x] Repeat both directions with the send acknowledgement discarded before controller receipt.
- [ ] Run both directions in an isolated controller container, including unavailable networking during pending recovery.

This initial version intentionally has no swap execution or quote API.
