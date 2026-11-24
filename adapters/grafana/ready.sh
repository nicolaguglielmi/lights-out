#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-grafana: polls /api/health (or /api/v1/query?query=up fallback) up to
# READY_TIMEOUT seconds. Silent on stdout; exit 0 when ready, 1 on timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/grafana/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if grafana_curl_get -o /dev/null "$GRAFANA_BASE_URL/api/health" 2>/dev/null; then
    exit 0
  fi
  if grafana_curl_get -o "$tmp/q.json" -G "$GRAFANA_BASE_URL/api/v1/query" \
      --data-urlencode 'query=up' 2>/dev/null \
      && jq -e '.status == "success"' "$tmp/q.json" >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready-grafana: endpoint not ready after %ss: %s\n' "$READY_TIMEOUT" "$GRAFANA_BASE_URL" >&2
exit 1
