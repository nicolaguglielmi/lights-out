"""Draft a repository's plane half from its container build file (ADR 0100, ADR 0110): stdlib only.

Inspects a Containerfile or Dockerfile at the repository root (or under the
selected INIT_SERVICE subdirectory in monorepo mode) to extract the exposed
port, an explicit HTTP healthcheck path (falling back honestly to tcp:<port>),
runtime/test multi-stage targets, and stateful dependency hints from
.env.example / compose.yaml / docker-compose.yml, then writes compose.yaml,
flags/flags.json, auth-aware smoke.sh and load.sh, and crew-egress.txt.example
into the given contract directory.
"""

from __future__ import annotations

import json
import os
import posixpath
import re

DEFAULT_PORT = 8000
DEFAULT_CANARY_VERSION = "0.2.0-canary"
DEFAULT_SAMPLE_PATH = "/"
PLANE_KEYS = ("CANARY_VERSION", "WORKLOAD_KILL_SWITCH", "WORKLOAD_SAMPLE_PATH")
_FORBIDDEN_PATH_CHARS = frozenset("$#{}<>\\'\"")

_ENV_DEP_RULES = (
    (
        "postgres",
        re.compile(
            r"(?:^\s*(?:export\s+)?(?:DATABASE_URL|POSTGRES_(?:URL|HOST|DB))\s*=|postgres(?:ql)?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "redis",
        re.compile(
            r"(?:^\s*(?:export\s+)?REDIS_(?:URL|HOST)\s*=|rediss?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "mysql",
        re.compile(
            r"(?:^\s*(?:export\s+)?MYSQL_(?:URL|HOST|DATABASE)\s*=|mysql://)",
            re.IGNORECASE,
        ),
    ),
)

_COMPOSE_DEP_RULES = (
    (
        "postgres",
        re.compile(
            r"(?:^\s*image\s*:\s*['\"]?(?:[\w./-]+/)?(?:postgres|postgis|timescaledb)(?:[:@\s'\"]|$)"
            r"|^\s{2}(?:postgres|postgresql)\s*:"
            r"|\bPOSTGRES_(?:DB|USER|PASSWORD|HOST)\b"
            r"|postgres(?:ql)?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "redis",
        re.compile(
            r"(?:^\s*image\s*:\s*['\"]?(?:[\w./-]+/)?(?:redis|valkey)(?:[:@\s'\"]|$)"
            r"|^\s{2}(?:redis|valkey)\s*:"
            r"|\bREDIS_(?:URL|HOST)\b"
            r"|rediss?://)",
            re.IGNORECASE,
        ),
    ),
    (
        "mysql",
        re.compile(
            r"(?:^\s*image\s*:\s*['\"]?(?:[\w./-]+/)?(?:mysql|mariadb)(?:[:@\s'\"]|$)"
            r"|^\s{2}(?:mysql|mariadb)\s*:"
            r"|\bMYSQL_(?:URL|HOST|DATABASE)\b"
            r"|mysql://)",
            re.IGNORECASE,
        ),
    ),
)


def _normalize_service_rel(service: str | None) -> str:
    raw = (os.environ.get("INIT_SERVICE", "") if service is None else service).strip()
    if not raw or raw == "." or os.path.isabs(raw) or raw.startswith("/"):
        return ""
    norm = posixpath.normpath(raw.replace(os.sep, "/")).strip("/")
    if norm in ("", ".") or norm == ".." or norm.startswith("../"):
        return ""
    return norm


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


def detect_stateful_deps(repo: str | os.PathLike[str], service: str | None = None) -> list[str]:
    """Detect postgres/redis/mysql hints from non-comment lines in .env.example and compose files."""
    root = os.fspath(repo)
    norm_service = _normalize_service_rel(service)
    search_dirs = [root]
    if norm_service:
        sub = os.path.join(root, norm_service)
        if os.path.isdir(sub):
            search_dirs.append(sub)

    detected: set[str] = set()
    for base in search_dirs:
        for env_name in (".env.example", ".env.sample", ".env.template"):
            path = os.path.join(base, env_name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    code = re.sub(r"\s+#.*$", "", stripped)
                    for dep, pat in _ENV_DEP_RULES:
                        if pat.search(code):
                            detected.add(dep)
        for comp_name in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml"):
            path = os.path.join(base, comp_name)
            if not os.path.isfile(path):
                continue
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    stripped = raw.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    code = re.sub(r"\s+#.*$", "", raw.rstrip())
                    for dep, pat in _COMPOSE_DEP_RULES:
                        if pat.search(code):
                            detected.add(dep)

    return [dep for dep in ("postgres", "redis", "mysql") if dep in detected]


def detect_container(repo: str | os.PathLike[str], service: str | None = None) -> dict | None:
    """Inspect Containerfile/Dockerfile (under service if set, else root); return metadata or None."""
    root = os.fspath(repo)
    norm_service = _normalize_service_rel(service)
    candidates: list[str] = []
    if norm_service:
        candidates.extend([f"{norm_service}/Containerfile", f"{norm_service}/Dockerfile"])
    candidates.extend(["Containerfile", "Dockerfile"])

    dockerfile = next((cand for cand in candidates if os.path.isfile(os.path.join(root, cand))), None)
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
        "service": norm_service,
        "port": port,
        "port_explicit": port_explicit,
        "port_evidence": port_evidence,
        "health_probe": health_probe,
        "health_path": health_path,
        "health_evidence": health_evidence,
        "has_runtime": has_runtime,
        "has_test": has_test,
        "stateful_deps": detect_stateful_deps(root, service=norm_service),
    }


def _render_stateful_hints(info: dict) -> str:
    deps = info.get("stateful_deps") or []
    if not deps:
        return ""
    df = info["dockerfile"]
    lines = [
        "",
        f"# Detected stateful dependencies ({', '.join(deps)}) from repo config.",
        "# Uncomment and pin image digests before enabling stateful plane or crew tests:",
        "#",
    ]
    crew_deps: list[str] = []
    crew_env: list[str] = []
    if "postgres" in deps:
        crew_deps.append("crew-postgres")
        crew_env.append("    TEST_DATABASE_URL: postgresql://app:app@crew-postgres:5432/app_test")
        lines.extend(
            [
                "#   postgres:",
                "#     image: docker.io/library/postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
                "#     environment: { POSTGRES_DB: app, POSTGRES_USER: app, POSTGRES_PASSWORD: app }",
                "#   crew-postgres:  # isolated crew test DB (ADR 0046 §6-7: network crew only, no published ports)",
                "#     image: docker.io/library/postgres:17-alpine@sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73",
                '#     profiles: ["crew"]',
                "#     networks: [crew]",
                '#     dns: ["192.0.2.1"]',
                "#     environment: { POSTGRES_DB: app_test, POSTGRES_USER: app, POSTGRES_PASSWORD: app }",
            ]
        )
    if "redis" in deps:
        crew_deps.append("crew-redis")
        crew_env.append("    REDIS_URL: redis://crew-redis:6379/0")
        lines.extend(
            [
                "#   redis:",
                "#     image: docker.io/library/redis:7-alpine",
                "#   crew-redis:  # isolated crew cache (ADR 0046 §6-7: network crew only, no published ports)",
                "#     image: docker.io/library/redis:7-alpine",
                '#     profiles: ["crew"]',
                "#     networks: [crew]",
                '#     dns: ["192.0.2.1"]',
            ]
        )
    if "mysql" in deps:
        crew_deps.append("crew-mysql")
        crew_env.append("    MYSQL_URL: mysql://app:app@crew-mysql:3306/app_test")
        lines.extend(
            [
                "#   mysql:",
                "#     image: docker.io/library/mysql:8.4",
                "#     environment: { MYSQL_DATABASE: app, MYSQL_USER: app, MYSQL_PASSWORD: app, MYSQL_ROOT_PASSWORD: app }",
                "#   crew-mysql:  # isolated crew test DB (ADR 0046 §6-7: network crew only, no published ports)",
                "#     image: docker.io/library/mysql:8.4",
                '#     profiles: ["crew"]',
                "#     networks: [crew]",
                '#     dns: ["192.0.2.1"]',
                "#     environment: { MYSQL_DATABASE: app_test, MYSQL_USER: app, MYSQL_PASSWORD: app, MYSQL_ROOT_PASSWORD: app }",
            ]
        )
    lines.extend(
        [
            "#",
            "# x-crew-worker isolation snippet (attach every CLI harness service to crew-* dependencies):",
            "# x-crew-worker: &crew-worker",
            "#   depends_on:",
            *[f"#     - {dep}" for dep in crew_deps],
            "#   environment:",
            *[f"# {entry}" for entry in crew_env],
            "#   # <harness-service>: *crew-worker",
            "#",
            "# Optional migration hook (uncomment and wire depends_on: migrate condition: service_completed_successfully):",
            "#   migrate:",
            "#     build:",
            "#       context: ${WORKLOAD}",
            f"#       dockerfile: {df}",
            '#     command: ["<migration-command>"]',
            '#     restart: "no"',
        ]
    )
    return "\n".join(lines) + "\n"


def _render_compose(info: dict) -> str:
    df = info["dockerfile"]
    backend_target = "      target: runtime\n" if info.get("has_runtime") else ""
    test_target = "      target: test\n" if info.get("has_test") else ""
    hints = _render_stateful_hints(info)
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
        f"{hints}"
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
        '# Optional auth header (e.g. SMOKE_AUTH_HEADER="Authorization: Bearer <token>"), never embedded.\n'
        'set -- -sS -o "$tmp/out" -w \'%{http_code}\' --connect-timeout 1 --max-time 3\n'
        'if [ -n "${SMOKE_AUTH_HEADER:-}" ]; then\n'
        '  set -- "$@" -H "$SMOKE_AUTH_HEADER"\n'
        "fi\n"
        f'if ! code=$(curl "$@" "$APP_URL{path}" 2>"$tmp/err"); then\n'
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
        "set -- -s -o /dev/null --connect-timeout 1 --max-time 3\n"
        'if [ -n "${SMOKE_AUTH_HEADER:-}" ]; then\n'
        '  set -- "$@" -H "$SMOKE_AUTH_HEADER"\n'
        "fi\n"
        "while true; do\n"
        '  curl "$@" "$APP_URL$WORKLOAD_SAMPLE_PATH" || true\n'
        "  sleep 0.2\n"
        "done\n"
    )


def _render_egress_example() -> str:
    return (
        "# Opt-in worker egress allowlist example (ADR 0046 §5).\n"
        "# Every entry in crew-egress.txt opens a direct outbound channel from the\n"
        "# isolated crew container to that host:port (data can leave to an account on\n"
        "# that host). Keep crew-egress.txt absent when dependencies are vendored or\n"
        "# pre-cached; copy to crew-egress.txt and uncomment only required registries.\n"
        "# Format: lowercase host:port, no wildcards, no IP literals.\n"
        "#\n"
        "# Python package index:\n"
        "# pypi.org:443\n"
        "# files.pythonhosted.org:443\n"
        "#\n"
        "# JavaScript / TypeScript registry:\n"
        "# registry.npmjs.org:443\n"
        "#\n"
        "# Rust crates index:\n"
        "# crates.io:443\n"
        "# static.crates.io:443\n"
        "# index.crates.io:443\n"
        "#\n"
        "# Java / Kotlin Maven & Gradle repositories:\n"
        "# repo.maven.apache.org:443\n"
        "# services.gradle.org:443\n"
        "# plugins.gradle.org:443\n"
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
    service: str | None = None,
) -> dict[str, tuple[str, str]]:
    """Write plane files into contract_dir (preserving existing ones) and return plane keys."""
    del repo, service  # inspection results already captured in info; signature extends ADR 0100
    out = os.fspath(contract_dir)
    committed = committed or {}
    os.makedirs(os.path.join(out, "flags"), exist_ok=True)

    _write_if_missing(os.path.join(out, "compose.yaml"), _render_compose(info))
    _write_if_missing(os.path.join(out, "flags", "flags.json"), _render_flags())
    _write_if_missing(os.path.join(out, "smoke.sh"), _render_smoke(info), executable=True)
    _write_if_missing(os.path.join(out, "load.sh"), _render_load(), executable=True)
    _write_if_missing(os.path.join(out, "crew-egress.txt.example"), _render_egress_example())

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
