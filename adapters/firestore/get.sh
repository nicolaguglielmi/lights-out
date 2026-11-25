#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# get-firestore: prints <collection>/<id> as plain JSON on stdout.
# Exits 1 silently when the document does not exist (HTTP 404); prints one
# stderr line and exits 2 on any other failure so callers distinguish absent
# from unreachable/error.
set -eu
collection=${1:?usage: get.sh <collection> <id>}
id=${2:?usage: get.sh <collection> <id>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/firestore/lib.sh
. "$here/lib.sh"

firestore_validate_segment "$collection" collection || exit 2
firestore_validate_segment "$id" id || exit 2
firestore_resolve_token || exit 2

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

code=$(firestore_curl GET "$FIRESTORE_URL/$collection/$id" "" "$tmp/resp.json")
case "$code" in
  200) jq "$FS_DECODE" "$tmp/resp.json" ;;
  404) exit 1 ;;
  *)
    printf 'get-firestore: GET %s/%s returned %s\n' "$collection" "$id" "$code" >&2
    exit 2 ;;
esac
