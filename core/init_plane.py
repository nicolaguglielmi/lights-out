"""Draft a repository's plane half from its container build file (docs/adr/0100): stdlib only.

Inspects a Containerfile or Dockerfile at the repository root to extract the
exposed port, an explicit HTTP healthcheck path (falling back honestly to
tcp:<port>), and runtime/test multi-stage targets, then writes compose.yaml,
flags/flags.json, smoke.sh, and load.sh into the given contract directory.
"""

from __future__ import annotations

import json
import os
import re

DEFAULT_PORT = 8000
DEFAULT_CANARY_VERSION = "0.2.0-canary"
DEFAULT_SAMPLE_PATH = "/"
PLANE_KEYS = ("CANARY_VERSION", "WORKLOAD_KILL_SWITCH", "WORKLOAD_SAMPLE_PATH")
_FORBIDDEN_PATH_CHARS = frozenset("$#{}<>\\'\"")


def _logical_lines(text: str) -> list[str]:
    """Join backslash-continued Containerfile/Dockerfile lines, dropping comments."""
    out: list[str] = []
    buf: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.endswith("\\"):
            part = stripped[:-1].rstrip()
            if part:
                buf.append(part)
        else:
            if stripped:
                buf.append(stripped)
            if buf:
                out.append(" ".join(buf))
                buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def _extract_http_health_path(cmd_text: str) -> str | None:
    """Return a clear static HTTP path from a HEALTHCHECK CMD line, or None."""
    match = re.search(r"https?://([^\s\"'`\],;|)&]+)", cmd_text, re.IGNORECASE)
    if not match:
        return None
    host_and_path = match.group(1)
    if not host_and_path:
        return None
    _, sep, rest = host_and_path.partition("/")
    path = "/" + rest if sep else "/"
    if any(ch in path for ch in _FORBIDDEN_PATH_CHARS):
        return None
    if not re.fullmatch(r"/\S*", path):
        return None
    return path


def detect_container(repo: str | os.PathLike[str]) -> dict | None:
    """Inspect Containerfile then Dockerfile at repo root; return metadata or None."""
    root = os.fspath(repo)
    dockerfile = None
    for candidate in ("Containerfile", "Dockerfile"):
        if os.path.isfile(os.path.join(root, candidate)):
            dockerfile = candidate
            break
    if dockerfile is None:
        return None

    with open(os.path.join(root, dockerfile), encoding="utf-8", errors="replace") as fh:
        lines = _logical_lines(fh.read())

    port = DEFAULT_PORT
    port_explicit = False
    port_evidence = f"default {DEFAULT_PORT} ({dockerfile} has no literal EXPOSE port)"
    health_path: str | None = None
    has_runtime = False
    has_test = False

    for line in lines:
        stage_match = re.match(
            r"^FROM\s+(?:--\S+\s+)*\S+\s+AS\s+([A-Za-z0-9._-]+)(?:\s|$)",
            line,
            re.IGNORECASE,
        )
        if stage_match:
            stage = stage_match.group(1).lower()
            if stage == "runtime":
                has_runtime = True
            elif stage == "test":
                has_test = True
            continue

        if not port_explicit:
            expose_match = re.match(r"^EXPOSE\s+(.+)$", line, re.IGNORECASE)
            if expose_match:
                for token in expose_match.group(1).split():
                    pm = re.fullmatch(r"([1-9][0-9]*)(?:/tcp)?", token, re.IGNORECASE)
                    if pm and 1 <= int(pm.group(1)) <= 65535:
                        port = int(pm.group(1))
                        port_explicit = True
                        port_evidence = f"{dockerfile} EXPOSE {port}"
                        break
                continue

        hc_match = re.match(r"^HEALTHCHECK\s+(.+)$", line, re.IGNORECASE)
        if hc_match:
            rest = hc_match.group(1).strip()
            if rest.upper() == "NONE":
                health_path = None
            else:
                cmd_match = re.match(r"^(?:--\S+\s+)*CMD\s+(.+)$", rest, re.IGNORECASE)
                health_path = _extract_http_health_path(cmd_match.group(1)) if cmd_match else None

    if health_path is not None:
        health_probe = f"http:{health_path}"
        health_evidence = f"{dockerfile} HEALTHCHECK probes {health_path}"
    else:
        health_probe = f"tcp:{port}"
        health_evidence = f"{dockerfile} has no HTTP HEALTHCHECK; TCP connect on port {port}"

    return {
        "dockerfile": dockerfile,
        "port": port,
        "port_explicit": port_explicit,
        "port_evidence": port_evidence,
        "health_probe": health_probe,
        "health_path": health_path,
        "health_evidence": health_evidence,
        "has_runtime": has_runtime,
        "has_test": has_test,
    }


def _render_compose(info: dict) -> str:
    df = info["dockerfile"]
    backend_target = "      target: runtime\n" if info.get("has_runtime") else ""
    test_target = "      target: test\n" if info.get("has_test") else ""
    return (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "services:\n"
        "  backend:\n"
        "    image: localhost:5050/lights-out-backend:${APP_TAG:-latest}\n"
        "    build:\n"
        "      context: ${WORKLOAD}\n"
        f"      dockerfile: {df}\n"
        f"{backend_target}"
        "\n"
        "  backend-canary:\n"
        "    image: ${CANARY_IMAGE:-localhost:5050/lights-out-backend:latest}\n"
        '    profiles: ["canary"]\n'
        "\n"
        "  backend-test:\n"
        "    build:\n"
        "      context: ${WORKLOAD}\n"
        f"      dockerfile: {df}\n"
        f"{test_target}"
        '    profiles: ["test"]\n'
    )


def _render_flags() -> str:
    payload = {
        "flags": {
            "release-freeze": {
                "state": "ENABLED",
                "variants": {"on": True, "off": False},
                "defaultVariant": "off",
            }
        }
    }
    return json.dumps(payload, indent=2) + "\n"


def _render_smoke(info: dict) -> str:
    path = info.get("health_path") or "/"
    return (
        "#!/bin/sh\n"
        "# SPDX-License-Identifier: Apache-2.0\n"
        "set -eu\n"
        ': "${PLANE_HOST:=host.containers.internal}"\n'
        ': "${APP_URL:=http://$PLANE_HOST:8080}"\n'
        "\n"
        "tmp=$(mktemp -d)\n"
        'trap \'rm -rf "$tmp"\' EXIT\n'
        "\n"
        f'if ! code=$(curl -sS -o "$tmp/out" -w \'%{{http_code}}\' --connect-timeout 1 --max-time 3 "$APP_URL{path}" 2>"$tmp/err"); then\n'
        f'  printf \'smoke: GET {path} failed: %s\\n\' "$(cat "$tmp/err")" >&2\n'
        "  exit 1\n"
        "fi\n"
        'case "$code" in\n'
        "  2??|3??) ;;\n"
        f'  *) printf \'smoke: GET {path} answered %s, want 2xx or 3xx\\n\' "$code" >&2; exit 1 ;;\n'
        "esac\n"
        "\n"
        f'printf \'smoke: GET {path} answered %s through proxy at %s\\n\' "$code" "$APP_URL" >&2\n'
    )


def _render_load() -> str:
    return (
        "#!/bin/sh\n"
        "# SPDX-License-Identifier: Apache-2.0\n"
        "set -eu\n"
        ': "${PLANE_HOST:=host.containers.internal}"\n'
        ': "${APP_URL:=http://$PLANE_HOST:8080}"\n'
        ': "${WORKLOAD_SAMPLE_PATH:=/}"\n'
        "\n"
        "while true; do\n"
        '  curl -s -o /dev/null --connect-timeout 1 --max-time 3 "$APP_URL$WORKLOAD_SAMPLE_PATH" || true\n'
        "  sleep 0.2\n"
        "done\n"
    )


def _write_if_missing(path: str, content: str, executable: bool = False) -> None:
    if os.path.exists(path):
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    if executable:
        os.chmod(path, 0o755)  # nosec B103 - owner-writable script only


def draft_plane(
    repo: str | os.PathLike[str],
    contract_dir: str | os.PathLike[str],
    info: dict,
    committed: dict[str, str] | None = None,
) -> dict[str, tuple[str, str]]:
    """Write plane files into contract_dir (preserving existing ones) and return plane keys."""
    del repo  # inspection results already captured in info; signature matches ADR 0100
    out = os.fspath(contract_dir)
    committed = committed or {}
    os.makedirs(os.path.join(out, "flags"), exist_ok=True)

    _write_if_missing(os.path.join(out, "compose.yaml"), _render_compose(info))
    _write_if_missing(os.path.join(out, "flags", "flags.json"), _render_flags())
    _write_if_missing(os.path.join(out, "smoke.sh"), _render_smoke(info), executable=True)
    _write_if_missing(os.path.join(out, "load.sh"), _render_load(), executable=True)

    df = info["dockerfile"]
    inferred: dict[str, tuple[str, str]] = {
        "CANARY_VERSION": (
            DEFAULT_CANARY_VERSION,
            f"inferred: default canary version for {df} plane draft (docs/adr/0100)",
        ),
        "WORKLOAD_KILL_SWITCH": (
            "",
            f"inferred: empty; proxy SLO rollback needs no in-app kill switch ({df}, docs/adr/0098)",
        ),
        "WORKLOAD_HEALTH_PROBE": (
            info["health_probe"],
            f"inferred: {info['health_evidence']}",
        ),
        "WORKLOAD_SAMPLE_PATH": (
            DEFAULT_SAMPLE_PATH,
            f"inferred: default HTTP sample path for {df} proxy verification (docs/adr/0100)",
        ),
    }
    if info["port"] != DEFAULT_PORT:
        inferred["WORKLOAD_PORT"] = (str(info["port"]), f"inferred: {info['port_evidence']}")

    has_committed = (
        any(committed.get(k, "") for k in (*PLANE_KEYS, "WORKLOAD_HEALTH_PROBE", "WORKLOAD_PORT"))
        or ("WORKLOAD_HEALTH_PROBE" in committed)
        or ("WORKLOAD_PORT" in committed)
    )
    if not has_committed:
        return inferred

    result: dict[str, tuple[str, str]] = {}
    for k, default_pair in inferred.items():
        if k in committed:
            result[k] = (committed[k], "kept: from the committed contract")
        else:
            result[k] = default_pair
    for extra in ("WORKLOAD_HEALTH_PROBE", "WORKLOAD_PORT"):
        if extra in committed and extra not in result:
            result[extra] = (committed[extra], "kept: from the committed contract")
    return result
