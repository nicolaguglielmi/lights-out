#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${APP_URL:=http://$PLANE_HOST:8080}"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for path in /ok /; do
  if ! code=$(curl -sS -o "$tmp/out" -w '%{http_code}' --connect-timeout 1 --max-time 3 "$APP_URL$path" 2>"$tmp/err"); then
    printf 'smoke: GET %s failed: %s\n' "$path" "$(cat "$tmp/err")" >&2
    exit 1
  fi
  [ "$code" = "200" ] || { printf 'smoke: GET %s answered %s, want 200\n' "$path" "$code" >&2; exit 1; }
done

printf 'smoke: /ok and / answered 200 through Envoy at %s\n' "$APP_URL" >&2
