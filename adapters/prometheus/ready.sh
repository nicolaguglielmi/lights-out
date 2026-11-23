#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-prometheus: polls until the envoy scrape target is up, not a gate
# (docs/adr/0012 §6): no GO/NO-GO, silent on success, one stderr line and
# exit 1 on timeout.
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${READY_TIMEOUT:=60}"

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if curl -fsS --connect-timeout 1 --max-time 3 -G "http://$PLANE_HOST:${METRICS_PORT:-9090}/api/v1/query" \
      --data-urlencode 'query=up{job="envoy"}' 2>/dev/null | grep -q ',"1"]'
  then
    exit 0
  fi
  i=$((i + 1))
  sleep 1
done
printf 'ready: prometheus not ready after %ss: GET http://%s:%s/api/v1/query?query=up{job="envoy"}\n' "$READY_TIMEOUT" "$PLANE_HOST" "${METRICS_PORT:-9090}" >&2
exit 1
