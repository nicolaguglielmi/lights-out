#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-firestore: polls Cloud Firestore REST v1 up to READY_TIMEOUT seconds
# (accepting 200 or 404 on a probe document). Silent on exit 0; one stderr line
# and exit 1 on timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/firestore/lib.sh
. "$here/lib.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if firestore_resolve_token 2>/dev/null; then
    code=$(firestore_curl GET "$FIRESTORE_URL/__ready__/__probe__" "" "$tmp/resp.json")
    case "$code" in
      200|404) exit 0 ;;
    esac
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready: firestore not ready after %ss: %s\n' "$READY_TIMEOUT" "$FIRESTORE_URL" >&2
exit 1
