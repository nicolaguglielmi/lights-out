#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-metrics query.sh: cold file-backed Metrics fixture returning
# Prometheus-compatible /api/v1/query JSON on stdout (ADR 0058, ADR 0133).
set -eu
query=${1:-}
[ -n "$(printf '%s' "$query" | tr -d '[:space:]')" ] || {
  printf 'file-metrics: usage: query.sh <query>\n' >&2
  exit 1
}

command -v python3 >/dev/null 2>&1 || {
  printf 'file-metrics: python3 is not on PATH\n' >&2
  exit 1
}

exec python3 -I - "$query" <<'PY'
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

query = sys.argv[1].strip()
metrics_url = (os.environ.get("METRICS_URL") or "").strip()
if metrics_url:
    parsed = urlparse(metrics_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        sys.stderr.write(f"file-metrics: invalid METRICS_URL {metrics_url!r}\n")
        raise SystemExit(1)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((parsed.hostname, port), timeout=1):
            pass
    except OSError as exc:
        sys.stderr.write(f"file-metrics: METRICS_URL {metrics_url} unreachable: {exc}\n")
        raise SystemExit(1) from None

pairs = {"(": ")", "[": "]", "{": "}"}
stack = []
in_quote = None
escaped = False
for ch in query:
    if escaped:
        escaped = False
        continue
    if ch == "\\" and in_quote:
        escaped = True
        continue
    if in_quote:
        if ch == in_quote:
            in_quote = None
        continue
    if ch in ('"', "'"):
        in_quote = ch
    elif ch in pairs:
        stack.append(pairs[ch])
    elif ch in pairs.values():
        if not stack or stack.pop() != ch:
            sys.stderr.write(f"file-metrics: malformed query syntax: {query!r}\n")
            raise SystemExit(1)
if in_quote or stack or "invalid" in query.lower():
    sys.stderr.write(f"file-metrics: malformed query syntax: {query!r}\n")
    raise SystemExit(1)

state_dir = Path(os.environ.get("FILE_METRICS_DIR") or ".crew/file-metrics")
db_path = state_dir / "metrics.json"
if db_path.exists():
    try:
        store = json.loads(db_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"file-metrics: cannot read {db_path}: {exc}\n")
        raise SystemExit(1) from None
    if not isinstance(store, dict):
        sys.stderr.write(f"file-metrics: {db_path} is not a JSON object\n")
        raise SystemExit(1)
else:
    store = {"up": [{"metric": {"__name__": "up", "service_version": "0.1.0"}, "value": [1700000000, "1"]}]}

result = store.get(query, [])
if not isinstance(result, list):
    sys.stderr.write(f"file-metrics: metric {query!r} is not a list\n")
    raise SystemExit(1)

print(json.dumps({"status": "success", "data": {"resultType": "vector", "result": result}}))
PY
