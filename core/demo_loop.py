# SPDX-License-Identifier: Apache-2.0
"""Closed deterministic L0 self-healing demo loop (docs/adr/0132): stdlib only.

Exercises the complete closed loop inside an isolated temporary sandbox + state
directory without external API keys or modifying the host checkout:
  1) Initial held canary: real minimal Python service + test suite + contract
     inside a bare+working git repo using local file-backed adapters, staging
     v0.2.0-rc1 to 25% held.
  2) Alert -> Triage -> Policy Rollback: probes the injected 5xx regression,
     posts a canary alert JSON through core/webhook.py, evaluates recorded L0
     ops-triage verdict, executes triage.mitigate(..., AUTO_MITIGATE_ON_TRIAGE=on)
     rolling back traffic to 0%, and runs ops_loop.on_rollback() with redacted
     tail logs + exemplars.
  3) Post-mortem -> Action Ticket + Derived Perimeter: runs core/postmortem.sh
     and ops_loop.derive_action_perimeters(), creating fix ticket 0002 with a
     derived ## Perimeter passing gates/spec.sh.
  4) Fix -> 8/8 PR Gates -> Merge & Mainline: opens PR #2 fixing the regression
     and adding a regression test, runs real PR gates + adr-check + spec,
     checks mainline.rejudge, merges to main, and verifies mainline green.
  5) Fix Canary -> Human GO -> 100% + Provenance & Frozen Console: stages
     v0.2.1 (5% -> 25% GO), promotes with GO_FOR_LAUNCH=GO to 100%, writes
     and verifies SLSA/in-toto provenance, builds logbook, and exports summary
     + frozen snapshot HTML (core/freeze.py) to DEMO_LOOP_OUT.

Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess  # nosec B404 - fixed scripts and git/cosign with explicit argv lists
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

try:
    from . import adr_gov, freeze, ops_loop, webhook
except ImportError:  # run as a script: core/ is sys.path[0]
    import adr_gov
    import freeze
    import ops_loop
    import webhook

# Provenance: docs/adr/0132, "Provenance" (cold L0 fast-feedback ceiling under 900s demo).
DEMO_LOOP_BUDGET_SECONDS = 300
INJECT_STAGES = ("triage", "spec", "gates", "canary")
CONTRACT_REL = os.environ.get("WORKLOAD_CONTRACT") or ("." + "lights" + "-out")
ROOT = Path(__file__).resolve().parents[1]

_TRIAGE_TEMPLATE = "964175f8ab25d1b8b1058a0ee144167aa83f14f1c47a2c21acd176750265c8a9.json"
_POSTMORTEM_TEMPLATE = "e3b939ad61f9fccf16e91a5b11657951829ef9bdd2383424cbbe1ae1f2958c9f.json"

_SERVICE_BASELINE = '''"""Minimal HTTP checkout service for closed-loop L0 verification."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def quote_total(price: float, discount_pct: float = 0.0) -> float:
    """Compute discounted price safely."""
    if price < 0 or discount_pct < 0 or discount_pct > 100:
        raise ValueError("invalid price or discount")
    return round(price * (1.0 - discount_pct / 100.0), 2)


def handle_request(raw_path: str) -> tuple[int, dict]:
    """Route a minimal HTTP GET path to (status_code, json_body)."""
    parsed = urlparse(raw_path)
    params = parse_qs(parsed.query)
    if parsed.path == "/health":
        return 200, {"status": "ok"}
    if parsed.path == "/quote":
        price = float(params.get("price", ["100"])[0])
        discount = float(params.get("discount", ["0"])[0])
        return 200, {"total": quote_total(price, discount)}
    return 404, {"error": "not found"}


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                code, body = handle_request(self.path)
            except Exception as exc:  # pragma: no cover - surfaced as 500
                code, body = 500, {"error": str(exc)}
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def start_server() -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
'''

_SERVICE_BUGGY = '''"""Minimal HTTP checkout service for closed-loop L0 verification."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def quote_total(price: float, discount_pct: float = 0.0) -> float:
    """Compute discounted price safely."""
    if price < 0 or discount_pct < 0 or discount_pct > 100:
        raise ValueError("invalid price or discount")
    return round(price * (1.0 - discount_pct / 100.0), 2)


def checkout_total(items: int, promo: str = "NONE") -> float:
    """Apply promo code divisor to item basket total."""
    subtotal = float(items * 10)
    divisor_by_promo = {"NONE": 1, "SAVE10": 2, "VIP0": 0}
    divisor = divisor_by_promo.get(promo, 1)
    return round(subtotal / divisor, 2)


def handle_request(raw_path: str) -> tuple[int, dict]:
    """Route a minimal HTTP GET path to (status_code, json_body)."""
    parsed = urlparse(raw_path)
    params = parse_qs(parsed.query)
    if parsed.path == "/health":
        return 200, {"status": "ok"}
    if parsed.path == "/quote":
        price = float(params.get("price", ["100"])[0])
        discount = float(params.get("discount", ["0"])[0])
        return 200, {"total": quote_total(price, discount)}
    if parsed.path == "/checkout":
        items = int(params.get("items", ["1"])[0])
        promo = params.get("promo", ["NONE"])[0]
        return 200, {"ok": True, "total": checkout_total(items, promo)}
    return 404, {"error": "not found"}


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                code, body = handle_request(self.path)
            except Exception as exc:  # pragma: no cover - surfaced as 500
                code, body = 500, {"error": str(exc)}
            raw = json.dumps(body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def start_server() -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
'''

_SERVICE_FIXED = _SERVICE_BUGGY.replace(
    '    divisor_by_promo = {"NONE": 1, "SAVE10": 2, "VIP0": 0}\n'
    '    divisor = divisor_by_promo.get(promo, 1)\n'
    '    return round(subtotal / divisor, 2)',
    '    if promo == "VIP0":\n'
    '        return round(subtotal * 0.5, 2)\n'
    '    divisor_by_promo = {"NONE": 1, "SAVE10": 2}\n'
    '    divisor = divisor_by_promo.get(promo, 1)\n'
    '    return round(subtotal / divisor, 2)',
)

_TESTS_BASELINE = '''"""Unit tests for the minimal checkout service."""

import unittest

from svc import server


class ServiceTests(unittest.TestCase):
    def test_health_and_quote(self) -> None:
        code, body = server.handle_request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        qcode, qbody = server.handle_request("/quote?price=100&discount=10")
        self.assertEqual(qcode, 200)
        self.assertEqual(qbody["total"], 90.0)


if __name__ == "__main__":
    unittest.main()
'''

_TESTS_V020 = '''"""Unit tests for the minimal checkout service."""

import unittest

from svc import server


class ServiceTests(unittest.TestCase):
    def test_health_and_quote(self) -> None:
        code, body = server.handle_request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        qcode, qbody = server.handle_request("/quote?price=100&discount=10")
        self.assertEqual(qcode, 200)
        self.assertEqual(qbody["total"], 90.0)

    def test_checkout_save10(self) -> None:
        code, body = server.handle_request("/checkout?items=4&promo=SAVE10")
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 20.0)


if __name__ == "__main__":
    unittest.main()
'''

_TESTS_FIXED = '''"""Unit tests for the minimal checkout service."""

import unittest

from svc import server


class ServiceTests(unittest.TestCase):
    def test_health_and_quote(self) -> None:
        code, body = server.handle_request("/health")
        self.assertEqual(code, 200)
        self.assertEqual(body["status"], "ok")
        qcode, qbody = server.handle_request("/quote?price=100&discount=10")
        self.assertEqual(qcode, 200)
        self.assertEqual(qbody["total"], 90.0)

    def test_checkout_save10(self) -> None:
        code, body = server.handle_request("/checkout?items=4&promo=SAVE10")
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 20.0)

    def test_checkout_vip0_applies_half_discount(self) -> None:
        code, body = server.handle_request("/checkout?items=4&promo=VIP0")
        self.assertEqual(code, 200)
        self.assertEqual(body["total"], 20.0)


if __name__ == "__main__":
    unittest.main()
'''

_SAST_PY = '''"""Minimal stdlib AST SAST scanner conforming to gates/sast.sh contract."""

import ast
import json
import os
import sys

scope = sys.argv[1]
items = []
for root, dirs, files in os.walk(scope):
    dirs[:] = sorted(d for d in dirs if d != "tests" and d != "__pycache__")
    for fname in sorted(files):
        if not fname.endswith(".py") or fname.startswith("test_"):
            continue
        rel = os.path.join(root, fname).replace("\\\\", "/")
        with open(rel, encoding="utf-8") as fh:
            src = fh.read()
        lines = src.splitlines()
        tree = ast.parse(src, filename=rel)
        for node in ast.walk(tree):
            rule = None
            if isinstance(node, ast.Assert):
                rule = "B101"
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
                rule = "B102"
            if rule and 1 <= getattr(node, "lineno", 0) <= len(lines) and "# nosec" not in lines[node.lineno - 1]:
                items.append({"rule": rule, "file": rel, "line": node.lineno})

print(json.dumps({"findings": len(items), "items": items}))
sys.exit(1 if items else 0)
'''

_MUTATOR_PY = '''"""Minimal stdlib AST mutator conforming to gates/mutation.sh MUTATION_LINES contract."""

import json
import os
import shutil
import subprocess
import sys
import tempfile

scope, test_cmd = sys.argv[1], sys.argv[2]
raw_lines = (os.environ.get("MUTATION_LINES") or "").strip()
targets = {}
for chunk in raw_lines.split():
    if "=" not in chunk:
        continue
    path, ranges = chunk.split("=", 1)
    line_set = set()
    for part in ranges.split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            line_set.update(range(int(lo), int(hi) + 1))
        elif part:
            line_set.add(int(part))
    targets[path] = line_set

if not targets:
    print(json.dumps({"total": 0, "killed": 0, "survived": 0, "score": None}))
    sys.exit(0)

SWAPS = (
    (" * 0.5", " * 1.0"),
    (" / divisor", " * divisor"),
    ('"VIP0"', '"NONE"'),
    ("round(subtotal * 0.5, 2)", "0.0"),
)

mutants = []
for rel, line_set in sorted(targets.items()):
    if not os.path.isfile(rel):
        continue
    with open(rel, encoding="utf-8") as fh:
        lines = fh.readlines()
    for lineno in sorted(line_set):
        if 1 <= lineno <= len(lines):
            orig = lines[lineno - 1]
            for old, new in SWAPS:
                if old in orig:
                    mutants.append((rel, lineno, f"{old.strip()}->{new.strip()}", orig.replace(old, new, 1)))

if not mutants:
    print(json.dumps({"total": 0, "killed": 0, "survived": 0, "score": None}))
    sys.exit(0)

killed = 0
survived = 0
with tempfile.TemporaryDirectory() as tmp:
    wt = os.path.join(tmp, "wt")
    shutil.copytree(".", wt, ignore=shutil.ignore_patterns(".git", "__pycache__", ".crew"))
    for rel, lineno, op, mutated_line in mutants:
        target = os.path.join(wt, rel)
        with open(target, encoding="utf-8") as fh:
            original = fh.readlines()
        patched = list(original)
        patched[lineno - 1] = mutated_line
        with open(target, "w", encoding="utf-8") as fh:
            fh.writelines(patched)
        proc = subprocess.run(["sh", "-c", test_cmd], cwd=wt, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(target, "w", encoding="utf-8") as fh:
            fh.writelines(original)
        if proc.returncode != 0:
            killed += 1
        else:
            survived += 1
            print(f"SURVIVOR {rel}:{lineno} {op}", file=sys.stderr)

total = killed + survived
score = round(killed / total, 4) if total else None
print(json.dumps({"total": total, "killed": killed, "survived": survived, "score": score}))
'''

_METRICS_QUERY_SH = '''#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
query=${1:?usage: query.sh <promql>}
: "${FILE_METRICS_JSON:?FILE_METRICS_JSON not set}"
exec python3 - "$FILE_METRICS_JSON" "$query" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
q = sys.argv[2]
if "5.." in q:
    metric = "error_rate"
elif "histogram_quantile" in q:
    metric = "latency_p95"
else:
    metric = "traffic"
rows = [
    {"metric": {"service_version": ver}, "value": [0, str(vals[metric])]}
    for ver, vals in sorted(data.items())
    if vals.get(metric) is not None
]
print(json.dumps({"status": "success", "data": {"result": rows}}))
PY
'''


class LoopFailed(Exception):
    """Raised when a phase fails closed and must report NO-GO: demo-loop."""


def _say(msg: str) -> None:
    print(f"[demo-loop] {msg}", file=sys.stderr, flush=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_text(path: Path, text: str, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)
    return path


def _write_json(path: Path, obj: Any) -> Path:
    return _write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _run(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    check_msg: str | None = None,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # nosec B603 - explicit argv list, no shell
        argv,
        cwd=str(cwd) if cwd is not None else None,
        env=dict(os.environ) if env is None else dict(env),
        input=stdin_text,
        stdin=subprocess.DEVNULL if stdin_text is None else None,
        capture_output=True,
        text=True,
        check=False,
    )
    if check_msg and proc.returncode != 0:
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit {proc.returncode}"
        raise LoopFailed(f"{check_msg}: {tail}")
    return proc


def _git(repo: Path, *args: str, check_msg: str | None = None) -> str:
    proc = _run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=demo-loop",
            "-c",
            "user.email=demo-loop@localhost.invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check_msg=check_msg or f"git {' '.join(args)} failed",
    )
    return proc.stdout.strip()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _probe_http(paths: list[str]) -> list[tuple[str, int, float]]:
    """Run real HTTP GET requests against an ephemeral local svc/server.py instance."""
    records: list[tuple[str, int, float]] = []
    # Import svc.server dynamically from repo_dir in sys.path so each version is probed live.
    mod = sys.modules.get("svc.server")
    if mod is None:
        import importlib

        mod = importlib.import_module("svc.server")
    srv = mod.start_server()
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        for req_path in paths:
            t0 = time.monotonic()
            url = f"http://127.0.0.1:{port}{req_path}"
            try:
                with urllib.request.urlopen(url, timeout=5) as resp:  # nosec B310 - loopback test server
                    code = int(resp.status)
                    resp.read()
            except urllib.error.HTTPError as err:
                code = int(err.code)
            records.append((req_path, code, round(time.monotonic() - t0, 4)))
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=2)
    return records


def _metrics_from_probes(
    stable_ver: str,
    stable_probes: list[tuple[str, int, float]],
    canary_ver: str,
    canary_probes: list[tuple[str, int, float]],
) -> dict[str, dict[str, float]]:
    def summarize(probes: list[tuple[str, int, float]]) -> dict[str, float]:
        n = max(len(probes), 1)
        errs = sum(1 for _, code, _ in probes if code >= 500)
        lat = sorted(lat for _, _, lat in probes)
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.01
        return {
            "traffic": round(float(n), 3),
            "error_rate": round(errs / float(n), 3),
            "latency_p95": max(0.005, round(p95, 3)),
        }

    return {stable_ver: summarize(stable_probes), canary_ver: summarize(canary_probes)}


def _canonical_json(text: str) -> str:
    return json.dumps(json.loads(text), sort_keys=True, indent=2)


def _prompt_sha256(sandbox: Path) -> str:
    return hashlib.sha256((sandbox / "roles" / "ops-triage" / "prompt.md").read_bytes()).hexdigest()


def _write_adk_fixture(
    sandbox: Path,
    template_name: str,
    input_text: str,
    parts: list[dict[str, Any]],
    final_text: str,
) -> Path:
    """Derive a sandbox L0 ReplayLlm fixture from a committed ops-triage template."""
    tmpl = _load_json(ROOT / "fixtures" / "adk" / "ops-triage" / template_name)
    canon = _canonical_json(input_text)
    key = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    tmpl["input"] = canon
    tmpl["instruction_sha256"] = _prompt_sha256(sandbox)
    tmpl["responses"][0]["content"]["parts"] = parts
    if len(tmpl["responses"]) > 1:
        tmpl["responses"][1]["content"]["parts"] = [{"text": final_text}]
    dest = sandbox / "adk-fixtures" / "ops-triage" / f"{key}.json"
    return _write_json(dest, tmpl)


def _prepare_sandbox(sandbox: Path) -> dict[str, Path]:
    """Build isolated platform links, local file-backed adapters, and keys."""
    for name in ("core", "gates", "ci", "policy", "roles", "verify", "console", "fixtures"):
        os.symlink(ROOT / name, sandbox / name)

    adapters_dir = sandbox / "adapters"
    adapters_dir.mkdir(parents=True)
    for name in ("sqlite", "spool", "flags-file", "file", "adk", "replay"):
        os.symlink(ROOT / "adapters" / name, adapters_dir / name)

    outside = ROOT / "fixtures" / "outside-adapters"
    for name in ("file-delivery", "file-registry", "file-codehost"):
        shutil.copytree(outside / name, adapters_dir / name)

    _write_text(adapters_dir / "file-metrics" / "query.sh", _METRICS_QUERY_SH, executable=True)

    state_dir = sandbox / ".crew"
    state_dir.mkdir(parents=True)
    repo_dir = sandbox / "repo"
    repo_dir.mkdir(parents=True)
    flags_file = sandbox / "flags.json"
    _write_json(
        flags_file,
        {
            "flags": {
                "release-freeze": {
                    "state": "ENABLED",
                    "variants": {"on": True, "off": False},
                    "defaultVariant": "off",
                },
                "checkout-promo": {
                    "state": "ENABLED",
                    "variants": {"on": True, "off": False},
                    "defaultVariant": "on",
                },
            }
        },
    )
    metrics_file = sandbox / "metrics.json"
    _write_json(metrics_file, {})
    targets_file = sandbox / "targets.txt"
    _write_text(targets_file, "help\n")

    cosign_key = sandbox / "keys" / "k.key"
    if shutil.which("cosign"):
        cosign_key.parent.mkdir(parents=True, exist_ok=True)
        _run(
            ["cosign", "generate-key-pair", "--output-key-prefix", "k"],
            cwd=cosign_key.parent,
            env=dict(os.environ, COSIGN_PASSWORD=""),
            check_msg="cosign generate-key-pair failed",
        )

    return {
        "sandbox": sandbox,
        "state_dir": state_dir,
        "repo_dir": repo_dir,
        "flags_file": flags_file,
        "metrics_file": metrics_file,
        "targets_file": targets_file,
        "cosign_key": cosign_key,
        "adk_fixtures": sandbox / "adk-fixtures",
        "codehost_dir": sandbox / "codehost",
        "delivery_dir": sandbox / "delivery",
        "registry_dir": sandbox / "registry",
    }


def _build_contract(repo_dir: Path) -> Path:
    cdir = repo_dir / CONTRACT_REL
    cdir.mkdir(parents=True, exist_ok=True)
    _write_text(
        cdir / "workload.env",
        "\n".join(
            [
                "CANARY_VERSION=v0.2.0-rc1",
                "# Provenance, decision: demo service baseline matching ADR 0132",
                "MUTATION_BASELINE=0.80",
                "WORKLOAD_KILL_SWITCH=checkout-promo",
                "WORKLOAD_SAMPLE_PATH=/health",
                "WORKLOAD_LOCKFILES=uv.lock",
                "WORKLOAD_MUTATION_SCOPE=svc/",
                "WORKLOAD_TEST_ROOT=",
                "WORKLOAD_TEST_PATHS=tests/test_*.py",
                "TOOLCHAIN_IMAGE=workbench",
                "TEST_CMD=python3 -m unittest discover -s tests -t .",
                f"SAST_CMD=sh {CONTRACT_REL}/runners/sast.sh svc",
                f'MUTATION_CMD=sh {CONTRACT_REL}/runners/mutation.sh svc "python3 -m unittest discover -s tests -t ."',
                "",
            ]
        ),
    )
    _write_text(
        cdir / "policy.env",
        "\n".join(
            [
                "# Provenance, decision: ADR 0132 L0 demo medium line threshold",
                "RISK_SIZE_MEDIUM_LINES=200",
                "# Provenance, decision: ADR 0132 enforce ADR-0001 on high-risk PRs",
                "RISK_REQUIRE_ADR=high",
                "# Provenance, decision: ADR 0132 L0 error rate delta ceiling",
                "ERROR_RATE_MAX_DELTA=0.01",
                "# Provenance, decision: ADR 0132 L0 latency p95 delta ceiling",
                "LATENCY_P95_MAX_DELTA=0.25",
                "",
            ]
        ),
    )
    runners = cdir / "runners"
    _write_text(runners / "sast_check.py", _SAST_PY)
    _write_text(
        runners / "sast.sh",
        f'#!/bin/sh\nset -eu\nexec python3 {CONTRACT_REL}/runners/sast_check.py "${{1:?}}"\n',
        executable=True,
    )
    _write_text(runners / "mutator.py", _MUTATOR_PY)
    _write_text(
        runners / "mutation.sh",
        f'#!/bin/sh\nset -eu\nexec python3 {CONTRACT_REL}/runners/mutator.py "${{1:?}}" "${{2:?}}"\n',
        executable=True,
    )
    return cdir


def _build_adr_0001() -> str:
    return "\n".join(
        [
            "# ADR 0001 — Checkout Service Contract",
            "",
            "- Status: accepted",
            "- Date: 2026-11-25",
            "",
            "## Context",
            "",
            "The demo service needs an explicit baseline decision record for checkout behavior.",
            "",
            "## Decision",
            "",
            "Expose `/health`, `/quote`, and `/checkout` over stdlib HTTP handlers covered by unit tests.",
            "",
            "## Alternatives",
            "",
            "- Unstructured script without unit tests: rejected because gates require deterministic test proof.",
            "",
            "## Consequences",
            "",
            "- Every PR touching `svc/` must keep unit tests and SAST checks green.",
            "",
            "## Provenance",
            "",
            "This record introduces no numeric threshold.",
            "",
        ]
    )


def _base_env(paths: dict[str, Path]) -> dict[str, str]:
    sandbox = paths["sandbox"]
    state_dir = paths["state_dir"]
    repo_dir = paths["repo_dir"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "LEVEL": "l0",
            "AGENT_HARNESS": "replay",
            "ADK_FIXTURES": str(paths["adk_fixtures"]),
            "LIGHTS_OUT_ROOT": str(sandbox),
            "STATE_STORE": "sqlite",
            "STATE_FILE": str(state_dir / "state.sqlite"),
            "EVENT_BUS": "spool",
            "SPOOL_DIR": str(state_dir / "spool"),
            "TRACKER": "file",
            "TICKETS_DIR": str(repo_dir / "tickets"),
            "FLAGS": "flags-file",
            "FLAGS_FILE": str(paths["flags_file"]),
            "DELIVERY": "file-delivery",
            "FILE_DELIVERY_DIR": str(paths["delivery_dir"]),
            "FILE_DELIVERY_INITIAL_STABLE": "v0.1.0",
            "REGISTRY": "file-registry",
            "FILE_REGISTRY_DIR": str(paths["registry_dir"]),
            "FILE_REGISTRY_REPO": "local/demo-svc",
            "CODE_HOST": "file-codehost",
            "FILE_CODEHOST_DIR": str(paths["codehost_dir"]),
            "METRICS": "file-metrics",
            "FILE_METRICS_JSON": str(paths["metrics_file"]),
            "WORKLOAD": str(repo_dir),
            "WORKLOAD_CONTRACT": str(repo_dir / CONTRACT_REL),
            "WORKLOAD_ATTACHED": "1",
            "TOOLCHAIN_IMAGE": "workbench",
            "RELEASE_RECORD": str(state_dir / "release.json"),
            "RELEASES_LOG": str(state_dir / "releases.jsonl"),
            "TRIAGE_OUT": str(state_dir / "triage"),
            "INCIDENT_OUT": str(state_dir / "incidents"),
            "POSTMORTEM_OUT": str(state_dir / "postmortem"),
            "POSTMORTEM_DIR": str(state_dir / "postmortem"),
            "PROMOTE_OUT": str(state_dir / "promote"),
            "PROVENANCE_DIR": str(state_dir / "provenance"),
            "LOGBOOK_FILE": str(state_dir / "logbook.json"),
            "MAP_FILE": str(state_dir / "map.sqlite"),
            "MITIGATIONS_LOG": str(state_dir / "mitigations.jsonl"),
            "CREW_DIR": str(state_dir),
            "ROLES_DIR": str(sandbox / "roles"),
            "REPO_DIR": str(repo_dir),
            "COSIGN_KEY": str(paths["cosign_key"]),
            "STAGE_SECONDS": "0",
            "AWAIT_TIMEOUT": "2",
        }
    )
    return env


def _probe_repo_service(repo_dir: Path, req_paths: list[str]) -> list[tuple[str, int, float]]:
    """Import svc.server freshly from repo_dir and probe HTTP endpoints."""
    for mod_name in list(sys.modules):
        if mod_name == "svc" or mod_name.startswith("svc."):
            del sys.modules[mod_name]
    sys.path.insert(0, str(repo_dir))
    try:
        return _probe_http(req_paths)
    finally:
        if sys.path and sys.path[0] == str(repo_dir):
            sys.path.pop(0)


def _sqlite_put(sandbox: Path, env: Mapping[str, str], collection: str, doc_id: str, doc: Mapping[str, Any]) -> None:
    _run(
        ["sh", "adapters/sqlite/put.sh", collection, doc_id],
        cwd=sandbox,
        env=env,
        stdin_text=json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n",
        check_msg=f"sqlite put {collection}/{doc_id} failed",
    )


def _phase_initial_held_canary(paths: dict[str, Path], env: dict[str, str]) -> dict[str, Any]:
    """Phase 1: build minimal Python service repo, merge v0.2.0-rc1 PR, and stage canary at 25% held."""
    _say("phase 1: scaffolding minimal Python service, local adapters, and staging v0.2.0-rc1")
    sandbox = paths["sandbox"]
    repo_dir = paths["repo_dir"]

    _git(repo_dir, "init", "-q", "-b", "main")
    _write_text(repo_dir / ".gitignore", ".crew/\n__pycache__/\n*.pyc\n")
    _write_text(repo_dir / "uv.lock", "version = 1\n")
    adr1 = _build_adr_0001()
    if adr_gov.validate_adr(adr1):
        raise LoopFailed("generated ADR 0001 failed adr_gov validation")
    _write_text(repo_dir / "docs" / "adr" / "0001-service-contract.md", adr1)
    _write_text(repo_dir / "svc" / "__init__.py", "")
    _write_text(repo_dir / "svc" / "server.py", _SERVICE_BASELINE)
    _write_text(repo_dir / "tests" / "__init__.py", "")
    _write_text(repo_dir / "tests" / "test_server.py", _TESTS_BASELINE)
    _build_contract(repo_dir)

    ticket1_text = "\n".join(
        [
            "# 0001 — Add promo checkout endpoint",
            "",
            "- Status: open",
            "- Priority: P1",
            "",
            "## Problem",
            "",
            "Add `/checkout` promo discount endpoint in `svc/server.py`.",
            "",
            "## Acceptance criteria",
            "",
            "Given a checkout basket with promo SAVE10",
            "When GET /checkout?items=4&promo=SAVE10 is requested",
            "Then HTTP 200 returns total 20.0",
            "",
            "## Perimeter",
            "",
            "- `svc/server.py`",
            "- `tests/test_server.py`",
            "",
        ]
    )
    _write_text(repo_dir / "tickets" / "0001.md", ticket1_text)
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", "chore: initial baseline v0.1.0 (ADR-0001)")
    _git(repo_dir, "tag", "v0.1.0")
    v010_sha = _git(repo_dir, "rev-parse", "HEAD")

    # Initialize local adapters
    for adapter in ("sqlite", "spool", "flags-file", "file-delivery", "file-registry", "file-codehost"):
        _run(["sh", f"adapters/{adapter}/ready.sh"], cwd=sandbox, env=env, check_msg=f"{adapter} ready failed")
    _run(["sh", "adapters/file-codehost/bootstrap.sh"], cwd=sandbox, env=env, check_msg="codehost bootstrap failed")

    # Probe baseline v0.1.0 live
    stable_probes = _probe_repo_service(repo_dir, ["/health", "/quote?price=100&discount=10"])

    # Merge PR #1 (ticket/0001) introducing v0.2.0-rc1 with hidden VIP0 regression
    _git(repo_dir, "checkout", "-q", "-b", "ticket/0001")
    _write_text(repo_dir / "svc" / "server.py", _SERVICE_BUGGY)
    _write_text(repo_dir / "tests" / "test_server.py", _TESTS_V020)
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", "feat(0001): add promo checkout endpoint")
    pr1_head = _git(repo_dir, "rev-parse", "HEAD")

    _run(["sh", "adapters/file-codehost/push.sh", str(repo_dir), "ticket/0001"], cwd=sandbox, env=env, check_msg="push pr1 failed")
    pr1_json = sandbox / "pr1.json"
    _write_json(pr1_json, {"title": "Add promo checkout endpoint (0001)", "body": "Implements /checkout."})
    _run(
        ["sh", "adapters/file-codehost/open-pr.sh", "ticket/0001", str(pr1_json)],
        cwd=sandbox,
        env=env,
        check_msg="open-pr #1 failed",
    )
    merge1_out = _run(
        ["sh", "adapters/file-codehost/merge.sh", "1", pr1_head],
        cwd=sandbox,
        env=env,
        check_msg="merge pr #1 failed",
    ).stdout.strip()
    merge1_sha = merge1_out.split()[0]

    _git(repo_dir, "checkout", "-q", "main")
    clone_url = _run(["sh", "adapters/file-codehost/clone-url.sh"], cwd=sandbox, env=env, check_msg="clone-url failed").stdout.strip()
    _git(repo_dir, "pull", "-q", "--ff-only", clone_url, "main")

    # Exercise spool EventBus publish/pull/ack cleanly
    _run(
        ["sh", "adapters/spool/publish.sh", json.dumps({"ticket": "0001", "step": "release"})],
        cwd=sandbox,
        env=env,
        check_msg="spool publish failed",
    )
    pulled = _run(["sh", "adapters/spool/pull.sh"], cwd=sandbox, env=env, check_msg="spool pull failed").stdout.strip()
    if pulled:
        _run(["sh", "adapters/spool/ack.sh", pulled.split(" ", 1)[0]], cwd=sandbox, env=env, check_msg="spool ack failed")

    # Build initial project map and record ticket 0001 in sqlite
    _run(
        [
            sys.executable,
            "core/map.py",
            "build",
            "--repo",
            str(repo_dir),
            "--tickets",
            "tickets",
            "--targets",
            str(paths["targets_file"]),
            "--out",
            env["MAP_FILE"],
        ],
        cwd=sandbox,
        env=env,
        check_msg="initial map build failed",
    )

    canary_tag = f"sha-{merge1_sha}"
    image1 = _run(
        ["sh", "adapters/file-registry/publish.sh", canary_tag],
        cwd=sandbox,
        env=env,
        check_msg="publish v0.2.0-rc1 failed",
    ).stdout.strip()

    # Healthy smoke traffic for v0.2.0-rc1 staging (/health + /quote + /checkout?promo=SAVE10)
    rc1_probes = _probe_repo_service(repo_dir, ["/health", "/quote?price=100&discount=10", "/checkout?items=4&promo=SAVE10"])
    _write_json(paths["metrics_file"], _metrics_from_probes("v0.1.0", stable_probes, "v0.2.0-rc1", rc1_probes))

    t0 = _utc_now()
    _sqlite_put(
        sandbox,
        env,
        "tickets",
        "0001",
        {
            "id": "0001",
            "state": "held",
            "attempt": 1,
            "spec": {
                "sha256": hashlib.sha256(ticket1_text.encode("utf-8")).hexdigest(),
                "title": "Add promo checkout endpoint",
                "verdict": "GO",
                "line": "GO: spec",
            },
            "pr": {"number": 1, "url": f"file://{paths['codehost_dir']}/pulls/1", "base_sha": v010_sha, "head_sha": pr1_head},
            "merge": {"sha": merge1_sha, "head_sha": pr1_head},
            "mainline": {"verdict": "GO", "green": merge1_sha},
            "release": {"version": "v0.2.0-rc1", "image": image1, "verdict": "held"},
            "history": [
                {"step": "request", "state": "requested", "at": t0, "by": "demo-loop", "via": "cli"},
                {"step": "spec", "state": "opened", "at": t0},
                {"step": "assign", "state": "pr-opened", "at": t0, "attempt": 1},
                {"step": "ci", "state": "ci-passed", "at": t0, "attempt": 1},
                {"step": "merge", "state": "merged", "at": t0},
                {"step": "mainline", "state": "main-green", "at": t0},
                {"step": "release", "state": "held", "at": t0},
            ],
        },
    )
    _sqlite_put(sandbox, env, "mainline", "main", {"state": "green", "green": merge1_sha, "head": merge1_sha, "reverted": []})

    lp_env = dict(env, CANARY_VERSION="v0.2.0-rc1", CANARY_TAG=canary_tag, RELEASE_TICKET="0001")
    lp = _run(["sh", "core/launchpad.sh"], cwd=sandbox, env=lp_env, check_msg="initial launchpad v0.2.0-rc1 failed")

    rel = _load_json(Path(env["RELEASE_RECORD"]))
    status = json.loads(_run(["sh", "adapters/file-delivery/status.sh"], cwd=sandbox, env=env, check_msg="delivery status failed").stdout)
    if rel.get("verdict") != "held" or rel.get("held_at") != 25 or status.get("weight") != 25:
        raise LoopFailed(f"expected v0.2.0-rc1 held at 25%, got record={rel.get('verdict')} weight={status.get('weight')}")

    return {
        "verdict": "GO",
        "version": "v0.2.0-rc1",
        "stable": "v0.1.0",
        "image": image1,
        "merge_sha": merge1_sha,
        "base_sha": v010_sha,
        "held_at": 25,
        "launchpad_line": lp.stdout.strip(),
    }


def _phase_alert_triage_rollback(
    paths: dict[str, Path],
    env: dict[str, str],
    phase1: dict[str, Any],
    inject_break: str | None,
) -> dict[str, Any]:
    """Phase 2: trigger 5xx regression, POST alert to core/webhook.py, triage -> mitigate -> on_rollback."""
    _say("phase 2: probing 5xx regression -> core/webhook.py -> ops-triage -> policy rollback")
    sandbox = paths["sandbox"]
    repo_dir = paths["repo_dir"]

    # Real HTTP probe hitting the injected VIP0 zero-division bug on v0.2.0-rc1
    bug_probes = _probe_repo_service(repo_dir, ["/checkout?items=4&promo=VIP0", "/checkout?items=2&promo=VIP0"])
    if not all(code == 500 for _, code, _ in bug_probes):
        raise LoopFailed(f"expected real HTTP 500 on buggy VIP0 route, got {bug_probes}")

    # Write canary.log with real 500 probe + credential-shaped text to exercise redaction
    canary_log = paths["state_dir"] / "canary.log"
    secret_tok = "demo-canary-tok-999"
    secret_hdr = "demo-bearer-val-999"
    _write_text(
        canary_log,
        "\n".join(
            [
                f"[canary] GET /checkout?items=4&promo=VIP0 -> HTTP {bug_probes[0][1]} ZeroDivisionError",
                f"[canary] request Authorization: Bearer {secret_hdr}",
                f"[canary] SERVICE_TOKEN={secret_tok} slo: error_rate canary(v0.2.0-rc1)=1.000 stable(v0.1.0)=0.000",
                "",
            ]
        ),
    )

    # Emit alert JSON matching normalized core/webhook.py item shape
    alert_payload = {
        "headline": "canary v0.2.0-rc1: error_rate 100% on /checkout",
        "severity": "critical",
        "status": "firing",
        "version": "v0.2.0-rc1",
        "stable": "v0.1.0",
        "summary": "error_rate=1.000 on canary v0.2.0-rc1 at 25%",
        "labels": {"service": "demo-svc", "route": "/checkout"},
        "starts_at": "2026-11-25T00:00:00Z",
        "stage": {
            "pct": 25,
            "verdict": "NO-GO: slo — error_rate 1.000 > 0.010",
            "numbers": [
                "slo: traffic canary(v0.2.0-rc1)=2.000 stable(v0.1.0)=2.000",
                "slo: error_rate canary(v0.2.0-rc1)=1.000 stable(v0.1.0)=0.000",
                "slo: latency_p95 canary(v0.2.0-rc1)=0.020 stable(v0.1.0)=0.012",
            ],
        },
        "chain": {"ticket": "0001", "pr": 1, "merge": phase1["merge_sha"]},
    }
    normalized_item = webhook.normalize_payload(alert_payload)["alerts"][0]
    proposed_version = "v9.9.9-wrong" if inject_break == "triage" else "v0.2.0-rc1"
    _write_adk_fixture(
        sandbox,
        _TRIAGE_TEMPLATE,
        json.dumps(normalized_item, ensure_ascii=False, indent=2) + "\n",
        parts=[
            {
                "function_call": {
                    "id": "call_triage_1",
                    "name": "file_incident",
                    "args": {
                        "title": "Canary v0.2.0-rc1 100% 5xx error rate on /checkout",
                        "hypotheses": [
                            {
                                "rank": 1,
                                "cause": "v0.2.0-rc1 introduced a division-by-zero regression for promo VIP0",
                                "evidence": "error_rate canary(v0.2.0-rc1)=1.000 vs stable(v0.1.0)=0.000",
                            },
                            {
                                "rank": 2,
                                "cause": "Config map mismatch on canary pod",
                                "evidence": "stable serves normal traffic at 0.000 error rate",
                            },
                        ],
                    },
                }
            },
            {
                "function_call": {
                    "id": "call_triage_2",
                    "name": "propose_rollback",
                    "args": {
                        "version": proposed_version,
                        "reason": "Canary v0.2.0-rc1 fails 100% of /checkout promo VIP0 requests while stable is healthy.",
                    },
                }
            },
        ],
        final_text=f"Proposed rollback for {proposed_version} due to 100% canary error rate.",
    )

    hook_env = dict(
        env,
        AUTO_MITIGATE_ON_TRIAGE="on",
        AUTO_INCIDENT_ON_ROLLBACK="on",
        AUTO_POSTMORTEM_ON_ROLLBACK="off",
        RELEASE_TICKET="0001",
        ROLLBACK_LOG_SOURCE=str(canary_log),
    )
    bearer = f"demo-loop-hook-{os.getpid()}"
    alerts_dir = paths["state_dir"] / "alerts"
    srv = webhook.create_server(bind="127.0.0.1", port=0, out_dir=str(alerts_dir), secret=bearer, dispatch=True, env=hook_env)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/alerts",
            data=json.dumps(alert_payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 - loopback webhook server
            hook_resp = json.loads(resp.read().decode("utf-8"))
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)

    dispatched = hook_resp.get("dispatched") or []
    if not dispatched or dispatched[0].get("status") != "ok":
        detail = dispatched[0].get("line") if dispatched else "no alert dispatched"
        raise LoopFailed(f"triage rejected alert for v0.2.0-rc1: {detail}")

    mit = dispatched[0].get("mitigation") or {}
    if mit.get("status") != "applied":
        raise LoopFailed(f"triage mitigation did not apply rollback: {mit}")

    # Update release record stages with the observed 5xx breach so incident exemplars carry the breach cleanly
    rel = _load_json(Path(env["RELEASE_RECORD"]))
    rel["stages"].append(
        {
            "pct": 25,
            "verdict": "NO-GO: slo — error_rate 1.000 > 0.010",
            "numbers": [
                "slo: traffic canary(v0.2.0-rc1)=2.000 stable(v0.1.0)=2.000",
                "slo: error_rate canary(v0.2.0-rc1)=1.000 stable(v0.1.0)=0.000",
                "slo: latency_p95 canary(v0.2.0-rc1)=0.020 stable(v0.1.0)=0.012",
            ],
            "started_at": "2026-11-25T00:00:00Z",
            "judged_at": "2026-11-25T00:01:00Z",
            "slo": [
                {"query": "traffic", "canary": 2.0, "stable": 2.0, "delta": 0.0, "max_delta": None},
                {"query": "error_rate", "canary": 1.0, "stable": 0.0, "delta": 1.0, "max_delta": 0.01},
                {"query": "latency_p95", "canary": 0.02, "stable": 0.012, "delta": 0.008, "max_delta": 0.25},
            ],
        }
    )
    _write_json(Path(env["RELEASE_RECORD"]), rel)
    with contextlib.redirect_stderr(io.StringIO()):
        inc_res = ops_loop.on_rollback(env["RELEASE_RECORD"], hook_env, strict=True, release_hook=True)

    incident_path = Path(inc_res["incident_path"])
    raw_inc = incident_path.read_text(encoding="utf-8")
    if secret_tok in raw_inc or secret_hdr in raw_inc:
        raise LoopFailed("unredacted credential leaked into rollback incident document")
    if "[REDACTED]" not in raw_inc:
        raise LoopFailed("expected [REDACTED] token marker in rollback incident logs")

    delivery_status = json.loads(_run(["sh", "adapters/file-delivery/status.sh"], cwd=sandbox, env=env, check_msg="delivery status failed").stdout)
    if delivery_status.get("weight") != 0 or delivery_status.get("canary") is not None:
        raise LoopFailed(f"expected canary rolled back to 0%, got {delivery_status}")

    mitigations = [json.loads(line) for line in Path(env["MITIGATIONS_LOG"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    # Reflect the rolled-back state on ticket 0001 in StateStore so logbook stays consistent
    t1_raw = _run(["sh", "adapters/sqlite/get.sh", "tickets", "0001"], cwd=sandbox, env=env, check_msg="sqlite get 0001 failed").stdout
    t1_doc = json.loads(t1_raw)
    t1_doc["state"] = "rolled-back"
    t1_doc["release"] = {"version": "v0.2.0-rc1", "image": phase1["image"], "verdict": "rolled-back"}
    t1_doc["history"].append({"step": "rollback", "state": "rolled-back", "at": _utc_now()})
    _sqlite_put(sandbox, env, "tickets", "0001", t1_doc)

    return {
        "verdict": "GO",
        "webhook_written": hook_resp["written"],
        "mitigation_status": mit["status"],
        "mitigation_actions": mit["actions"],
        "mitigations_count": len(mitigations),
        "delivery_weight_after": delivery_status["weight"],
        "release_verdict_after": rel["verdict"],
        "incident_path": str(incident_path),
        "incident": inc_res["incident"],
    }


def _phase_postmortem_and_ticket(
    paths: dict[str, Path],
    env: dict[str, str],
    phase2: dict[str, Any],
    inject_break: str | None,
) -> dict[str, Any]:
    """Phase 3: run core/postmortem.sh + derive_action_perimeters() -> ticket 0002 passing gates/spec.sh."""
    _say("phase 3: running postmortem, filing fix ticket 0002, and deriving ## Perimeter")
    sandbox = paths["sandbox"]
    repo_dir = paths["repo_dir"]
    incident_path = Path(phase2["incident_path"])
    inc_doc = phase2["incident"]
    merge_sha = inc_doc["chain"]["merge"]

    acceptance = (
        "Given the checkout endpoint receives promo VIP0\n"
        "When GET /checkout?items=4&promo=VIP0 is requested\n"
        "Then the checkout works correctly"
        if inject_break == "spec"
        else "Given the checkout endpoint receives promo VIP0\n"
        "When GET /checkout?items=4&promo=VIP0 is requested\n"
        "Then HTTP 200 returns total 20.0 without ZeroDivisionError"
    )
    _write_adk_fixture(
        sandbox,
        _POSTMORTEM_TEMPLATE,
        incident_path.read_text(encoding="utf-8"),
        parts=[
            {
                "function_call": {
                    "id": "call_pm_1",
                    "name": "file_postmortem",
                    "args": {
                        "title": "Postmortem: v0.2.0-rc1 rollback on /checkout VIP0 5xx regression",
                        "timeline": [
                            {"anchor": "release:v0.2.0-rc1", "event": "v0.2.0-rc1 deployed from PR #1."},
                            {"anchor": "pr:1", "event": "Feature PR #1 introduced promo VIP0 handling."},
                            {"anchor": f"merge:{merge_sha}", "event": "PR #1 merged to main."},
                            {"anchor": "log:1", "event": "VIP0 checkout probe returned HTTP 500 ZeroDivisionError."},
                            {"anchor": "alert:1", "event": "Canary 5xx alert triggered policy rollback to 0%."},
                        ],
                        "causes": [
                            {
                                "cause": "promo VIP0 mapped to zero divisor in checkout_total",
                                "evidence": "log:1 shows ZeroDivisionError on GET /checkout?items=4&promo=VIP0",
                            }
                        ],
                        "actions": [
                            {
                                "title": "Fix VIP0 promo zero-division regression and add test",
                                "problem": "checkout_total divides by zero when promo is VIP0, causing 500 errors.",
                                "acceptance": acceptance,
                            }
                        ],
                    },
                }
            }
        ],
        final_text="Postmortem filed with fix ticket action item.",
    )

    pm_env = dict(env, INCIDENT=str(incident_path))
    pm_proc = _run(["sh", "core/postmortem.sh"], cwd=sandbox, env=pm_env, check_msg="postmortem failed")

    pm_dir = Path(env["POSTMORTEM_OUT"]) / inc_doc["name"]
    pm_json = _load_json(pm_dir / "postmortem.json")
    ticket_id = str(pm_json["actions"][0]["ticket"])
    ticket_path = repo_dir / "tickets" / f"{ticket_id}.md"

    with contextlib.redirect_stderr(io.StringIO()):
        perim_results = ops_loop.derive_action_perimeters(
            [str(ticket_path)],
            inc_doc["chain"],
            str(repo_dir),
            map_file=env["MAP_FILE"],
            env=dict(env, AUTO_PROMOTE_POSTMORTEM_TICKETS="on"),
        )
    perim = perim_results[0]
    if perim["status"] != "injected":
        raise LoopFailed(f"expected derived perimeter injected on {ticket_id}, got {perim}")

    spec_proc = _run(["sh", "gates/spec.sh", str(ticket_path)], cwd=sandbox, env=env)
    if spec_proc.returncode != 0:
        raise LoopFailed(f"spec gate rejected ticket {ticket_id}: {spec_proc.stdout.strip()}")

    return {
        "verdict": "GO",
        "postmortem_line": pm_proc.stdout.strip(),
        "postmortem_md": str(pm_dir / "postmortem.md"),
        "postmortem_json": str(pm_dir / "postmortem.json"),
        "ticket_id": ticket_id,
        "ticket_path": str(ticket_path),
        "derived_perimeter": perim["perimeter"],
        "readiness": perim["readiness"],
        "spec_line": spec_proc.stdout.strip(),
    }


def _phase_fix_gates_merge_mainline(
    paths: dict[str, Path],
    env: dict[str, str],
    phase3: dict[str, Any],
    inject_break: str | None,
) -> dict[str, Any]:
    """Phase 4: open fix PR #2, run real PR gates + adr-check + spec, merge to main, verify mainline."""
    _say("phase 4: opening fix PR #2, running 8/8 PR gates + adr-check + spec, merging & verifying mainline")
    sandbox = paths["sandbox"]
    repo_dir = paths["repo_dir"]
    ticket_id = phase3["ticket_id"]
    ticket_path = Path(phase3["ticket_path"])

    # Stage ticket 0002 cleanly on main before branching or work on branch ticket/0002
    base_sha = _git(repo_dir, "rev-parse", "main")
    branch = f"ticket/{ticket_id}"
    _git(repo_dir, "checkout", "-q", "-b", branch, "main")

    # Dev-worker fix: omit code fix when inject_break == "gates" so gates/test.sh fails genuinely
    if inject_break != "gates":
        _write_text(repo_dir / "svc" / "server.py", _SERVICE_FIXED)
    _write_text(repo_dir / "tests" / "test_server.py", _TESTS_FIXED)
    _git(repo_dir, "add", "svc/server.py", "tests/test_server.py")
    _git(repo_dir, "commit", "-q", "-m", f"fix({ticket_id}): handle VIP0 promo without division by zero (ADR-0001)")
    head_sha = _git(repo_dir, "rev-parse", "HEAD")

    # Perimeter check on diff
    diff_names = _git(repo_dir, "diff", "--no-renames", "--name-only", base_sha, head_sha) + "\n"
    _run(
        ["sh", "core/perimeter.sh", str(ticket_path)],
        cwd=sandbox,
        env=env,
        stdin_text=diff_names,
        check_msg="perimeter check refused PR diff",
    )

    _run(["sh", "adapters/file-codehost/push.sh", str(repo_dir), branch], cwd=sandbox, env=env, check_msg="push fix branch failed")
    pr2_json = sandbox / "pr2.json"
    _write_json(
        pr2_json,
        {
            "title": f"Fix VIP0 promo 5xx regression ({ticket_id})",
            "body": f"Fixes ticket {ticket_id} inside derived perimeter and adds regression test.",
        },
    )
    open_out = _run(
        ["sh", "adapters/file-codehost/open-pr.sh", branch, str(pr2_json)],
        cwd=sandbox,
        env=env,
        check_msg="open-pr #2 failed",
    ).stdout.strip()
    pr_num_str, _, pr_url = open_out.partition(" ")
    pr_num = int(pr_num_str)

    gates_out = sandbox / "gates-out"
    gates_out.mkdir(parents=True, exist_ok=True)
    # Read contract keys into gate env
    c_env = dict(env)
    for line in (repo_dir / CONTRACT_REL / "workload.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            c_env[k.strip()] = v.strip()
    c_env.update(
        {
            "BASE": base_sha,
            "HEAD": head_sha,
            "GATES_OUT": str(gates_out),
            "GATES_TICKET": str(ticket_path),
            "TICKET": ticket_id,
        }
    )

    gate_commands: list[tuple[str, list[str]]] = [
        ("risk", ["sh", "gates/risk.sh"]),
        ("secrets", ["sh", "ci/secrets.sh"]),
        ("test-weakening", ["sh", "gates/test-weakening.sh"]),
        ("deps", ["sh", "gates/deps.sh"]),
        ("sast", ["sh", "gates/sast.sh"]),
        ("test", ["sh", "gates/test.sh"]),
        ("mutation", ["sh", "gates/mutation.sh"]),
        ("test-proof", ["sh", "gates/test-proof.sh"]),
        ("adr-check", ["sh", "gates/adr-check.sh"]),
        ("spec", ["sh", "gates/spec.sh", str(ticket_path)]),
    ]
    gate_lines: list[str] = []
    gate_seconds: dict[str, float] = {}
    for gate_name, cmd in gate_commands:
        gs = time.monotonic()
        proc = _run(cmd, cwd=sandbox, env=c_env)
        elapsed_g = round(time.monotonic() - gs, 3)
        line = (proc.stdout or proc.stderr or "").strip().splitlines()[-1] if (proc.stdout or proc.stderr or "").strip() else f"NO-GO: {gate_name}"
        if gate_name in ("risk", "secrets", "test-weakening", "deps", "sast", "test", "mutation", "test-proof"):
            gate_lines.append(line)
            gate_seconds[gate_name] = elapsed_g
        if proc.returncode != 0:
            raise LoopFailed(f"gate {gate_name} failed: {line}")

    risk_doc = _load_json(gates_out / "risk.json")
    summary_line = f"GO: gates — 8/8 PR gates passed ({risk_doc['level']}, {risk_doc['decision']})"
    _run(
        ["sh", "adapters/file-codehost/status.sh", head_sha, "success", "gates", summary_line],
        cwd=sandbox,
        env=env,
        check_msg="post commit status failed",
    )

    t_now = _utc_now()
    ticket_doc: dict[str, Any] = {
        "id": ticket_id,
        "state": "ci-passed",
        "attempt": 1,
        "spec": {
            "sha256": hashlib.sha256(ticket_path.read_bytes()).hexdigest(),
            "title": "Fix VIP0 promo zero-division regression and add test",
            "verdict": "GO",
            "line": "GO: spec",
        },
        "tasks": [
            {
                "attempt": 1,
                "role": "dev-worker",
                "harness": "replay",
                "provider": "local",
                "model": "deterministic",
                "thinking": "high",
                "turns": 2,
                "input_sha256": hashlib.sha256(ticket_path.read_bytes()).hexdigest(),
                "tokens": {"input": 420, "output": 140},
                "usd": 0.0,
                "files": [
                    {"path": "svc/server.py", "added": 3, "removed": 2},
                    {"path": "tests/test_server.py", "added": 6, "removed": 0},
                ],
            }
        ],
        "pr": {"number": pr_num, "url": pr_url, "base_sha": base_sha, "head_sha": head_sha},
        "ci": {
            "verdict": "GO",
            "failed": None,
            "risk_level": risk_doc["level"],
            "decision": risk_doc["decision"],
            "head_sha": head_sha,
            "line": summary_line,
            "details": [],
            "lines": gate_lines,
            "seconds": gate_seconds,
        },
        "history": [
            {"step": "request", "state": "requested", "at": t_now, "by": "postmortem", "via": "ops-loop"},
            {"step": "spec", "state": "opened", "at": t_now},
            {"step": "assign", "state": "pr-opened", "at": t_now, "attempt": 1},
            {"step": "ci", "state": "ci-passed", "at": t_now, "attempt": 1},
        ],
    }

    doc_file = sandbox / f"doc-{ticket_id}.json"
    _write_json(doc_file, ticket_doc)
    ml_env = dict(env, TICKET=ticket_id, DOC_FILE=str(doc_file))

    rejudge_proc = _run(
        [sys.executable, "core/mainline.py", "rejudge"],
        cwd=sandbox,
        env=ml_env,
        check_msg="mainline rejudge failed",
    )
    if rejudge_proc.stdout.strip():
        raise LoopFailed(f"mainline rejudge blocked merge: {rejudge_proc.stdout.strip()}")

    merge2_out = _run(
        ["sh", "adapters/file-codehost/merge.sh", str(pr_num), head_sha],
        cwd=sandbox,
        env=env,
        check_msg="merge pr #2 failed",
    ).stdout.strip()
    merge2_sha = merge2_out.split()[0]

    _git(repo_dir, "checkout", "-q", "main")
    clone_url = _run(["sh", "adapters/file-codehost/clone-url.sh"], cwd=sandbox, env=env, check_msg="clone-url failed").stdout.strip()
    _git(repo_dir, "pull", "-q", "--ff-only", clone_url, "main")

    # Rebuild project map after merge
    _run(
        [
            sys.executable,
            "core/map.py",
            "build",
            "--repo",
            str(repo_dir),
            "--tickets",
            "tickets",
            "--targets",
            str(paths["targets_file"]),
            "--out",
            env["MAP_FILE"],
        ],
        cwd=sandbox,
        env=env,
        check_msg="post-merge map build failed",
    )

    ticket_doc["state"] = "merged"
    ticket_doc["merge"] = {"sha": merge2_sha, "head_sha": head_sha}
    ticket_doc["history"].append({"step": "merge", "state": "merged", "at": _utc_now()})
    _write_json(doc_file, ticket_doc)

    # Verify real tree gates (sast + test) on merged main commit + run mainline.check()
    main_gates_out = sandbox / "main-gates-out"
    main_gates_out.mkdir(parents=True, exist_ok=True)
    main_gate_env = dict(c_env, BASE=base_sha, HEAD=merge2_sha, GATES_OUT=str(main_gates_out))
    for g in ("sast", "test"):
        _run(["sh", f"gates/{g}.sh"], cwd=sandbox, env=main_gate_env, check_msg=f"mainline {g} failed")

    ml_proc = _run(
        [sys.executable, "core/mainline.py", "check"],
        cwd=sandbox,
        env=ml_env,
        check_msg="mainline check failed",
    )
    ml_res = json.loads(ml_proc.stdout.strip())
    if ml_res.get("state") != "main-green":
        raise LoopFailed(f"mainline check did not reach main-green: {ml_res}")

    ticket_doc["state"] = "main-green"
    ticket_doc["mainline"] = ml_res["mainline"]
    ticket_doc["history"].append({"step": "mainline", "state": "main-green", "at": _utc_now()})
    _sqlite_put(sandbox, env, "tickets", ticket_id, ticket_doc)

    return {
        "verdict": "GO",
        "pr_number": pr_num,
        "base_sha": base_sha,
        "head_sha": head_sha,
        "merge_sha": merge2_sha,
        "risk_level": risk_doc["level"],
        "risk_decision": risk_doc["decision"],
        "gates_count": 8,
        "gate_lines": gate_lines,
        "gate_seconds": gate_seconds,
        "mainline_state": ml_res["state"],
        "ticket_doc": ticket_doc,
    }


def _phase_canary_promote_provenance_freeze(
    paths: dict[str, Path],
    env: dict[str, str],
    phase3: dict[str, Any],
    phase4: dict[str, Any],
    inject_break: str | None,
) -> dict[str, Any]:
    """Phase 5: stage v0.2.1 -> GO_FOR_LAUNCH=GO 100% -> provenance -> logbook + frozen HTML."""
    _say("phase 5: staging v0.2.1 canary -> GO_FOR_LAUNCH=GO 100% -> provenance & frozen console")
    sandbox = paths["sandbox"]
    repo_dir = paths["repo_dir"]
    ticket_id = phase3["ticket_id"]
    merge2_sha = phase4["merge_sha"]
    canary_tag = f"sha-{merge2_sha}"

    fixed_image = _run(
        ["sh", "adapters/file-registry/publish.sh", canary_tag],
        cwd=sandbox,
        env=env,
        check_msg="publish v0.2.1 failed",
    ).stdout.strip()

    # Probe fixed service endpoints (including previously failing /checkout?promo=VIP0)
    fixed_probes = _probe_repo_service(
        repo_dir,
        ["/health", "/quote?price=100&discount=10", "/checkout?items=4&promo=VIP0"],
    )
    if not all(code == 200 for _, code, _ in fixed_probes):
        raise LoopFailed(f"fixed v0.2.1 service failed live HTTP probe: {fixed_probes}")

    metrics_doc = _metrics_from_probes(
        "v0.1.0",
        [("/health", 200, 0.01), ("/quote", 200, 0.01)],
        "v0.2.1",
        fixed_probes,
    )
    if inject_break == "canary":
        metrics_doc["v0.2.1"]["error_rate"] = 0.800
    _write_json(paths["metrics_file"], metrics_doc)

    lp_env = dict(env, CANARY_VERSION="v0.2.1", CANARY_TAG=canary_tag, RELEASE_TICKET=ticket_id)
    lp_proc = _run(["sh", "core/launchpad.sh"], cwd=sandbox, env=lp_env)
    if lp_proc.returncode != 0:
        lp_line = (lp_proc.stdout or lp_proc.stderr or "").strip().splitlines()[-1] if (lp_proc.stdout or lp_proc.stderr or "").strip() else "canary failed"
        raise LoopFailed(f"canary v0.2.1 failed SLO staging: {lp_line}")

    promote_env = dict(
        env,
        GO_FOR_LAUNCH="GO",
        GO_FOR_LAUNCH_BY="demo-operator",
        GO_FOR_LAUNCH_VIA="cli",
        GO_FOR_LAUNCH_REASON="v0.2.1 VIP0 fix verified at 25% canary",
    )
    promote_proc = _run(["sh", "core/promote.sh"], cwd=sandbox, env=promote_env, check_msg="promote to 100% failed")
    delivery_status = json.loads(
        _run(["sh", "adapters/file-delivery/status.sh"], cwd=sandbox, env=env, check_msg="delivery status failed").stdout
    )
    if delivery_status.get("canary") != "v0.2.1" or delivery_status.get("weight") != 100:
        raise LoopFailed(f"expected v0.2.1 at 100% after promote, got {delivery_status}")

    rel = _load_json(Path(env["RELEASE_RECORD"]))
    ticket_doc = dict(phase4["ticket_doc"])
    ticket_doc["state"] = "held"
    ticket_doc["release"] = {
        "version": "v0.2.1",
        "image": fixed_image,
        "verdict": "held",
        "numbers": [num for st in rel.get("stages") or [] for num in st.get("numbers") or []],
    }
    ticket_doc["history"].append({"step": "release", "state": "held", "at": _utc_now()})
    _sqlite_put(sandbox, env, "tickets", ticket_id, ticket_doc)

    # Provenance build & verify (cosign-signed when cosign is available)
    prov_dir = Path(env["PROVENANCE_DIR"])
    att_path = prov_dir / "v0.2.1.intoto.json"
    bundle_path: Path | None = None
    cosign_pub: Path | None = None
    if paths["cosign_key"].is_file() and shutil.which("cosign"):
        _run(
            [
                "sh",
                "core/provenance.sh",
                "build",
                "--version",
                "v0.2.1",
                "--ticket",
                ticket_id,
                "--repo",
                str(repo_dir),
                "--out-dir",
                str(prov_dir),
            ],
            cwd=sandbox,
            env=env,
            check_msg="provenance build+sign failed",
        )
        _run(["sh", "core/provenance.sh", "verify", str(att_path)], cwd=sandbox, env=env, check_msg="provenance verify failed")
        bundle_path = prov_dir / "v0.2.1.intoto.json.bundle"
        cosign_pub = paths["cosign_key"].with_suffix(".pub")
    else:
        _run(
            [
                sys.executable,
                "core/provenance.py",
                "build",
                "--version",
                "v0.2.1",
                "--ticket",
                ticket_id,
                "--repo",
                str(repo_dir),
                "--out-dir",
                str(prov_dir),
            ],
            cwd=sandbox,
            env=env,
            check_msg="provenance build failed",
        )
        _run(
            [sys.executable, "core/provenance.py", "verify", "--statement", str(att_path), "--repo", str(repo_dir)],
            cwd=sandbox,
            env=env,
            check_msg="provenance verify failed",
        )
    stmt = _load_json(att_path)

    # Logbook + frozen snapshot HTML
    _run(["sh", "core/logbook.sh"], cwd=sandbox, env=env, check_msg="logbook build failed")
    logbook_path = Path(env["LOGBOOK_FILE"])
    frozen_html = paths["state_dir"] / "console-frozen.html"
    with contextlib.redirect_stdout(io.StringIO()):
        rc = freeze.main(
            [
                "--logbook",
                str(logbook_path),
                "--out",
                str(frozen_html),
                "--page",
                str(sandbox / "console" / "index.html"),
                "--prefix",
                "demo-loop v0.2.1",
            ]
        )
    if rc != 0 or not frozen_html.is_file():
        raise LoopFailed("core/freeze.py failed to produce frozen console HTML")

    return {
        "verdict": "GO",
        "version": "v0.2.1",
        "image": fixed_image,
        "launchpad_line": lp_proc.stdout.strip(),
        "promote_line": promote_proc.stdout.strip(),
        "delivery_weight": delivery_status["weight"],
        "provenance_path": str(att_path),
        "provenance_bundle": str(bundle_path) if bundle_path else None,
        "cosign_pub": str(cosign_pub) if cosign_pub else None,
        "provenance_missing": stmt["predicate"]["missing"],
        "logbook_path": str(logbook_path),
        "frozen_html_path": str(frozen_html),
    }


def _render_summary_md(report: Mapping[str, Any]) -> str:
    phases = report["phases"]
    lines = [
        "# Closed Self-Healing Demo Loop Summary (`ADR 0132`)",
        "",
        f"- Verdict: `{report['verdict']}`",
        f"- Contract line: `{report['line']}`",
        f"- Elapsed: `{report['elapsed_s']}s` (budget `{report['budget_s']}s`)",
        "",
        "## Phases",
        "",
        f"1. **Initial held canary**: `{phases['initial_canary']['version']}` held at `{phases['initial_canary']['held_at']}%` (`{phases['initial_canary']['image']}`)",
        f"2. **Alert -> Triage -> Policy Rollback**: `{phases['triage_rollback']['mitigation_status']}`, weight `{phases['triage_rollback']['delivery_weight_after']}%`, release `{phases['triage_rollback']['release_verdict_after']}`",
        f"3. **Post-mortem -> Ticket + Derived Perimeter**: ticket `{phases['postmortem']['ticket_id']}` (`{phases['postmortem']['spec_line']}`)",
        f"4. **Fix PR -> 8/8 Gates -> Merge & Mainline**: PR `#{phases['fix_gates']['pr_number']}`, `{phases['fix_gates']['gates_count']}/8` gates passed, mainline `{phases['fix_gates']['mainline_state']}`",
        f"5. **Fix Canary -> Human GO -> 100% + Provenance & Frozen Console**: `{phases['canary_promote']['version']}` at `{phases['canary_promote']['delivery_weight']}%`, provenance gaps `{len(phases['canary_promote']['provenance_missing'])}`",
        "",
    ]
    return "\n".join(lines)


def _export_artifacts(out_dir: Path, paths: dict[str, Path], report: dict[str, Any]) -> dict[str, str]:
    """Copy key artifacts from sandbox into DEMO_LOOP_OUT and return exported paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_dir in ("alerts", "postmortem", "tickets"):
        shutil.rmtree(out_dir / stale_dir, ignore_errors=True)
    for stale_file in (
        "incident.json",
        "mitigations.jsonl",
        "release.json",
        "releases.jsonl",
        "v0.2.1.intoto.json",
        "v0.2.1.intoto.json.bundle",
        "cosign.pub",
        "logbook.json",
        "console-frozen.html",
        "summary.md",
        "summary.json",
    ):
        (out_dir / stale_file).unlink(missing_ok=True)

    exported: dict[str, str] = {}
    phases = report["phases"]

    copies: list[tuple[str, str | None, str]] = [
        ("alert", phases.get("triage_rollback", {}).get("webhook_written"), "alerts/alert.json"),
        ("incident", phases.get("triage_rollback", {}).get("incident_path"), "incident.json"),
        ("postmortem_md", phases.get("postmortem", {}).get("postmortem_md"), "postmortem/postmortem.md"),
        ("postmortem_json", phases.get("postmortem", {}).get("postmortem_json"), "postmortem/postmortem.json"),
        ("ticket_0001", str(paths["repo_dir"] / "tickets" / "0001.md"), "tickets/0001.md"),
        ("ticket_0002", phases.get("postmortem", {}).get("ticket_path"), "tickets/0002.md"),
        ("mitigations", str(paths["state_dir"] / "mitigations.jsonl"), "mitigations.jsonl"),
        ("release_record", str(paths["state_dir"] / "release.json"), "release.json"),
        ("releases_log", str(paths["state_dir"] / "releases.jsonl"), "releases.jsonl"),
        ("provenance", phases.get("canary_promote", {}).get("provenance_path"), "v0.2.1.intoto.json"),
        ("provenance_bundle", phases.get("canary_promote", {}).get("provenance_bundle"), "v0.2.1.intoto.json.bundle"),
        ("cosign_pub", phases.get("canary_promote", {}).get("cosign_pub"), "cosign.pub"),
        ("logbook", phases.get("canary_promote", {}).get("logbook_path"), "logbook.json"),
        ("frozen_html", phases.get("canary_promote", {}).get("frozen_html_path"), "console-frozen.html"),
    ]
    for key, src, rel in copies:
        if src and os.path.isfile(src):
            dst = out_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            exported[key] = str(dst)

    summary_json = out_dir / "summary.json"
    exported["summary_json"] = str(summary_json)
    summary_md = out_dir / "summary.md"
    exported["summary_md"] = str(summary_md)
    report["artifacts"] = exported
    _write_json(summary_json, report)
    if report["verdict"] == "GO":
        _write_text(summary_md, _render_summary_md(report))
    return exported


def run_demo_loop(
    *,
    out_dir: str | Path | None = None,
    budget_s: int = DEMO_LOOP_BUDGET_SECONDS,
    inject_break: str | None = None,
) -> dict[str, Any]:
    """Execute the deterministic 5-phase closed self-healing loop inside an isolated sandbox."""
    if inject_break is not None and inject_break not in INJECT_STAGES:
        raise ValueError(f"unsupported --inject-break stage {inject_break!r}; choose from {', '.join(INJECT_STAGES)}")
    if budget_s <= 0:
        raise ValueError(f"budget_s must be positive, got {budget_s}")

    resolved_out = Path(out_dir or os.environ.get("DEMO_LOOP_OUT") or os.path.join(".crew", "demo-loop")).resolve()
    start = time.monotonic()
    phases: dict[str, Any] = {}
    verdict = "NO-GO"
    reason = ""

    with tempfile.TemporaryDirectory(prefix="lights-out-demo-loop-") as tmp:
        paths = _prepare_sandbox(Path(tmp).resolve())
        env = _base_env(paths)
        orig_cwd = os.getcwd()
        os.chdir(paths["sandbox"])
        try:
            phases["initial_canary"] = _phase_initial_held_canary(paths, env)
            phases["triage_rollback"] = _phase_alert_triage_rollback(paths, env, phases["initial_canary"], inject_break)
            phases["postmortem"] = _phase_postmortem_and_ticket(paths, env, phases["triage_rollback"], inject_break)
            phases["fix_gates"] = _phase_fix_gates_merge_mainline(paths, env, phases["postmortem"], inject_break)
            phases["canary_promote"] = _phase_canary_promote_provenance_freeze(
                paths, env, phases["postmortem"], phases["fix_gates"], inject_break
            )
            elapsed = max(1, round(time.monotonic() - start))
            if elapsed > budget_s:
                raise LoopFailed(f"elapsed {elapsed}s exceeded budget {budget_s}s")
            verdict = "GO"
        except LoopFailed as exc:
            reason = str(exc)
        finally:
            os.chdir(orig_cwd)
        if verdict != "GO":
            elapsed = max(1, round(time.monotonic() - start))

        if verdict == "GO":
            line = (
                f"GO: demo-loop — closed loop (alert -> rollback -> postmortem -> ticket -> gates -> canary -> 100%) "
                f"in {elapsed}s (budget {budget_s}s)"
            )
        else:
            line = f"NO-GO: demo-loop — {reason}"

        report: dict[str, Any] = {
            "schema": "lights-out.demo-loop/1",
            "verdict": verdict,
            "line": line,
            "reason": reason or None,
            "elapsed_s": elapsed,
            "budget_s": budget_s,
            "inject_break": inject_break,
            "phases": {
                k: {pk: pv for pk, pv in v.items() if pk not in ("incident", "ticket_doc")}
                for k, v in phases.items()
            },
            "artifacts": {},
        }
        _export_artifacts(resolved_out, paths, report)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core/demo_loop.py", description=__doc__.splitlines()[0])
    parser.add_argument("action", nargs="?", default="run", choices=("run",))
    parser.add_argument("--json", action="store_true", help="Print structured JSON report to stdout")
    parser.add_argument("--out", default=None, help="Artifact export directory (default DEMO_LOOP_OUT or .crew/demo-loop)")
    parser.add_argument(
        "--budget",
        type=int,
        default=int(os.environ.get("DEMO_LOOP_BUDGET_SECONDS") or DEMO_LOOP_BUDGET_SECONDS),
        help="Ceiling in seconds (default 300)",
    )
    parser.add_argument("--inject-break", choices=INJECT_STAGES, default=None, help="Inject deterministic break at stage")
    args = parser.parse_args(argv)

    try:
        report = run_demo_loop(out_dir=args.out, budget_s=args.budget, inject_break=args.inject_break)
    except ValueError as exc:
        line = f"NO-GO: demo-loop — {exc}"
        print(json.dumps({"verdict": "NO-GO", "line": line, "reason": str(exc)}) if args.json else line)
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["line"])
    return 0 if report["verdict"] == "GO" else 1


if __name__ == "__main__":
    sys.exit(main())
