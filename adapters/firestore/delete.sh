#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# delete-firestore: removes <collection>/<id>; deleting an absent document is
# success (HTTP 200 or 404). Silent on success; one stderr line and exit 1 on failure.
set -eu
collection=${1:?usage: delete.sh <collection> <id>}
id=${2:?usage: delete.sh <collection> <id>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/firestore/lib.sh
. "$here/lib.sh"

firestore_validate_segment "$collection" collection || exit 1
firestore_validate_segment "$id" id || exit 1
firestore_resolve_token || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

code=$(firestore_curl DELETE "$FIRESTORE_URL/$collection/$id" "" "$tmp/resp.json")
case "$code" in
  200|404) exit 0 ;;
  *)
    printf 'delete-firestore: DELETE %s/%s returned %s\n' "$collection" "$id" "$code" >&2
    exit 1 ;;
esac
