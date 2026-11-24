#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# deploy-envoy-canary: starts backend-canary at <image> (the verified
# digest core/launchpad.sh passes, docs/adr/0025 §6) with container label
# lights-out.version=<version>, and waits until backend-canary passes
# WORKLOAD_HEALTH_PROBE directly inside the Compose network via the
# ephemeral `probe` service (ADR 0098 §4).
# <image> is a bare repo@digest (docs/adr/0025 §1); localhost:5050 is the
# one registry host both engines treat as insecure without configuration.
# No `--build`: the image already exists in Zot, pushed by
# adapters/$REGISTRY/publish.sh. `--no-deps`: the plane is `./go up`'s;
# on podman 4.9.3 compose would otherwise recreate the canary's
# dependencies under the running stable (docs/adr/0066 §1).
# Not a gate: no GO/NO-GO, one stderr line and exit 1 on timeout.
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
: "${READY_TIMEOUT:=60}"
probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}
port=${WORKLOAD_PORT:-8000}

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
override="$tmpdir/canary-override.yaml"
jq -cn --arg v "$version" \
  '{services: {"backend-canary": {labels: {"lights-out.version": $v}}}}' >"$override"

COMPOSE_FILE="${COMPOSE_FILE:-compose.yaml}:$override" \
  CANARY_VERSION="$version" \
  CANARY_IMAGE="localhost:5050/$image" \
  docker-compose --profile canary up -d --no-deps backend-canary >&2

if ! docker-compose run --rm -T --no-deps probe python3 - "$probe" "$READY_TIMEOUT" "$port" <<'PY' >&2
import http.client
import socket
import sys
import time

probe = sys.argv[1] or "http:/healthz"
timeout = int(sys.argv[2])
default_port = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else 8000
deadline = time.monotonic() + timeout
last = "no answer"

if probe.startswith("http:"):
    path = probe[5:]
    if not path.startswith("/"):
        print(f"deploy-envoy-canary: invalid HTTP probe {probe!r}", file=sys.stderr)
        sys.exit(2)
    check_kind = "http"
    target = path
elif probe == "tcp":
    check_kind = "tcp"
    target = default_port
elif probe.startswith("tcp:"):
    check_kind = "tcp"
    target = int(probe.split(":", 1)[1])
else:
    print(f"deploy-envoy-canary: invalid WORKLOAD_HEALTH_PROBE {probe!r}", file=sys.stderr)
    sys.exit(2)

while True:
    try:
        if check_kind == "http":
            conn = http.client.HTTPConnection("backend-canary", default_port, timeout=2)
            try:
                conn.request("GET", target)
                resp = conn.getresponse()
                resp.read()
                if 200 <= resp.status < 400:
                    sys.exit(0)
                last = f"HTTP {resp.status}"
            finally:
                conn.close()
        else:
            with socket.create_connection(("backend-canary", target), timeout=2):
                sys.exit(0)
    except OSError as exc:
        last = str(exc)
    rem = deadline - time.monotonic()
    if rem <= 0:
        break
    time.sleep(min(0.5, rem))

print(last, file=sys.stderr)
sys.exit(1)
PY
then
  printf 'deploy-envoy-canary: backend-canary failed probe %s after %ss\n' "$probe" "$READY_TIMEOUT" >&2
  exit 1
fi
