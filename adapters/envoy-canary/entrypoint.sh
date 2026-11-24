#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# envoy-canary entrypoint: keeps /etc/envoy/envoy.yaml immutable and validated
# at build time, copies it to /tmp/envoy.yaml (writable as user: envoy),
# substitutes WORKLOAD_PORT when != 8000, and execs envoy (docs/adr/0100).
set -eu

port=${WORKLOAD_PORT:-8000}
case "$port" in
  ""|*[!0-9]*|0*)
    printf 'envoy-canary: invalid WORKLOAD_PORT=%s (want integer 1..65535)\n' "$port" >&2
    exit 1
    ;;
esac
if [ "${#port}" -gt 5 ] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
  printf 'envoy-canary: invalid WORKLOAD_PORT=%s (want integer 1..65535)\n' "$port" >&2
  exit 1
fi

src=${ENVOY_CONFIG_SRC:-/etc/envoy/envoy.yaml}
dst=${ENVOY_CONFIG_DST:-/tmp/envoy.yaml}

if [ "$port" -eq 8000 ]; then
  cp "$src" "$dst"
else
  sed "s/port_value: 8000/port_value: $port/g" "$src" > "$dst"
fi

exec envoy -c "$dst" "$@"
