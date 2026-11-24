#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
pct=${1:?usage: shift.sh <5|25|100>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-delivery/env.sh
. "$here/env.sh"

case "$pct" in
  5 | 25 | 100) ;;
  *)
    printf 'file-delivery: unsupported canary weight %s (allowed: 5, 25, 100)\n' "$pct" >&2
    exit 1
    ;;
esac

init_state
canary=$(jq -r '.canary // empty' "$FILE_DELIVERY_STATE")
[ -n "$canary" ] || {
  printf 'file-delivery: no canary revision deployed; call deploy.sh before shift.sh\n' >&2
  exit 1
}
updated=$(jq -c --argjson w "$pct" '{stable: .stable, canary: .canary, weight: $w}' "$FILE_DELIVERY_STATE")
write_state "$updated"
