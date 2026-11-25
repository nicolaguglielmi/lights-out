#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-unleash: polls Unleash client features (or health) endpoint with stdin
# -H @- auth up to READY_TIMEOUT seconds. Silent on exit 0; 1-line stderr on error.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/unleash/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  code=$(unleash_curl GET "$UNLEASH_URL/api/client/features" "$tmp/features.json")
  if [ "$code" = "200" ] && jq -e 'type == "object"' "$tmp/features.json" >/dev/null 2>&1; then
    exit 0
  fi
  if [ "$code" = "404" ]; then
    hcode=$(unleash_curl GET "$UNLEASH_URL/health" "$tmp/health.json")
    if [ "$hcode" = "200" ] \
        && jq -e 'type == "object" and ((.health // "") == "GOOD" or (.status // "") == "ok" or (.status // "") == "UP")' \
          "$tmp/health.json" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready-unleash: endpoint not ready after %ss: GET %s/api/client/features\n' "$READY_TIMEOUT" "$UNLEASH_URL" >&2
exit 1
