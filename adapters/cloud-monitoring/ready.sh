#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-cloud-monitoring: polls projects.timeSeries.query up to READY_TIMEOUT
# seconds. Silent on stdout; exit 0 when reachable, 1 on timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/cloud-monitoring/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if cm_curl_post "$CM_QUERY_URL" '{"query":"fetch global | within 1m"}' "$tmp/resp.json" 2>/dev/null \
      && jq -e 'type == "object" and (has("error") | not)' "$tmp/resp.json" >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready-cloud-monitoring: endpoint not ready after %ss: %s\n' "$READY_TIMEOUT" "$CM_QUERY_URL" >&2
exit 1
