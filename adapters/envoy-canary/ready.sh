#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-envoy-canary: waits until Envoy is serving and the workload passes
# WORKLOAD_HEALTH_PROBE (ADR 0098 §3-4). Default when unset/empty: http:/healthz.
# Not a gate (docs/adr/0012 §6): no GO/NO-GO, silent on success, one stderr
# line and exit 1 on timeout.
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"
: "${ENVOY_ADMIN_URL:=http://$PLANE_HOST:${DELIVERY_ADMIN_PORT:-9901}}"
: "${READY_TIMEOUT:=60}"
probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}

case "$probe" in
  http:/*)
    path=${probe#http:}
    i=0
    while [ "$i" -lt "$READY_TIMEOUT" ]; do
      code=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 1 --max-time 3 "$APP_URL$path" 2>/dev/null) || code=000
      case "$code" in
        2??|3??) exit 0 ;;
      esac
      i=$((i + 1))
      sleep 1
    done
    printf 'ready: envoy-canary not ready after %ss: GET %s%s (last HTTP %s)\n' "$READY_TIMEOUT" "$APP_URL" "$path" "$code" >&2
    exit 1
    ;;
  tcp|tcp:*)
    if [ "$probe" = "tcp" ]; then
      port=${WORKLOAD_PORT:-8000}
    else
      port=${probe#tcp:}
    fi
    i=0
    envoy_ok=0
    while [ "$i" -lt "$READY_TIMEOUT" ]; do
      if curl -fsS --connect-timeout 1 --max-time 3 "$ENVOY_ADMIN_URL/ready" >/dev/null 2>&1; then
        envoy_ok=1
        break
      fi
      i=$((i + 1))
      sleep 1
    done
    if [ "$envoy_ok" -ne 1 ]; then
      printf 'ready: envoy-canary admin not ready after %ss: GET %s/ready\n' "$READY_TIMEOUT" "$ENVOY_ADMIN_URL" >&2
      exit 1
    fi
    rem=$((READY_TIMEOUT - i))
    [ "$rem" -gt 0 ] || rem=1
    if ! docker-compose run --rm -T --no-deps probe python3 - "$port" "$rem" <<'PY' >/dev/null 2>&1
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.monotonic() + int(sys.argv[2])
while True:
    try:
        with socket.create_connection(("backend", port), timeout=2):
            sys.exit(0)
    except OSError:
        rem = deadline - time.monotonic()
        if rem <= 0:
            sys.exit(1)
        time.sleep(min(0.5, rem))
PY
    then
      printf 'ready: envoy-canary backend tcp:%s not ready after %ss\n' "$port" "$READY_TIMEOUT" >&2
      exit 1
    fi
    exit 0
    ;;
  *)
    printf 'ready: envoy-canary invalid WORKLOAD_HEALTH_PROBE=%s\n' "$probe" >&2
    exit 1
    ;;
esac
