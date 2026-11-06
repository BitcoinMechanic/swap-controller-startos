# 0102 dedicated live integration candidate

Build this candidate with the verified 0102 bundle runner. It binds exact engine,
UI and Python-runtime image IDs into an isolated packaging copy and compiles the
installed StartOS registry. The source manifest alone is not an installer.

Use a fresh dedicated installation. The legacy controller volume/authority is
not migrated. Complete the Initialize Swap Service task while stopped, then
start the service. Pair reviewed incoming/outgoing credentials for each direction
using the coordinator HTTPS address and its StartOS root CA. Credentials are
masked and sent privately, never included in public status.

New swap: recipient invoice → review Neoxa price and limits → explicit confirmation
→ pay the published incoming invoice once. The automatic worker preserves the
saved price, exact incoming HTLC and one outgoing attempt across restarts. Pricing
uses the existing fixed Neoxa market/depth policy; markup defaults to 0% and is
configurable. An unavailable market prevents new quotes, not saved recovery.


Ordinary restart/update is not backup restoration. Restored wallets remain fenced
from new coordinator authority; recovery may force-close channels. Incoming
swap deadline protection can also close the actual receiving channel if a swap
cannot resolve before its approved deadline. Grants require explicit review.

0102 validation distinguishes synthetic mainnet policy checks, actual funded
regtest fixtures, packed-file checks, and pending StartOS host acceptance. It
does not claim a mainnet swap or StartOS host test from a regtest receipt.
