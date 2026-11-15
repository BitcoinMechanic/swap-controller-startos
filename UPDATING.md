# Updating

Keep the StartOS SDK at 2.0.9 until a deliberate upgrade is tested. The Python
transport derives from the tested coordinator read-only probe. Review every
transport change against certificate validation, identity pinning and privacy
tests. The runtime uses Python 3.13 on Debian Bookworm; the image tag is not a
digest pin, so this development build is not bit-for-bit reproducible.

Run the Python tests, TypeScript check, bundle build and scripts/check-bundle.cjs
before packaging. Update the StartOS version graph for subsequent releases.
Legacy transition admission is embedded from scripts/legacy/ by embed.py and uses a read-only temporary mount. Re-run its Python controls, scripts/check-bundle.cjs, TypeScript compilation, and the isolated runtime transition fixture before packaging. Never initialize new authority or execute a swap as a side effect of migration.

## Clean CI packaging

`npm ci && make x86` downloads the published image archives listed in
`build-inputs.json` and verifies their exact sizes and SHA-256 hashes before
packing. No local Docker tags, server volumes or registry credentials are needed.
Only x86_64 is supported by these image inputs; ARM builds are not advertised.

These are prebuilt native/runtime and UI images from the tested installers, not
a native compilation from source. The lock records their source commit and hashes
of runtime source files. If those files change, the build refuses to package the
old images: publish matching image inputs and update the lock. Existing archived
Docker recipes are retained for provenance; they are not the default build path.

CI builds and retains the installer as an Actions artifact. Deployment testing
on StartOS is a separate step. Existing release tags and installers are unchanged.
