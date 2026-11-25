#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# put-firestore: replaces the whole document <collection>/<id> from plain JSON
# object on stdin (no updateMask: full document replacement per ADR 0019).
# Silent on success; one stderr line and exit 1 on failure.
set -eu
collection=${1:?usage: put.sh <collection> <id> < document.json}
id=${2:?usage: put.sh <collection> <id> < document.json}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/firestore/lib.sh
. "$here/lib.sh"

firestore_validate_segment "$collection" collection || exit 1
firestore_validate_segment "$id" id || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

cat > "$tmp/raw.json"
if ! jq -cs 'length == 1 and (.[0] | type == "object")' "$tmp/raw.json" 2>/dev/null | grep -qx true; then
  printf 'put-firestore: stdin is not a JSON object\n' >&2
  exit 1
fi
jq -c "$FS_ENCODE" "$tmp/raw.json" > "$tmp/body.json" || {
  printf 'put-firestore: failed to encode JSON object\n' >&2
  exit 1
}

firestore_resolve_token || exit 1
code=$(firestore_curl PATCH "$FIRESTORE_URL/$collection/$id" "$tmp/body.json" "$tmp/resp.json")
[ "$code" = "200" ] || {
  printf 'put-firestore: PATCH %s/%s returned %s\n' "$collection" "$id" "$code" >&2
  exit 1
}
