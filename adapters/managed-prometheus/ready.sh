#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-managed-prometheus: polls Managed Service for Prometheus /api/v1/query
# up to READY_TIMEOUT seconds. Silent on exit 0; one stderr line on timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/managed-prometheus/lib.sh
. "$here/lib.sh"

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if mp_resolve_auth 2>/dev/null \
      && resp=$(mp_query_raw 'vector(1)' 2>/dev/null) \
      && printf '%s' "$resp" | jq -e '.status == "success"' >/dev/null 2>&1; then
    exit 0
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready: managed-prometheus not ready after %ss: %s/api/v1/query\n' "$READY_TIMEOUT" "$METRICS_URL" >&2
exit 1
