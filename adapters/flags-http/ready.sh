#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-flags-http: polls OFREP POST /ofrep/v1/evaluate/flags/release-freeze.
# Silent on success, one stderr line and exit 1 on failure or timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/flags-http/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if flags_http_post "$FLAGS_URL/ofrep/v1/evaluate/flags/release-freeze" '{"context":{}}' "$tmp/resp.json" \
     && jq -e 'has("value") and (.value != null)' "$tmp/resp.json" >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done

printf 'ready-flags-http: OFREP endpoint not ready after %ss: POST %s/ofrep/v1/evaluate/flags/release-freeze\n' "$READY_TIMEOUT" "$FLAGS_URL" >&2
exit 1
