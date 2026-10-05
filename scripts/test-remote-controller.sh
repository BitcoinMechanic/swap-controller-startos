#!/bin/bash
set -euo pipefail
if [ "$#" -lt 3 ] || [ "$#" -gt 5 ]; then
  echo 'Usage: bash scripts/test-remote-controller.sh BTC_IMAGE XBT_IMAGE KNOTS_BITCOIND [all|forward|forward-failure|reverse|reverse-failure] [--drop-send-reply]' >&2
  exit 2
fi
mode=${4:-all}
case "$mode" in all|forward|forward-failure|reverse|reverse-failure) ;; *) exit 2 ;; esac
flags=()
if [ "$#" = 5 ]; then
  [ "$5" = --drop-send-reply ] || exit 2
  flags=(--drop-send-reply)
fi
repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
pair=$(realpath -- "$repo/../btc-cln-startos/tests")
test -f "$pair/image_pair.py"
backend=$(realpath -- "$3")
test -f "$backend" && test -x "$backend"
btc_image=$(docker image inspect --format '{{.Id}}' "$1")
xbt_image=$(docker image inspect --format '{{.Id}}' "$2")
prefix=$(mktemp -d /tmp/remote-cln-binaries.XXXXXX)
container=''
cleanup() {
  if [ -n "$container" ]; then docker rm -f "$container" >/dev/null 2>&1 || true; fi
  rm -rf -- "$prefix"
}
trap cleanup EXIT
container=$(docker create --network none "$xbt_image")
docker cp "$container:/usr/local/." "$prefix/"
docker rm "$container" >/dev/null
container=''
results=$(mktemp -d /tmp/remote-cln-tests.XXXXXX)
printf 'Disposable logs: %s\nBTC image: %s\nXBT image: %s\n' "$results" "$btc_image" "$xbt_image"
modes=("$mode")
if [ "$mode" = all ]; then modes=(forward forward-failure reverse reverse-failure); fi
for scenario in "${modes[@]}"; do
  docker run --rm --network none --init \
    -e BTC_XBT_DISPOSABLE_CONTAINER=1 -e PYTHONDONTWRITEBYTECODE=1 \
    --mount "type=bind,src=$backend,dst=/test-bitcoind,readonly" \
    --mount "type=bind,src=$prefix,dst=/opt/xbt,readonly" \
    --mount "type=bind,src=$pair,dst=/pair-fixtures,readonly" \
    --mount "type=bind,src=$repo/assets,dst=/controller-assets,readonly" \
    --mount "type=bind,src=$repo/tests/remote,dst=/remote-tests,readonly" \
    --mount "type=bind,src=$results,dst=/results" \
    --entrypoint /usr/bin/python3 "$btc_image" \
    /remote-tests/image_remote.py "$scenario" "${flags[@]}"
done
