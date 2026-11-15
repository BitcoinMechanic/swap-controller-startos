ARCHES := x86
# overrides to s9pk.mk must precede the include statement
include node_modules/@start9labs/start-sdk/s9pk.mk

# Fetch and verify public, pinned image inputs before the SDK packs the archive.
.PHONY: build-inputs
check-deps: build-inputs
build-inputs:
	python3 scripts/fetch-build-inputs.py

.build-inputs/%.tar: build-inputs.json scripts/fetch-build-inputs.py
	python3 scripts/fetch-build-inputs.py
