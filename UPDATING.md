# Updating

Keep the StartOS SDK at 2.0.9 until a deliberate upgrade is tested. The Python
transport derives from the tested coordinator read-only probe. Review every
transport change against certificate validation, identity pinning and privacy
tests. The runtime uses Python 3.13 on Debian Bookworm; the image tag is not a
digest pin, so this development build is not bit-for-bit reproducible.

Run the Python tests, TypeScript check, bundle build and scripts/check-bundle.cjs
before packaging. Update the StartOS version graph for subsequent releases.
Legacy transition admission is embedded from scripts/legacy/ by embed.py and uses a read-only temporary mount. Re-run its Python controls, scripts/check-bundle.cjs, TypeScript compilation, and the isolated runtime transition fixture before packaging. Never initialize new authority or execute a swap as a side effect of migration.
