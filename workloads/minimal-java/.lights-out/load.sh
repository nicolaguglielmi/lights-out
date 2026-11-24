#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"
: "${WORKLOAD_SAMPLE_PATH:=/}"

set -- -s -o /dev/null --connect-timeout 1 --max-time 3
if [ -n "${SMOKE_AUTH_HEADER:-}" ]; then
  set -- "$@" -H "$SMOKE_AUTH_HEADER"
fi
while true; do
  curl "$@" "$APP_URL$WORKLOAD_SAMPLE_PATH" || true
  sleep 0.2
done
