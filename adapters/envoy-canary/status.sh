#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# status-envoy-canary: the Delivery port's state verb (docs/adr/0026 §2):
# one JSON object, exactly three keys — stable, canary (string or null),
# weight (0-100) — read from the container engine socket (ADR 0098 §4)
# and Envoy runtime admin API, not from RELEASE_RECORD or /healthz JSON.
# Version precedence per container:
#   lights-out.version label -> APP_VERSION env -> image tag/digest.
# Not a gate (docs/adr/0012 §6): exit 1 and one stderr line if a revision
# that should answer does not.
set -eu
: "${PLANE_HOST:=host.containers.internal}"
: "${ENVOY_ADMIN_URL:=http://$PLANE_HOST:${DELIVERY_ADMIN_PORT:-9901}}"

versions_json=$(python3 - <<'PY'
import http.client
import json
import os
import re
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: int = 5):
        super().__init__("localhost", timeout=timeout)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.socket_path)
        self.sock = sock


def engine_get(path: str):
    host = os.environ.get("DOCKER_HOST") or "unix:///var/run/docker.sock"
    if host.startswith("unix://"):
        conn = UnixHTTPConnection(host[7:], timeout=5)
    else:
        u = urlparse(host.replace("tcp://", "http://", 1))
        conn = http.client.HTTPConnection(u.hostname or "localhost", u.port or 2375, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"GET {path} returned HTTP {resp.status}")
        return json.loads(body)
    finally:
        conn.close()


def extract_version(summary: dict, detail: dict) -> str:
    cfg = detail.get("Config") or {}
    labels = dict(summary.get("Labels") or {})
    labels.update(cfg.get("Labels") or {})
    v = (labels.get("lights-out.version") or "").strip()
    if v:
        return v
    for item in cfg.get("Env") or []:
        if item.startswith("APP_VERSION="):
            ev = item.split("=", 1)[1].strip()
            if ev:
                return ev
    img = (cfg.get("Image") or summary.get("Image") or "").strip()
    if img:
        if "@" in img:
            return img.split("@", 1)[1]
        tail = img.rsplit("/", 1)[-1]
        if ":" in tail:
            return tail.split(":", 1)[1]
        return img
    return ""


try:
    containers = engine_get("/containers/json")
except Exception as exc:
    print(f"status-envoy-canary: could not query container engine socket: {exc}", file=sys.stderr)
    sys.exit(1)

running = [c for c in containers if c.get("State") == "running"]
project_env = (os.environ.get("COMPOSE_PROJECT_NAME") or "").strip()
cwd = os.getcwd()
cwd_proj = re.sub(r"[^a-z0-9_-]", "", Path(cwd).name.lower())

if project_env:
    scoped = [
        c for c in running
        if (c.get("Labels") or {}).get("com.docker.compose.project") == project_env
    ]
else:
    by_workdir = [
        c for c in running
        if (c.get("Labels") or {}).get("com.docker.compose.working_dir") == cwd
    ]
    if by_workdir:
        scoped = by_workdir
    else:
        by_name = [
            c for c in running
            if (c.get("Labels") or {}).get("com.docker.compose.project") in (cwd_proj, "lights-out")
        ]
        scoped = by_name or running

by_service = {}
for c in scoped:
    svc = (c.get("Labels") or {}).get("com.docker.compose.service")
    if svc in ("backend", "backend-canary") and svc not in by_service:
        by_service[svc] = c

if "backend" not in by_service:
    print("status-envoy-canary: backend container is not running", file=sys.stderr)
    sys.exit(1)

def read_container_version(svc: str) -> str:
    summary = by_service[svc]
    detail = engine_get(f"/containers/{summary['Id']}/json")
    if not (detail.get("State") or {}).get("Running", True):
        print(f"status-envoy-canary: {svc} is not running", file=sys.stderr)
        sys.exit(1)
    ver = extract_version(summary, detail)
    if not ver:
        print(f"status-envoy-canary: {svc} did not expose a version via label, env, or image", file=sys.stderr)
        sys.exit(1)
    return ver

stable = read_container_version("backend")
canary = read_container_version("backend-canary") if "backend-canary" in by_service else None
print(json.dumps({"stable": stable, "canary": canary}))
PY
) || exit 1

stable=$(printf '%s' "$versions_json" | jq -r .stable)
canary_json=$(printf '%s' "$versions_json" | jq -c .canary)

weight=$(curl -fsS --connect-timeout 1 --max-time 3 "$ENVOY_ADMIN_URL/runtime" \
    | jq -r '.entries["weights.canary"].final_value // 0') \
  || { printf 'status-envoy-canary: could not read %s/runtime\n' "$ENVOY_ADMIN_URL" >&2; exit 1; }

jq -cn --arg s "$stable" --argjson c "$canary_json" --argjson w "$weight" '{stable: $s, canary: $c, weight: $w}'
