#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-metrics ready.sh: cold file-backed Metrics fixture readiness
# probe (ADR 0058, ADR 0133). Silent on exit 0; 1 stderr line on error.
set -eu

for tool in jq python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'file-metrics: %s is not on PATH\n' "$tool" >&2
    exit 1
  }
done

if [ -n "${METRICS_URL:-}" ]; then
  if ! python3 -I - "$METRICS_URL" <<'PY'
import socket, sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
if u.scheme not in ("http", "https") or not u.hostname:
    raise SystemExit(1)
port = u.port or (443 if u.scheme == "https" else 80)
with socket.create_connection((u.hostname, port), timeout=1):
    pass
PY
  then
    printf 'file-metrics: METRICS_URL %s is unreachable\n' "$METRICS_URL" >&2
    exit 1
  fi
fi

dir=${FILE_METRICS_DIR:-.crew/file-metrics}
mkdir -p "$dir" || {
  printf 'file-metrics: cannot create directory %s\n' "$dir" >&2
  exit 1
}

db="$dir/metrics.json"
if [ ! -f "$db" ]; then
  tmp=$(mktemp "$dir/.metrics.XXXXXX")
  printf '{"up":[{"metric":{"__name__":"up","service_version":"0.1.0"},"value":[1700000000,"1"]}]}\n' > "$tmp"
  mv "$tmp" "$db"
fi

jq -e 'type == "object"' "$db" >/dev/null 2>&1 || {
  printf 'file-metrics: %s is not a valid JSON object\n' "$db" >&2
  exit 1
}
