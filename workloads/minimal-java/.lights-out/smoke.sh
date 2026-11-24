#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Optional auth header (e.g. SMOKE_AUTH_HEADER="Authorization: Bearer <token>"), never embedded.
set -- -sS -o "$tmp/out" -w '%{http_code}' --connect-timeout 1 --max-time 3
if [ -n "${SMOKE_AUTH_HEADER:-}" ]; then
  set -- "$@" -H "$SMOKE_AUTH_HEADER"
fi
if ! code=$(curl "$@" "$APP_URL/healthz" 2>"$tmp/err"); then
  printf 'smoke: GET /healthz failed: %s\n' "$(cat "$tmp/err")" >&2
  exit 1
fi
case "$code" in
  2??|3??) ;;
  *) printf 'smoke: GET /healthz answered %s, want 2xx or 3xx\n' "$code" >&2; exit 1 ;;
esac

printf 'smoke: GET /healthz answered %s through proxy at %s\n' "$code" "$APP_URL" >&2
