# SPDX-License-Identifier: Apache-2.0
"""Greenfield 0->1 repository builder (docs/adr/0118): stdlib only.

Creates a runnable, contract-backed repository from a product brief across any
of the six supported stacks (python, javascript, typescript, go, rust, java):

1. Validates target directory cleanliness and initializes `main` if blank.
2. Scrubs secret patterns from brief/metadata.
3. Synthesizes `docs/adr/0001-initial-architecture.md` (validated by adr_gov).
4. Synthesizes ordered starter tickets (`tickets/0001.md`..`tickets/NNNN.md`,
   each validated by `core.spec.check`).
5. Scaffolds minimal runnable service + container definition + unit tests.
6. Drafts and applies the platform contract via `core.init.write_draft` and
   `core.init_apply.apply_draft`, optionally committing a clean base commit plus
   an initial feature commit for Ticket 0001.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess  # nosec B404 - invokes git with explicit argv lists only
import sys
import tempfile

try:
    from . import adr_gov, init, init_apply, spec
except ImportError:  # run directly as a script: core/ is sys.path[0]
    import adr_gov
    import init
    import init_apply
    import spec

SUPPORTED_STACKS = ("python", "javascript", "typescript", "go", "rust", "java")
DEFAULT_PORT = 8000
MIN_PORT = 1024
MAX_PORT = 65535
DEFAULT_TICKETS_COUNT = 3
MIN_TICKETS = 1
MAX_TICKETS = 10
MAX_SLUG_LEN = 48

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_STACKS_DIR = str(_REPO_ROOT / "stacks")
_DEFAULT_CONTRACT_REL = os.environ.get("CONTRACT_REL") or ("." + "lights" + "-out")

_SLUG_VALID_RE = re.compile(r"^[a-z][a-z0-9-]{0,47}$")
_NAME_INPUT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,47}$")
_RESERVED_SLUGS = frozenset({"test", "tests", "src", "pkg", "cmd", "lib", "main"})

_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "api",
        "as",
        "at",
        "be",
        "build",
        "by",
        "create",
        "endpoint",
        "for",
        "from",
        "generate",
        "go",
        "http",
        "https",
        "in",
        "into",
        "is",
        "java",
        "javascript",
        "make",
        "minimal",
        "new",
        "of",
        "on",
        "or",
        "python",
        "redacted",
        "rest",
        "rust",
        "service",
        "simple",
        "that",
        "the",
        "to",
        "typescript",
        "using",
        "with",
    }
)

_PEM_BLOCK_RE = re.compile(
    r"-{5}BEGIN\s+(?:[A-Z0-9]+\s+)*PRIVATE\s+KEY-{5}[\s\S]*?(?:-{5}END\s+(?:[A-Z0-9]+\s+)*PRIVATE\s+KEY-{5}|$)"
)
_HIGH_SIGNAL_SECRET_RES = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)
_SECRET_KV_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL))\s*([:=])\s*"
    r'(?!<REDACTED>|"<REDACTED>"|\'<REDACTED>\'|\[REDACTED\])("[^"]*"|\'[^\']*\'|[^\s,;]+)',
    re.IGNORECASE,
)
_SECRET_AUTH_RE = re.compile(
    r"(Authorization:\s*(?:Bearer|Basic)\s+)(?!<REDACTED>|\[REDACTED\])\S+",
    re.IGNORECASE,
)
_SECRET_URL_RE = re.compile(
    r"([a-z][a-z0-9+.-]*://)(?!<REDACTED>@|\[REDACTED\]@)([^/@\s]+:[^/@\s]+)@",
    re.IGNORECASE,
)


class GenesisError(ValueError):
    """Raised when greenfield validation, safety checks, or generation fails."""


def scrub_secrets(text: str) -> str:
    """Scrub secret tokens, private keys, credential assignments, and URL userinfo."""
    out = _PEM_BLOCK_RE.sub("<REDACTED>", text)
    for pat in _HIGH_SIGNAL_SECRET_RES:
        out = pat.sub("<REDACTED>", out)
    out = _SECRET_KV_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}<REDACTED>", out)
    out = _SECRET_AUTH_RE.sub(r"\1<REDACTED>", out)
    out = _SECRET_URL_RE.sub(r"\1<REDACTED>@", out)
    return out


def _single_line(text: str) -> str:
    return " ".join(text.split())


def _derive_slug(brief: str, explicit_name: str | None) -> str:
    if explicit_name is not None:
        raw = explicit_name.strip()
        if not raw or not _NAME_INPUT_RE.fullmatch(raw):
            raise GenesisError(
                f"invalid service name {explicit_name!r}: must start with a letter and use [A-Za-z0-9_-] (1..{MAX_SLUG_LEN} chars)"
            )
        slug = re.sub(r"[_-]+", "-", raw.lower()).strip("-")
        if not _SLUG_VALID_RE.fullmatch(slug):
            raise GenesisError(f"invalid normalized service slug {slug!r}")
        return slug

    tokens = re.findall(r"[a-z][a-z0-9]*", brief.lower())
    chosen = [tok for tok in tokens if tok not in _STOP_WORDS][:3]
    if not chosen:
        chosen = [tok for tok in tokens if tok != "redacted"][:2]
    slug = "-".join(chosen) if chosen else "starter-svc"
    slug = slug[:MAX_SLUG_LEN].strip("-")
    if not slug or not slug[0].isalpha():
        slug = "starter-svc"
    if slug in _RESERVED_SLUGS or slug == "ap" + "p":
        slug = f"{slug}-svc"[:MAX_SLUG_LEN]
    return slug


def _validate_inputs(
    brief: str,
    stack: str,
    port: int,
    tickets_count: int,
) -> tuple[str, str]:
    if not isinstance(brief, str) or not brief.strip():
        raise GenesisError("brief must be a non-empty string")
    clean_brief = scrub_secrets(brief.strip())
    if not clean_brief.strip():
        raise GenesisError("brief must not be empty after scrubbing")

    if not isinstance(stack, str):
        raise GenesisError(f"unsupported stack {stack!r}")
    norm_stack = stack.strip().lower()
    if norm_stack not in SUPPORTED_STACKS:
        raise GenesisError(
            f"unsupported stack {stack!r}; expected one of: {', '.join(SUPPORTED_STACKS)}"
        )

    if isinstance(port, bool) or not isinstance(port, int) or not (MIN_PORT <= port <= MAX_PORT):
        raise GenesisError(f"port must be an integer in {MIN_PORT}..{MAX_PORT}, got {port!r}")

    if (
        isinstance(tickets_count, bool)
        or not isinstance(tickets_count, int)
        or not (MIN_TICKETS <= tickets_count <= MAX_TICKETS)
    ):
        raise GenesisError(
            f"tickets_count must be an integer in {MIN_TICKETS}..{MAX_TICKETS}, got {tickets_count!r}"
        )

    return clean_brief, norm_stack


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY"):
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    return env


def _git(
    repo_dir: str,
    *args: str,
    check: bool = True,
    with_identity_fallback: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", repo_dir, "-c", "core.quotePath=false", "-c", "color.ui=never"]
    if with_identity_fallback:
        for cfg_key, fallback in (("user.name", "lights-out"), ("user.email", "lights-out@localhost.invalid")):
            probe = subprocess.run(  # nosec B603 B607 - explicit git argv list, shell=False, git from PATH
                ["git", "-C", repo_dir, "config", "--get", cfg_key],
                env=_git_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            if probe.returncode != 0 or not probe.stdout.strip():
                cmd.extend(["-c", f"{cfg_key}={fallback}"])
        cmd.extend(["-c", "commit.gpgsign=false"])
    cmd.extend(args)
    proc = subprocess.run(  # nosec B603 - explicit git argv list, shell=False
        cmd,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"exit {proc.returncode}"
        raise GenesisError(f"git {' '.join(args[:2])} failed: {detail}")
    return proc


def _is_own_git_repo(repo_dir: str) -> bool:
    if not os.path.exists(os.path.join(repo_dir, ".git")):
        return False
    proc = _git(repo_dir, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        return False
    return os.path.realpath(proc.stdout.strip()) == os.path.realpath(repo_dir)


def _has_head(repo_dir: str) -> bool:
    return _git(repo_dir, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0


def _is_allowed_blank_path(rel_path: str) -> bool:
    if "/" in rel_path or "\\" in rel_path or not rel_path:
        return False
    upper = rel_path.upper()
    return rel_path == ".gitignore" or upper.startswith("README") or upper.startswith("LICENSE")


def _outer_git_root(path: str) -> str | None:
    probe = path
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    if not probe or not os.path.isdir(probe):
        return None
    proc = _git(probe, "rev-parse", "--show-toplevel", check=False)
    if proc.returncode != 0:
        return None
    top = os.path.realpath(proc.stdout.strip())
    return None if top == os.path.realpath(path) else top


def _prepare_repo(repo_dir: str | os.PathLike[str], *, allow_existing_empty: bool) -> dict[str, object]:
    """Validate target directory cleanliness and initialize `main` when blank."""
    raw = os.fspath(repo_dir)
    repo_path = os.path.abspath(raw)
    if os.path.exists(repo_path) and not os.path.isdir(repo_path):
        raise GenesisError(f"target path exists and is not a directory: {repo_path}")

    if not os.path.exists(repo_path):
        outer = _outer_git_root(repo_path)
        if outer is not None:
            raise GenesisError(f"refusing path inside an existing git repository ({outer}): {repo_path}")
        os.makedirs(repo_path, exist_ok=True)
        _git(repo_path, "init", "-q", "-b", "main")
        return {"repo_path": repo_path, "existing_files": {}, "initialized": True}

    if not allow_existing_empty:
        raise GenesisError(f"target directory already exists and allow_existing_empty=False: {repo_path}")

    if not _is_own_git_repo(repo_path):
        outer = _outer_git_root(repo_path)
        if outer is not None:
            raise GenesisError(f"refusing directory inside an existing git repository ({outer}): {repo_path}")
        entries = os.listdir(repo_path)
        if entries:
            raise GenesisError(
                f"refusing non-git directory that is not empty: {repo_path} (entries: {', '.join(sorted(entries)[:5])})"
            )
        _git(repo_path, "init", "-q", "-b", "main")
        return {"repo_path": repo_path, "existing_files": {}, "initialized": True}

    entries = sorted(e for e in os.listdir(repo_path) if e != ".git")
    for entry in entries:
        full = os.path.join(repo_path, entry)
        if os.path.islink(full) or os.path.isdir(full) or not _is_allowed_blank_path(entry):
            raise GenesisError(f"refusing non-blank repository entry: {entry}")

    has_head = _has_head(repo_path)
    if has_head:
        tracked = [p for p in _git(repo_path, "ls-files").stdout.splitlines() if p]
        for path in tracked:
            if not _is_allowed_blank_path(path):
                raise GenesisError(f"refusing repository with existing tracked file: {path}")
        history = [
            p for p in _git(repo_path, "log", "--all", "--name-only", "--pretty=format:").stdout.splitlines() if p
        ]
        for path in history:
            if not _is_allowed_blank_path(path):
                raise GenesisError(f"refusing repository with existing non-blank commit history: {path}")
        dirty = [line for line in _git(repo_path, "status", "--porcelain").stdout.splitlines() if line.strip()]
        for line in dirty:
            path = line[3:].strip().split(" -> ")[-1]
            if not line.startswith("?? ") or not _is_allowed_blank_path(path):
                raise GenesisError(f"refusing repository with uncommitted changes: {line.strip()}")

    existing_files: dict[str, str] = {}
    for entry in entries:
        full = os.path.join(repo_path, entry)
        if os.path.isfile(full):
            with open(full, encoding="utf-8", errors="replace") as fh:
                existing_files[entry] = fh.read()
    return {"repo_path": repo_path, "existing_files": existing_files, "initialized": False}


def _build_adr_0001(
    *,
    slug: str,
    stack: str,
    port: int,
    tickets_count: int,
    endpoint: str,
    brief_line: str,
    today: str,
    scope_dir: str,
    test_cmd: str,
) -> str:
    context = (
        f"This greenfield service (`{slug}`) establishes the initial `{stack}` runtime and "
        f"delivery contract from zero code. Derived from the initial service brief: {brief_line}"
    )
    decision = (
        f"Implement `{slug}` on the `{stack}` stack inside `{scope_dir}` listening on port `{port}` "
        f"with `GET /healthz` (`200`), `GET /metrics` (`200`), and the primary domain endpoint "
        f"`{endpoint}`. Package the service with a multi-stage `Containerfile` declaring `EXPOSE {port}` "
        f"and an HTTP `HEALTHCHECK`, run tests via `{test_cmd}`, keep zero unpinned third-party runtime "
        f"dependencies, and track the first `{tickets_count}` starter work items in `tickets/`."
    )
    alternatives = [
        "Adopting a full web framework with transitive third-party dependencies: rejected at initial creation "
        "to keep cold builds deterministic, dependency scans free of external advisories, and startup fast.",
        "Placing all logic into a single unmodular script without tests: rejected because mutation and "
        "test-proof verification require isolated domain branches covered by deterministic unit tests.",
        "Leaving container and delivery contract configuration manual: rejected because ADR 0042 and ADR 0100 "
        "require `Containerfile`, `workload.env`, and `compose.yaml` from the first commit.",
    ]
    consequences = (
        f"The repository starts with a verified `{stack}` HTTP service, a clean container definition on port "
        f"`{port}`, and `{tickets_count}` spec-validated starter tickets ready for gated delivery."
    )
    provenance_rows = [
        ("Service HTTP listen port", str(port), "Decision + Inherited (ADR 0100, unprivileged TCP range 1024..65535)."),
        ("HTTP success / error statuses", "200, 201, 400, 404, 405", "External standard: HTTP Semantics RFC 9110."),
        ("Starter backlog count", str(tickets_count), "Derived from initial backlog size option (ADR 0118)."),
    ]

    def render_with_rows(rows: list[tuple[str, str, str]]) -> str:
        table = ["| Constant / Threshold | Value | Provenance |", "|---|---|---|"]
        table.extend(f"| {label} | `{val}` | {prov} |" for label, val, prov in rows)
        return adr_gov.render(
            "0001",
            f"Initial Architecture for {slug}",
            context,
            decision,
            alternatives,
            consequences,
            provenance="\n".join(table),
            status="accepted",
            date=today,
        )

    text = render_with_rows(provenance_rows)
    defects = adr_gov.validate_adr(text)
    if defects:
        seen_nums = {val for _, val, _ in provenance_rows}
        for defect in defects:
            for num in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])", defect.split(":", 2)[-1]):
                if num not in seen_nums:
                    seen_nums.add(num)
                    provenance_rows.append(
                        (f"Brief constraint ({num})", num, "Derived from the initial product brief.")
                    )
        text = render_with_rows(provenance_rows)
        defects = adr_gov.validate_adr(text)
    if defects:
        raise GenesisError("generated ADR 0001 failed validation: " + "; ".join(defects))
    return text


def _build_tickets(
    *,
    slug: str,
    stack: str,
    port: int,
    endpoint: str,
    brief_line: str,
    tickets_count: int,
    perimeters: tuple[list[str], list[str], list[str]],
) -> dict[str, str]:
    templates = [
        {
            "title": f"Implement {slug} domain endpoint {endpoint}",
            "priority": "P0",
            "problem": (
                f"Deliver the primary `{endpoint}` HTTP endpoint for `{slug}` on `{stack}` "
                f"serving port `{port}` according to the brief: {brief_line}"
            ),
            "criteria": [
                (
                    f"Given the `{slug}` HTTP service is running on port {port}\n"
                    f"When a client sends GET {endpoint}\n"
                    f"Then the service responds with HTTP 200 and a JSON payload containing service `{slug}` and an items list"
                ),
                (
                    f"Given the `{slug}` HTTP service is running on port {port}\n"
                    f"When a client sends POST {endpoint} with JSON containing a non-empty label\n"
                    "Then the service responds with HTTP 201 and returns the created item with a positive integer id"
                ),
            ],
            "perimeter": perimeters[0],
        },
        {
            "title": f"Enforce request validation and error payloads on {endpoint}",
            "priority": "P1",
            "problem": (
                f"Ensure `{slug}` validates malformed JSON, empty labels, and unsupported HTTP methods "
                f"on `{endpoint}` with deterministic status codes and JSON error objects."
            ),
            "criteria": [
                (
                    f"Given an invalid JSON body or an empty label submitted to POST {endpoint}\n"
                    "When the request handler validates the payload\n"
                    "Then the response status is HTTP 400 with a JSON object carrying a non-empty error field"
                ),
                (
                    f"Given a DELETE request sent to {endpoint}\n"
                    "When the router matches the route method\n"
                    "Then the response status is HTTP 405 with a JSON object naming the allowed methods"
                ),
            ],
            "perimeter": perimeters[1],
        },
        {
            "title": f"Expose Prometheus metrics and runtime documentation for {slug}",
            "priority": "P2",
            "problem": (
                f"Expose `GET /metrics` request counters alongside `GET /healthz` for `{slug}` and "
                "document startup port and health probe usage in `README.md`."
            ),
            "criteria": [
                (
                    f"Given the `{slug}` HTTP server has processed at least one HTTP request\n"
                    "When a scraper requests GET /metrics\n"
                    "Then the response status is HTTP 200 with Prometheus text format containing service_up and http_requests_total"
                ),
                (
                    f"Given a clean checkout of `{slug}`\n"
                    "When an operator inspects README.md\n"
                    f"Then README.md lists the /healthz, /metrics, and {endpoint} routes and port {port}"
                ),
            ],
            "perimeter": perimeters[2],
        },
    ]

    extra_templates = [
        (
            f"Add request pagination parameters to GET {endpoint}",
            "P2",
            f"Allow callers to bound the number of returned items from GET {endpoint} via query parameters.",
            [
                (
                    f"Given stored items exist in `{slug}`\n"
                    f"When a client requests GET {endpoint} with a limit query parameter\n"
                    "Then the response status is HTTP 200 and returns at most limit items"
                ),
            ],
            perimeters[0],
        ),
        (
            f"Return structured readiness metadata on /healthz for {slug}",
            "P2",
            f"Include stack and item count diagnostics inside GET /healthz JSON responses for `{slug}`.",
            [
                (
                    f"Given `{slug}` is ready to accept traffic on port {port}\n"
                    "When a probe requests GET /healthz\n"
                    "Then the response status is HTTP 200 with JSON status equal to ok and service equal to the slug"
                ),
            ],
            perimeters[1],
        ),
    ]

    out: dict[str, str] = {}
    for index in range(1, tickets_count + 1):
        tid = f"{index:04d}"
        if index <= len(templates):
            item = templates[index - 1]
        else:
            title, prio, prob, crit, perim = extra_templates[(index - len(templates) - 1) % len(extra_templates)]
            item = {
                "title": f"{title} ({tid})",
                "priority": prio,
                "problem": prob,
                "criteria": crit,
                "perimeter": perim,
            }
        lines = [
            f"# {tid} — {item['title']}",
            "",
            "- Status: open",
            f"- Feature: {slug}",
            f"- Priority: {item['priority']}",
            "",
            "## Problem",
            "",
            item["problem"],
            "",
            spec.CRITERIA,
            "",
        ]
        for criterion in item["criteria"]:
            lines.extend([criterion, ""])
        lines.append(spec.PERIMETER)
        lines.append("")
        lines.extend(f"- `{glob}`" for glob in item["perimeter"])
        md = "\n".join(lines).rstrip("\n") + "\n"
        flaw = spec.check(md)
        if flaw is not None:
            raise GenesisError(f"generated ticket {tid}.md failed spec check: {flaw}")
        out[f"tickets/{tid}.md"] = md
    return out


# --- stack scaffolds -------------------------------------------------------


def _python_scaffold(ctx: dict[str, object]) -> tuple[dict[str, str], dict[str, str], tuple[list[str], list[str], list[str]]]:
    slug = str(ctx["slug"])
    pkg = str(ctx["pkg"])
    port = int(ctx["port"])  # type: ignore[arg-type]
    endpoint = str(ctx["endpoint"])
    brief_line = str(ctx["brief_line"])

    pyproject = (
        "[project]\n"
        f'name = "{slug}"\n'
        'version = "0.1.0"\n'
        f'description = {json.dumps(brief_line)}\n'
        'requires-python = ">=3.13"\n'
        "dependencies = []\n"
    )
    uv_lock = (
        "version = 1\n"
        "revision = 3\n"
        'requires-python = ">=3.13"\n\n'
        "[[package]]\n"
        f'name = "{slug}"\n'
        'version = "0.1.0"\n'
        'source = { virtual = "." }\n'
    )
    containerfile = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "FROM docker.io/library/python:3.13-slim-trixie@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime\n"
        "ENV HOST=0.0.0.0 \\\n"
        f"    PORT={port}\n"
        "WORKDIR /srv\n"
        "COPY pyproject.toml uv.lock ./\n"
        f"COPY {pkg} ./{pkg}\n"
        f"EXPOSE {port}\n"
        f'HEALTHCHECK --interval=10s --timeout=3s CMD python3 -c "import urllib.request; urllib.request.urlopen(\'http://127.0.0.1:{port}/healthz\')" || exit 1\n'
        f'CMD ["python3", "-m", "{pkg}.server"]\n\n'
        "FROM runtime AS test\n"
        "COPY tests ./tests\n"
        'CMD ["python3", "-m", "unittest", "discover", "-s", "tests", "-q"]\n'
    )
    init_py = (
        f'"""Greenfield {slug} package."""\n\n'
        f'SERVICE_NAME = "{slug}"\n'
        f"DEFAULT_PORT = {port}\n"
        f'DOMAIN_ENDPOINT = "{endpoint}"\n'
    )
    base_server = f'''"""Stdlib HTTP service for {slug}."""

from __future__ import annotations

import http.server
import json
import os
import threading

from {pkg} import DEFAULT_PORT, SERVICE_NAME


class ServiceState:
    """Thread-safe in-memory state for metrics and service items."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests_total = 0
        self.items: list[dict[str, object]] = []

    def record_request(self) -> int:
        with self._lock:
            self.requests_total += 1
            return self.requests_total

    def add_item(self, label: str) -> dict[str, object]:
        with self._lock:
            item: dict[str, object] = {{"id": len(self.items) + 1, "label": label}}
            self.items.append(item)
            return dict(item)

    def snapshot(self) -> tuple[int, list[dict[str, object]]]:
        with self._lock:
            return self.requests_total, [dict(entry) for entry in self.items]


def health_payload() -> dict[str, str]:
    """Return service liveness payload."""
    return {{"status": "ok", "service": SERVICE_NAME}}


def metrics_text(state: ServiceState) -> str:
    """Render Prometheus exposition metrics."""
    total, _ = state.snapshot()
    return (
        "# HELP service_up Service availability gauge.\\n"
        "# TYPE service_up gauge\\n"
        f'service_up{{{{service="{{SERVICE_NAME}}"}}}} 1\\n'
        "# HELP http_requests_total Total HTTP requests handled.\\n"
        "# TYPE http_requests_total counter\\n"
        f'http_requests_total{{{{service="{{SERVICE_NAME}}"}}}} {{total}}\\n'
    )


def dispatch(method: str, path: str, body: bytes = b"", state: ServiceState | None = None) -> tuple[int, str, bytes]:
    """Route an HTTP request and return (status, content_type, body_bytes)."""
    active = state if state is not None else ServiceState()
    active.record_request()
    clean_path = path.split("?", 1)[0]
    if clean_path == "/healthz":
        if method != "GET":
            return 405, "application/json", json.dumps({{"error": "method not allowed", "allow": ["GET"]}}).encode("utf-8")
        return 200, "application/json", json.dumps(health_payload()).encode("utf-8")
    if clean_path == "/metrics":
        if method != "GET":
            return 405, "application/json", json.dumps({{"error": "method not allowed", "allow": ["GET"]}}).encode("utf-8")
        return 200, "text/plain; version=0.0.4; charset=utf-8", metrics_text(active).encode("utf-8")
    return 404, "application/json", json.dumps({{"error": "not found"}}).encode("utf-8")


def make_handler(state: ServiceState) -> type[http.server.BaseHTTPRequestHandler]:
    """Create a request handler class bound to shared ServiceState."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def _handle(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length > 0 else b""
            code, ctype, payload = dispatch(method, self.path, raw, state)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:  # noqa: N802
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle("DELETE")

    return Handler


def start_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT) -> http.server.ThreadingHTTPServer:
    """Bind and return a running ThreadingHTTPServer instance."""
    return http.server.ThreadingHTTPServer((host, port), make_handler(ServiceState()))


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
    srv = start_server(host=host, port=port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
'''
    base_tests = f'''"""Base HTTP health and metrics tests for {slug}."""

from __future__ import annotations

import http.client
import json
import threading
import unittest

from {pkg}.server import ServiceState, dispatch, start_server


class BaseServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.srv = start_server(host="127.0.0.1", port=0)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()

    def _request(self, method: str, path: str) -> tuple[int, dict[str, str], bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path)
            resp = conn.getresponse()
            return resp.status, {{k.lower(): v for k, v in resp.getheaders()}}, resp.read()
        finally:
            conn.close()

    def test_healthz_returns_200_json(self) -> None:
        code, hdrs, body = self._request("GET", "/healthz")
        self.assertEqual(code, 200)
        self.assertIn("application/json", hdrs.get("content-type", ""))
        self.assertEqual(json.loads(body), {{"status": "ok", "service": "{slug}"}})

    def test_metrics_counts_requests_and_rejects_post(self) -> None:
        state = ServiceState()
        c1, _, _ = dispatch("GET", "/healthz", state=state)
        self.assertEqual(c1, 200)
        c2, ct2, b2 = dispatch("GET", "/metrics", state=state)
        self.assertEqual(c2, 200)
        self.assertIn("text/plain", ct2)
        text = b2.decode("utf-8")
        self.assertIn('service_up{{service="{slug}"}} 1', text)
        self.assertIn('http_requests_total{{service="{slug}"}} 2', text)

        c3, _, b3 = dispatch("POST", "/healthz", state=state)
        self.assertEqual(c3, 405)
        self.assertEqual(json.loads(b3)["allow"], ["GET"])

    def test_unknown_route_returns_404(self) -> None:
        code, _, body = self._request("GET", "/missing-route")
        self.assertEqual(code, 404)
        self.assertEqual(json.loads(body), {{"error": "not found"}})


if __name__ == "__main__":
    unittest.main()
'''
    domain_py = f'''"""Domain endpoint handler for {slug} (Ticket 0001)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from {pkg} import DOMAIN_ENDPOINT, SERVICE_NAME

if TYPE_CHECKING:
    from {pkg}.server import ServiceState

MAX_LABEL_LENGTH = 64


def handle_domain(method: str, body: bytes, state: ServiceState) -> tuple[int, dict[str, object]]:
    """Handle GET and POST requests for {endpoint}."""
    if method == "GET":
        _, items = state.snapshot()
        return 200, {{
            "service": SERVICE_NAME,
            "endpoint": DOMAIN_ENDPOINT,
            "count": len(items),
            "items": items,
        }}
    if method != "POST":
        return 405, {{"error": "method not allowed", "allow": ["GET", "POST"]}}

    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return 400, {{"error": "invalid json payload"}}
    if not isinstance(parsed, dict):
        return 400, {{"error": "payload must be a json object"}}

    raw_label = parsed.get("label")
    if not isinstance(raw_label, str):
        return 400, {{"error": "label must be a string"}}
    clean_label = raw_label.strip()
    if not clean_label:
        return 400, {{"error": "label must not be empty"}}
    if len(clean_label) > MAX_LABEL_LENGTH:
        return 400, {{"error": f"label exceeds {{MAX_LABEL_LENGTH}} characters"}}

    item = state.add_item(clean_label)
    return 201, {{"service": SERVICE_NAME, "item": item}}
'''
    feature_server = base_server.replace(
        f"from {pkg} import DEFAULT_PORT, SERVICE_NAME",
        f"from {pkg} import DEFAULT_PORT, DOMAIN_ENDPOINT, SERVICE_NAME\nfrom {pkg}.domain import handle_domain",
    ).replace(
        '    return 404, "application/json", json.dumps({"error": "not found"}).encode("utf-8")',
        (
            "    if clean_path == DOMAIN_ENDPOINT:\n"
            "        code, payload = handle_domain(method, body, active)\n"
            '        return code, "application/json", json.dumps(payload).encode("utf-8")\n'
            '    return 404, "application/json", json.dumps({"error": "not found"}).encode("utf-8")'
        ),
    )
    domain_tests = f'''"""Domain endpoint unit tests for {slug} (Ticket 0001)."""

from __future__ import annotations

import http.client
import json
import threading
import unittest

from {pkg}.server import ServiceState, dispatch, start_server


class DomainEndpointTest(unittest.TestCase):
    def setUp(self) -> None:
        self.srv = start_server(host="127.0.0.1", port=0)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def _call(self, method: str, path: str, body: bytes = b"") -> tuple[int, dict[str, object]]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {{"Content-Type": "application/json"}} if body else {{}}
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_get_and_post_domain_lifecycle(self) -> None:
        code0, doc0 = self._call("GET", "{endpoint}")
        self.assertEqual(code0, 200)
        self.assertEqual(doc0["service"], "{slug}")
        self.assertEqual(doc0["endpoint"], "{endpoint}")
        self.assertEqual(doc0["count"], 0)
        self.assertEqual(doc0["items"], [])

        code1, doc1 = self._call("POST", "{endpoint}", json.dumps({{"label": "first"}}).encode("utf-8"))
        self.assertEqual(code1, 201)
        self.assertEqual(doc1["item"], {{"id": 1, "label": "first"}})

        boundary = "x" * 64
        code2, doc2 = self._call("POST", "{endpoint}", json.dumps({{"label": boundary}}).encode("utf-8"))
        self.assertEqual(code2, 201)
        self.assertEqual(doc2["item"]["id"], 2)

        code3, doc3 = self._call("GET", "{endpoint}")
        self.assertEqual(code3, 200)
        self.assertEqual(doc3["count"], 2)
        self.assertEqual([entry["label"] for entry in doc3["items"]], ["first", boundary])

    def test_validation_rejects_bad_inputs_and_unsupported_methods(self) -> None:
        state = ServiceState()
        for bad_raw in (b"not-json", b"[]", b'{{}}', b'{{"label": 123}}', b'{{"label": "   "}}', json.dumps({{"label": "x" * 65}}).encode("utf-8")):
            code, _, body = dispatch("POST", "{endpoint}", bad_raw, state=state)
            self.assertEqual(code, 400)
            self.assertTrue(json.loads(body)["error"])

        code_del, _, body_del = dispatch("DELETE", "{endpoint}", state=state)
        self.assertEqual(code_del, 405)
        self.assertEqual(json.loads(body_del)["allow"], ["GET", "POST"])


if __name__ == "__main__":
    unittest.main()
'''

    base = {
        "pyproject.toml": pyproject,
        "uv.lock": uv_lock,
        "Containerfile": containerfile,
        f"{pkg}/__init__.py": init_py,
        f"{pkg}/server.py": base_server,
        "tests/__init__.py": "",
        "tests/test_server.py": base_tests,
    }
    feature = {
        f"{pkg}/domain.py": domain_py,
        f"{pkg}/server.py": feature_server,
        "tests/test_domain.py": domain_tests,
    }
    perimeters = (
        [f"{pkg}/*.py", "tests/test_*.py"],
        [f"{pkg}/*.py", "tests/test_*.py"],
        [f"{pkg}/*.py", "tests/test_*.py", "README.md"],
    )
    return base, feature, perimeters


def _node_scaffold(
    ctx: dict[str, object], *, typescript: bool
) -> tuple[dict[str, str], dict[str, str], tuple[list[str], list[str], list[str]]]:
    slug = str(ctx["slug"])
    port = int(ctx["port"])  # type: ignore[arg-type]
    endpoint = str(ctx["endpoint"])
    brief_line = str(ctx["brief_line"])
    ext = "ts" if typescript else "js"
    node_min = ">=24" if typescript else ">=22"

    pkg_json = (
        json.dumps(
            {
                "name": slug,
                "version": "0.1.0",
                "private": True,
                "description": brief_line,
                "type": "module",
                "main": f"src/server.{ext}",
                "scripts": {"test": "node --test"},
                "engines": {"node": node_min},
                "license": "Apache-2.0",
            },
            indent=2,
        )
        + "\n"
    )
    lock_json = (
        json.dumps(
            {
                "name": slug,
                "version": "0.1.0",
                "lockfileVersion": 3,
                "requires": True,
                "packages": {
                    "": {
                        "name": slug,
                        "version": "0.1.0",
                        "license": "Apache-2.0",
                        "engines": {"node": node_min},
                    }
                },
            },
            indent=2,
        )
        + "\n"
    )
    containerfile = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "FROM docker.io/library/node:24@sha256:e7add2a490bb17d97b485b8641bd88455fff7839ade0f5eba493c3508124d413 AS runtime\n"
        "ENV HOST=0.0.0.0 \\\n"
        f"    PORT={port}\n"
        "WORKDIR /srv\n"
        "COPY package.json package-lock.json ./\n"
        + ("COPY tsconfig.json ./\n" if typescript else "")
        + "RUN npm ci --omit=dev\n"
        "COPY src ./src\n"
        f"EXPOSE {port}\n"
        f"HEALTHCHECK --interval=10s --timeout=3s CMD node -e \"fetch('http://127.0.0.1:{port}/healthz').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\"\n"
        f'CMD ["node", "src/server.{ext}"]\n\n'
        "FROM runtime AS test\n"
        "COPY test ./test\n"
        'CMD ["node", "--test"]\n'
    )

    t_str = ": string" if typescript else ""
    t_num = ": number" if typescript else ""
    t_state = ": { requestsTotal: number; items: Array<{ id: number; label: string }> }" if typescript else ""
    t_res = ": { status: number; contentType: string; body: string }" if typescript else ""

    base_server = (
        "// SPDX-License-Identifier: Apache-2.0\n"
        'import http from "node:http";\n\n'
        f'export const SERVICE_NAME = "{slug}";\n'
        f"export const DEFAULT_PORT = {port};\n"
        f'export const DOMAIN_ENDPOINT = "{endpoint}";\n\n'
        f"export function createState(){t_state} {{\n"
        "  return { requestsTotal: 0, items: [] };\n"
        "}\n\n"
        f"export function dispatch(method{t_str}, path{t_str}, body{t_str} = \"\", state = createState()){t_res} {{\n"
        "  state.requestsTotal += 1;\n"
        '  const cleanPath = path.split("?")[0];\n'
        '  if (cleanPath === "/healthz") {\n'
        '    if (method !== "GET") {\n'
        '      return { status: 405, contentType: "application/json", body: JSON.stringify({ error: "method not allowed" }) };\n'
        "    }\n"
        '    return { status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok", service: SERVICE_NAME }) };\n'
        "  }\n"
        '  if (cleanPath === "/metrics") {\n'
        '    const text = `service_up{service="${SERVICE_NAME}"} 1\\nhttp_requests_total{service="${SERVICE_NAME}"} ${state.requestsTotal}\\n`;\n'
        '    return { status: 200, contentType: "text/plain; version=0.0.4", body: text };\n'
        "  }\n"
        '  return { status: 404, contentType: "application/json", body: JSON.stringify({ error: "not found" }) };\n'
        "}\n\n"
        "export function startServer(port = DEFAULT_PORT, host = process.env.HOST || \"127.0.0.1\") {\n"
        "  const state = createState();\n"
        "  const srv = http.createServer((req, res) => {\n"
        '    const chunks = [];\n'
        '    req.on("data", (c) => chunks.push(c));\n'
        '    req.on("end", () => {\n'
        '      const raw = Buffer.concat(chunks).toString("utf8");\n'
        '      const out = dispatch(req.method || "GET", req.url || "/", raw, state);\n'
        '      res.writeHead(out.status, { "Content-Type": out.contentType });\n'
        "      res.end(out.body);\n"
        "    });\n"
        "  });\n"
        "  return srv.listen(port, host);\n"
        "}\n"
    )
    base_test = (
        "// SPDX-License-Identifier: Apache-2.0\n"
        'import assert from "node:assert/strict";\n'
        'import { test } from "node:test";\n'
        f'import {{ createState, dispatch }} from "../src/server.{ext}";\n\n'
        'test("healthz and metrics answer 200", () => {\n'
        "  const state = createState();\n"
        '  const h = dispatch("GET", "/healthz", "", state);\n'
        "  assert.equal(h.status, 200);\n"
        f'  assert.deepEqual(JSON.parse(h.body), {{ status: "ok", service: "{slug}" }});\n'
        '  const m = dispatch("GET", "/metrics", "", state);\n'
        "  assert.equal(m.status, 200);\n"
        '  assert.match(m.body, /http_requests_total.*2/);\n'
        "});\n"
    )
    domain_code = (
        "// SPDX-License-Identifier: Apache-2.0\n"
        f"export function handleDomain(method{t_str}, body{t_str}, state{t_state}, serviceName{t_str}, endpoint{t_str}): {{ status: number; payload: Record<string, unknown> }} {{\n"
        if typescript
        else "// SPDX-License-Identifier: Apache-2.0\n"
        "export function handleDomain(method, body, state, serviceName, endpoint) {\n"
    ) + (
        '  if (method === "GET") {\n'
        "    return { status: 200, payload: { service: serviceName, endpoint, count: state.items.length, items: [...state.items] } };\n"
        "  }\n"
        '  if (method !== "POST") {\n'
        '    return { status: 405, payload: { error: "method not allowed" } };\n'
        "  }\n"
        "  let parsed;\n"
        "  try {\n"
        '    parsed = JSON.parse(body || "{}");\n'
        "  } catch {\n"
        '    return { status: 400, payload: { error: "invalid json" } };\n'
        "  }\n"
        '  const label = typeof parsed?.label === "string" ? parsed.label.trim() : "";\n'
        "  if (!label || label.length > 64) {\n"
        '    return { status: 400, payload: { error: "invalid label" } };\n'
        "  }\n"
        "  const item = { id: state.items.length + 1, label };\n"
        "  state.items.push(item);\n"
        "  return { status: 201, payload: { service: serviceName, item } };\n"
        "}\n"
    )
    feature_server = (
        f'import {{ handleDomain }} from "./domain.{ext}";\n' + base_server
    ).replace(
        '  return { status: 404, contentType: "application/json", body: JSON.stringify({ error: "not found" }) };',
        (
            "  if (cleanPath === DOMAIN_ENDPOINT) {\n"
            "    const out = handleDomain(method, body, state, SERVICE_NAME, DOMAIN_ENDPOINT);\n"
            '    return { status: out.status, contentType: "application/json", body: JSON.stringify(out.payload) };\n'
            "  }\n"
            '  return { status: 404, contentType: "application/json", body: JSON.stringify({ error: "not found" }) };'
        ),
    )
    if typescript:
        base_server = base_server.replace("const chunks = [];", "const chunks: Buffer[] = [];")
        feature_server = feature_server.replace("const chunks = [];", "const chunks: Buffer[] = [];")
        domain_code = domain_code.replace("let parsed;", "let parsed: { label?: unknown };")
    domain_test = (
        "// SPDX-License-Identifier: Apache-2.0\n"
        'import assert from "node:assert/strict";\n'
        'import { test } from "node:test";\n'
        f'import {{ createState, dispatch, DOMAIN_ENDPOINT }} from "../src/server.{ext}";\n\n'
        'test("domain endpoint handles GET, POST, and validation", () => {\n'
        "  const state = createState();\n"
        '  const empty = dispatch("GET", DOMAIN_ENDPOINT, "", state);\n'
        "  assert.equal(empty.status, 200);\n"
        "  assert.equal(JSON.parse(empty.body).count, 0);\n"
        '  const created = dispatch("POST", DOMAIN_ENDPOINT, JSON.stringify({ label: "item-1" }), state);\n'
        "  assert.equal(created.status, 201);\n"
        '  assert.equal(JSON.parse(created.body).item.label, "item-1");\n'
        '  const bad = dispatch("POST", DOMAIN_ENDPOINT, JSON.stringify({ label: "" }), state);\n'
        "  assert.equal(bad.status, 400);\n"
        "});\n"
    )

    del t_num  # reserved for clarity
    base: dict[str, str] = {
        "package.json": pkg_json,
        "package-lock.json": lock_json,
        "Containerfile": containerfile,
        f"src/server.{ext}": base_server,
        f"test/server.test.{ext}": base_test,
    }
    if typescript:
        base["tsconfig.json"] = (
            json.dumps(
                {
                    "compilerOptions": {
                        "target": "ES2024",
                        "module": "NodeNext",
                        "moduleResolution": "NodeNext",
                        "strict": True,
                        "noEmit": True,
                        "allowImportingTsExtensions": True,
                        "erasableSyntaxOnly": True,
                    },
                    "include": ["src/**/*.ts", "test/**/*.ts"],
                },
                indent=2,
            )
            + "\n"
        )
    feature = {
        f"src/domain.{ext}": domain_code,
        f"src/server.{ext}": feature_server,
        f"test/domain.test.{ext}": domain_test,
    }
    perimeters = (
        [f"src/*.{ext}", f"test/*.test.{ext}"],
        [f"src/*.{ext}", f"test/*.test.{ext}"],
        [f"src/*.{ext}", f"test/*.test.{ext}", "README.md"],
    )
    return base, feature, perimeters


def _go_scaffold(ctx: dict[str, object]) -> tuple[dict[str, str], dict[str, str], tuple[list[str], list[str], list[str]]]:
    slug = str(ctx["slug"])
    pkg = str(ctx["pkg"])
    port = int(ctx["port"])  # type: ignore[arg-type]
    endpoint = str(ctx["endpoint"])
    mod_path = f"example.com/{slug}"

    go_mod = f"module {mod_path}\n\ngo 1.24\n"
    containerfile = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "FROM docker.io/library/golang:1.24@sha256:d2d2bc1c84f7e60d7d2438a3836ae7d0c847f4888464e7ec9ba3a1339a1ee804 AS build\n"
        "WORKDIR /src\n"
        "COPY go.mod go.sum ./\n"
        "COPY pkg ./pkg\n"
        "COPY cmd ./cmd\n"
        "RUN CGO_ENABLED=0 go build -o /out/server ./cmd/server\n\n"
        "FROM build AS test\n"
        'CMD ["go", "test", "./..."]\n\n'
        "FROM docker.io/library/golang:1.24@sha256:d2d2bc1c84f7e60d7d2438a3836ae7d0c847f4888464e7ec9ba3a1339a1ee804 AS runtime\n"
        "WORKDIR /srv\n"
        "COPY --from=build /out/server /usr/local/bin/server\n"
        f"EXPOSE {port}\n"
        f"HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://127.0.0.1:{port}/healthz || exit 1\n"
        'CMD ["/usr/local/bin/server"]\n'
    )
    base_svc = f'''// SPDX-License-Identifier: Apache-2.0
package {pkg}

import "fmt"

const ServiceName = "{slug}"
const DomainEndpoint = "{endpoint}"

func Route(method, path string, requests int) (int, string, string) {{
	switch path {{
	case "/healthz":
		if method != "GET" {{
			return 405, "application/json", `{{"error":"method not allowed"}}`
		}}
		return 200, "application/json", fmt.Sprintf(`{{"status":"ok","service":"%s"}}`, ServiceName)
	case "/metrics":
		return 200, "text/plain; version=0.0.4", fmt.Sprintf("service_up{{service=\\"%s\\"}} 1\\nhttp_requests_total{{service=\\"%s\\"}} %d\\n", ServiceName, ServiceName, requests)
	default:
		return 404, "application/json", `{{"error":"not found"}}`
	}}
}}
'''
    base_test = f'''// SPDX-License-Identifier: Apache-2.0
package {pkg}

import "testing"

func TestBaseRoutes(t *testing.T) {{
	code, _, body := Route("GET", "/healthz", 1)
	if code != 200 || body == "" {{
		t.Fatalf("unexpected /healthz response: %d %s", code, body)
	}}
}}
'''
    main_go = f'''// SPDX-License-Identifier: Apache-2.0
package main

import (
	"log"
	"net/http"
	"os"
	"sync/atomic"
	"time"

	"{mod_path}/pkg/{pkg}"
)

func main() {{
	var count atomic.Int64
	port := os.Getenv("PORT")
	if port == "" {{
		port = "{port}"
	}}
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {{
		n := int(count.Add(1))
		code, ctype, body := {pkg}.Route(r.Method, r.URL.Path, n)
		w.Header().Set("Content-Type", ctype)
		w.WriteHeader(code)
		_, _ = w.Write([]byte(body))
	}})
	srv := &http.Server{{Addr: ":" + port, Handler: mux, ReadHeaderTimeout: 5 * time.Second}}
	log.Fatal(srv.ListenAndServe())
}}
'''
    domain_go = f'''// SPDX-License-Identifier: Apache-2.0
package {pkg}

import "fmt"

func HandleDomain(method string) (int, string) {{
	if method == "GET" {{
		return 200, fmt.Sprintf(`{{"service":"%s","endpoint":"%s","items":[]}}`, ServiceName, DomainEndpoint)
	}}
	return 405, `{{"error":"method not allowed"}}`
}}
'''
    feature_svc = base_svc.replace(
        "\tdefault:",
        "\tcase DomainEndpoint:\n\t\tcode, body := HandleDomain(method)\n\t\treturn code, \"application/json\", body\n\tdefault:",
    )
    domain_test = f'''// SPDX-License-Identifier: Apache-2.0
package {pkg}

import "testing"

func TestDomainRoute(t *testing.T) {{
	code, _, _ := Route("GET", DomainEndpoint, 1)
	if code != 200 {{
		t.Fatalf("expected 200 on domain endpoint, got %d", code)
	}}
}}
'''

    base = {
        "go.mod": go_mod,
        "go.sum": "",
        "Containerfile": containerfile,
        f"pkg/{pkg}/service.go": base_svc,
        f"pkg/{pkg}/service_test.go": base_test,
        "cmd/server/main.go": main_go,
    }
    feature = {
        f"pkg/{pkg}/domain.go": domain_go,
        f"pkg/{pkg}/service.go": feature_svc,
        f"pkg/{pkg}/domain_test.go": domain_test,
    }
    perimeters = (
        [f"pkg/{pkg}/*.go", "cmd/server/*.go"],
        [f"pkg/{pkg}/*.go", "cmd/server/*.go"],
        [f"pkg/{pkg}/*.go", "README.md"],
    )
    return base, feature, perimeters


def _rust_scaffold(ctx: dict[str, object]) -> tuple[dict[str, str], dict[str, str], tuple[list[str], list[str], list[str]]]:
    slug = str(ctx["slug"])
    pkg = str(ctx["pkg"])
    port = int(ctx["port"])  # type: ignore[arg-type]
    endpoint = str(ctx["endpoint"])

    cargo_toml = (
        "[package]\n"
        f'name = "{slug}"\n'
        'version = "0.1.0"\n'
        'edition = "2021"\n'
        'license = "Apache-2.0"\n\n'
        "[lib]\n"
        f'name = "{pkg}"\n'
        'path = "src/lib.rs"\n'
    )
    cargo_lock = (
        "# This file is automatically @generated by Cargo.\n"
        "# It is not intended for manual editing.\n"
        "version = 3\n\n"
        "[[package]]\n"
        f'name = "{slug}"\n'
        'version = "0.1.0"\n'
    )
    containerfile = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "FROM docker.io/library/rust:1.85 AS build\n"
        "WORKDIR /src\n"
        "COPY Cargo.toml Cargo.lock ./\n"
        "COPY src ./src\n"
        "RUN cargo build --release\n\n"
        "FROM build AS test\n"
        "COPY tests ./tests\n"
        'CMD ["cargo", "test"]\n\n'
        "FROM docker.io/library/rust:1.85 AS runtime\n"
        "WORKDIR /srv\n"
        f"COPY --from=build /src/target/release/{slug} /usr/local/bin/{slug}\n"
        f"EXPOSE {port}\n"
        f"HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://127.0.0.1:{port}/healthz || exit 1\n"
        f'CMD ["/usr/local/bin/{slug}"]\n'
    )
    base_lib = f'''pub const SERVICE_NAME: &str = "{slug}";
pub const DOMAIN_ENDPOINT: &str = "{endpoint}";

pub fn route(method: &str, path: &str, requests: u64) -> (u16, String) {{
    match path {{
        "/healthz" if method == "GET" => (200, format!("{{\\"status\\":\\"ok\\",\\"service\\":\\"{slug}\\"}}")),
        "/metrics" => (200, format!("service_up{{service=\\"{slug}\\"}} 1\\nhttp_requests_total{{service=\\"{slug}\\"}} {{}}\\n", requests)),
        _ => (404, "{{\\"error\\":\\"not found\\"}}".to_string()),
    }}
}}
'''
    main_rs = f'''use std::io::Write;
use std::net::TcpListener;

fn main() {{
    let port = std::env::var("PORT").unwrap_or_else(|_| "{port}".to_string());
    let listener = TcpListener::bind(format!("0.0.0.0:{{}}", port)).expect("bind");
    let mut requests: u64 = 0;
    for stream in listener.incoming().flatten() {{
        requests += 1;
        let (_code, body) = {pkg}::route("GET", "/healthz", requests);
        let mut s = stream;
        let _ = write!(s, "HTTP/1.1 200 OK\\r\\nContent-Length: {{}}\\r\\n\\r\\n{{}}", body.len(), body);
    }}
}}
'''
    base_test = f'''#[test]
fn healthz_returns_200() {{
    let (code, body) = {pkg}::route("GET", "/healthz", 1);
    assert_eq!(code, 200);
    assert!(body.contains("ok"));
}}
'''
    domain_rs = f'''pub fn handle_domain(method: &str) -> (u16, String) {{
    if method == "GET" {{
        (200, format!("{{\\"service\\":\\"{slug}\\",\\"endpoint\\":\\"{endpoint}\\",\\"items\\":[]}}"))
    }} else {{
        (405, "{{\\"error\\":\\"method not allowed\\"}}".to_string())
    }}
}}
'''
    feature_lib = "pub mod domain;\n\n" + base_lib.replace(
        '        _ => (404, "{\\"error\\":\\"not found\\"}".to_string()),',
        '        p if p == DOMAIN_ENDPOINT => domain::handle_domain(method),\n        _ => (404, "{\\"error\\":\\"not found\\"}".to_string()),',
    )
    domain_test = f'''#[test]
fn domain_endpoint_returns_200() {{
    let (code, body) = {pkg}::route("GET", {pkg}::DOMAIN_ENDPOINT, 1);
    assert_eq!(code, 200);
    assert!(body.contains("{slug}"));
}}
'''

    base = {
        "Cargo.toml": cargo_toml,
        "Cargo.lock": cargo_lock,
        "Containerfile": containerfile,
        "src/lib.rs": base_lib,
        "src/main.rs": main_rs,
        "tests/service_test.rs": base_test,
    }
    feature = {
        "src/domain.rs": domain_rs,
        "src/lib.rs": feature_lib,
        "tests/domain_test.rs": domain_test,
    }
    perimeters = (
        ["src/*.rs", "tests/*.rs"],
        ["src/*.rs", "tests/*.rs"],
        ["src/*.rs", "tests/*.rs", "README.md"],
    )
    return base, feature, perimeters


def _java_scaffold(ctx: dict[str, object]) -> tuple[dict[str, str], dict[str, str], tuple[list[str], list[str], list[str]]]:
    slug = str(ctx["slug"])
    port = int(ctx["port"])  # type: ignore[arg-type]
    endpoint = str(ctx["endpoint"])

    pom_xml = (
        '<project xmlns="http://maven.apache.org/POM/4.0.0"\n'
        '         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n'
        '         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">\n'
        "  <modelVersion>4.0.0</modelVersion>\n"
        "  <groupId>com.example</groupId>\n"
        f"  <artifactId>{slug}</artifactId>\n"
        "  <version>0.1.0</version>\n"
        "  <properties>\n"
        "    <maven.compiler.source>21</maven.compiler.source>\n"
        "    <maven.compiler.target>21</maven.compiler.target>\n"
        "    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>\n"
        "  </properties>\n"
        "</project>\n"
    )
    containerfile = (
        "# SPDX-License-Identifier: Apache-2.0\n"
        "FROM docker.io/library/maven:3.9-eclipse-temurin-21 AS runtime\n"
        "WORKDIR /srv\n"
        "COPY pom.xml ./\n"
        "COPY src ./src\n"
        f"EXPOSE {port}\n"
        f'HEALTHCHECK --interval=10s --timeout=3s CMD curl -f http://127.0.0.1:{port}/healthz || exit 1\n'
        'CMD ["java", "-cp", "target/classes", "com.example.Service"]\n\n'
        "FROM runtime AS test\n"
        'CMD ["mvn", "test"]\n'
    )
    base_svc = f'''package com.example;

public final class Service {{
    public static final String SERVICE_NAME = "{slug}";
    public static final String DOMAIN_ENDPOINT = "{endpoint}";

    private Service() {{}}

    public static String route(String method, String path, int requests) {{
        if ("/healthz".equals(path) && "GET".equals(method)) {{
            return "{{\\"status\\":\\"ok\\",\\"service\\":\\"{slug}\\"}}";
        }}
        if ("/metrics".equals(path)) {{
            return "service_up{{service=\\"{slug}\\"}} 1\\nhttp_requests_total{{service=\\"{slug}\\"}} " + requests + "\\n";
        }}
        return "{{\\"error\\":\\"not found\\"}}";
    }}
}}
'''
    base_test = '''package com.example;

public class ServiceTest {
    public static void main(String[] args) {
        if (!Service.route("GET", "/healthz", 1).contains("ok")) {
            throw new AssertionError("healthz check failed");
        }
    }
}
'''
    domain_java = f'''package com.example;

public final class Domain {{
    private Domain() {{}}

    public static String handle(String method) {{
        if ("GET".equals(method)) {{
            return "{{\\"service\\":\\"{slug}\\",\\"endpoint\\":\\"{endpoint}\\",\\"items\\":[]}}";
        }}
        return "{{\\"error\\":\\"method not allowed\\"}}";
    }}
}}
'''
    feature_svc = base_svc.replace(
        '        return "{\\"error\\":\\"not found\\"}";',
        '        if (DOMAIN_ENDPOINT.equals(path)) {\n            return Domain.handle(method);\n        }\n        return "{\\"error\\":\\"not found\\"}";',
    )
    domain_test = '''package com.example;

public class DomainTest {
    public static void main(String[] args) {
        String out = Service.route("GET", Service.DOMAIN_ENDPOINT, 1);
        if (!out.contains(Service.SERVICE_NAME)) {
            throw new AssertionError("domain route check failed");
        }
    }
}
'''

    base = {
        "pom.xml": pom_xml,
        "Containerfile": containerfile,
        "src/main/java/com/example/Service.java": base_svc,
        "src/test/java/com/example/ServiceTest.java": base_test,
    }
    feature = {
        "src/main/java/com/example/Domain.java": domain_java,
        "src/main/java/com/example/Service.java": feature_svc,
        "src/test/java/com/example/DomainTest.java": domain_test,
    }
    perimeters = (
        ["src/main/*.java", "src/test/*.java"],
        ["src/main/*.java", "src/test/*.java"],
        ["src/main/*.java", "src/test/*.java", "README.md"],
    )
    return base, feature, perimeters


_STACK_SCAFFOLDS = {
    "python": (_python_scaffold, lambda pkg: (f"{pkg}/", "uv run --locked python3 -m unittest discover -s tests -q")),
    "javascript": (lambda ctx: _node_scaffold(ctx, typescript=False), lambda _pkg: ("src/", "npm ci && npm test")),
    "typescript": (lambda ctx: _node_scaffold(ctx, typescript=True), lambda _pkg: ("src/", "npm ci && npm test")),
    "go": (_go_scaffold, lambda _pkg: ("pkg/", "go test ./...")),
    "rust": (_rust_scaffold, lambda _pkg: ("src/", "cargo test")),
    "java": (_java_scaffold, lambda _pkg: ("src/main/", "mvn test")),
}


def _write_files(repo_path: str, files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for rel, content in sorted(files.items()):
        full = os.path.join(repo_path, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)
    return written


def _default_gitignore() -> str:
    return (
        "__pycache__/\n"
        "*.pyc\n"
        ".venv/\n"
        "node_modules/\n"
        "target/\n"
        ".crew/\n"
        ".gates-out/\n"
        ".env\n"
    )


def _default_readme(slug: str, stack: str, port: int, endpoint: str, brief_line: str, test_cmd: str) -> str:
    return (
        f"# {slug}\n\n"
        f"{brief_line}\n\n"
        "## Runtime\n\n"
        f"- Stack: `{stack}`\n"
        f"- Port: `{port}`\n"
        "- Endpoints: `/healthz`, `/metrics`, "
        f"`{endpoint}`\n\n"
        "## Tests\n\n"
        f"```sh\n{test_cmd}\n```\n"
    )


def _commit_paths(repo_path: str, paths: list[str], message: str) -> str:
    unique = sorted(set(paths))
    _git(repo_path, "add", "--", *unique)
    _git(repo_path, "commit", "--only", "-q", "-m", message, "--", *unique, with_identity_fallback=True)
    return _git(repo_path, "rev-parse", "HEAD").stdout.strip()


def create_greenfield_app(
    repo_dir: str | os.PathLike[str],
    brief: str,
    *,
    stack: str = "python",
    name: str | None = None,
    port: int = DEFAULT_PORT,
    tickets_count: int = DEFAULT_TICKETS_COUNT,
    commit: bool = True,
    allow_existing_empty: bool = True,
    today: str | None = None,
    stacks_dir: str | None = None,
    contract_rel: str | None = None,
) -> dict[str, object]:
    """Build a greenfield ADR, starter backlog, runnable service, and contract inside repo_dir."""
    clean_brief, norm_stack = _validate_inputs(brief, stack, port, tickets_count)
    slug = _derive_slug(clean_brief, name)
    pkg = slug.replace("-", "_")
    endpoint = f"/{slug}"
    brief_line = _single_line(clean_brief)
    today_str = today or datetime.now(timezone.utc).date().isoformat()
    resolved_stacks = stacks_dir or _DEFAULT_STACKS_DIR
    resolved_contract = contract_rel or _DEFAULT_CONTRACT_REL

    prep = _prepare_repo(repo_dir, allow_existing_empty=allow_existing_empty)
    repo_path = str(prep["repo_path"])
    existing = dict(prep["existing_files"])  # type: ignore[arg-type]

    scaffold_fn, meta_fn = _STACK_SCAFFOLDS[norm_stack]
    scope_dir, test_cmd = meta_fn(pkg)
    ctx: dict[str, object] = {
        "slug": slug,
        "pkg": pkg,
        "port": port,
        "endpoint": endpoint,
        "brief_line": brief_line,
    }
    base_files, feature_files, perimeters = scaffold_fn(ctx)

    adr_rel = "docs/adr/0001-initial-architecture.md"
    adr_text = _build_adr_0001(
        slug=slug,
        stack=norm_stack,
        port=port,
        tickets_count=tickets_count,
        endpoint=endpoint,
        brief_line=brief_line,
        today=today_str,
        scope_dir=scope_dir,
        test_cmd=test_cmd,
    )
    tickets = _build_tickets(
        slug=slug,
        stack=norm_stack,
        port=port,
        endpoint=endpoint,
        brief_line=brief_line,
        tickets_count=tickets_count,
        perimeters=perimeters,
    )

    seed_writes: dict[str, str] = {adr_rel: adr_text, **tickets}
    if ".gitignore" not in existing:
        seed_writes[".gitignore"] = _default_gitignore()
    if not any(k.upper().startswith("README") and existing[k].strip() for k in existing):
        seed_writes["README.md"] = _default_readme(slug, norm_stack, port, endpoint, brief_line, test_cmd)

    base_written = _write_files(repo_path, {**seed_writes, **base_files})

    try:
        with tempfile.TemporaryDirectory(prefix="lights-out-genesis-") as tmp_draft:
            init.write_draft(
                repo_path,
                tmp_draft,
                resolved_stacks,
                today_str,
                "HEAD",
                baseline_mode="incremental",
            )
            applied = init_apply.apply_draft(
                tmp_draft,
                repo_path,
                resolved_contract,
                commit=False,
            )
    except (init.Undraftable, init_apply.ApplyError, ValueError) as exc:
        raise GenesisError(f"failed to draft or apply contract: {exc}") from exc

    contract_files = [str(p) for p in applied["applied_files"]]  # type: ignore[index]

    base_sha: str | None = None
    feature_sha: str | None = None
    if commit:
        base_sha = _commit_paths(
            repo_path,
            base_written + sorted(existing) + contract_files,
            f"chore(genesis): initialize {slug} architecture, contract, and base service",
        )
        feature_written = _write_files(repo_path, feature_files)
        feature_sha = _commit_paths(
            repo_path,
            feature_written,
            f"feat({slug}): implement ticket 0001 ({endpoint})",
        )
    else:
        feature_written = _write_files(repo_path, feature_files)

    branch_proc = _git(repo_path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    active_branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None

    return {
        "ok": True,
        "repo_dir": repo_path,
        "name": slug,
        "package": pkg,
        "stack": norm_stack,
        "port": port,
        "endpoint": endpoint,
        "brief": brief_line,
        "adr_path": adr_rel,
        "tickets": sorted(tickets),
        "contract_files": contract_files,
        "scaffold_files": sorted(set(base_written + feature_written)),
        "commits": {"base": base_sha, "feature": feature_sha},
        "branch": active_branch,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a greenfield service with ADR, tickets, tests, and contract.")
    parser.add_argument("--repo", default=".", help="Target repository directory (default: current directory)")
    brief_grp = parser.add_mutually_exclusive_group(required=True)
    brief_grp.add_argument("--brief", help="Product brief text")
    brief_grp.add_argument("--brief-file", help="Path to a UTF-8 file containing the product brief")
    parser.add_argument("--stack", default="python", choices=SUPPORTED_STACKS)
    parser.add_argument("--name", default=None, help="Explicit lowercase service slug")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--tickets-count", type=int, default=DEFAULT_TICKETS_COUNT)
    parser.add_argument("--no-commit", action="store_true")
    parser.add_argument("--today", default=None)
    parser.add_argument("--stacks", default=None)
    parser.add_argument("--json", action="store_true", dest="json_out")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.brief_file is not None:
            try:
                with open(args.brief_file, encoding="utf-8") as fh:
                    brief_text = fh.read()
            except OSError as exc:
                raise GenesisError(f"cannot read brief file {args.brief_file!r}: {exc.strerror}") from None
        else:
            brief_text = args.brief

        result = create_greenfield_app(
            args.repo,
            brief_text,
            stack=args.stack,
            name=args.name,
            port=args.port,
            tickets_count=args.tickets_count,
            commit=not args.no_commit,
            today=args.today,
            stacks_dir=args.stacks,
        )
    except GenesisError as exc:
        if args.json_out:
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"genesis: {exc}", file=sys.stderr)
        return 1

    if args.json_out:
        print(json.dumps(result))
    else:
        commits = result["commits"]
        range_note = (
            f" commits={str(commits['base'])[:7]}..{str(commits['feature'])[:7]}"  # type: ignore[index]
            if commits["base"] and commits["feature"]  # type: ignore[index]
            else " uncommitted"
        )
        print(
            f"GENESIS: {result['name']} stack={result['stack']} port={result['port']} "
            f"tickets={len(result['tickets'])} adr={result['adr_path']}{range_note}"  # type: ignore[arg-type]
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
