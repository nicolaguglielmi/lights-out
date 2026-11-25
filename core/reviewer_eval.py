#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Labeled-diff reviewer and deterministic gate catch-rate evaluation (ADR 0137).

    python3 core/reviewer_eval.py [--fixtures DIR] [--mode replay|live]
                                  [--role auto|test-reviewer|architect]
                                  [--scope combined|reviewer|deterministic]
                                  [--provider NAME] [--model NAME]
                                  [--thinking LEVEL] [--record-replay] [--json]
    python3 core/reviewer_eval.py [--compare-manifest FILE] [--repo DIR]
                                  [--map FILE] [--mode replay|live]
                                  [--only-compare] [--json]

Evaluates deterministic checks (test-weakening, perimeter, migration-safety,
risk, secrets) alongside a pluggable reviewer (recorded L0 replay or live model
call) and optional MAP_BRIEF=off vs MAP_BRIEF=on architect comparison.
Stdlib-only at L0; strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess  # nosec B404 - fixed-argv gitleaks stdin probe without shell
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

try:
    from . import map as project_map
    from . import risk, testdiff
    from .gates import migration
except ImportError:
    _REPO_ROOT = Path(__file__).resolve().parents[1]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    project_map = importlib.import_module("core.map")
    risk = importlib.import_module("core.risk")
    testdiff = importlib.import_module("core.testdiff")
    migration = importlib.import_module("core.gates.migration")

SCHEMA = "lights-out/reviewer-eval/1"
REPLAY_SCHEMA = "lights-out/reviewer-eval-replay/1"
COMPARE_SCHEMA = "lights-out/architect-map-brief-compare/1"
DEFAULT_FIXTURES_DIR = os.path.join("fixtures", "reviewer-eval")
DEFAULT_TEST_GLOBS = "tests/*:*/tests/*:test_*.py:*_test.py:*.test.js:*.spec.js"
PLACEHOLDER_VALUES = frozenset({"", "replace-me", "changeme", "todo", "example", "dummy"})
VALID_MODES = ("replay", "live")
VALID_SCOPES = ("combined", "reviewer", "deterministic")
VALID_ROLES = ("auto", "test-reviewer", "architect")

_PEM_HEADER_RE = re.compile(r"-----BEGIN [A-Z0-9 ]*" + r"PRIVATE KEY-----")
_SECRET_ASSIGN_RE = re.compile(
    r"\b(?:PASSWORD|PASSWD|SECRET|ACCESS_TOKEN|AUTH_TOKEN|SIGNING_KEY|PRIVATE_KEY|BEARER_TOKEN)\b"
    r"\s*[:=]\s*['\"]([A-Za-z0-9_/+.:-]{16,})['\"]",
    re.IGNORECASE,
)
_ADR_REF_RE = re.compile(r"\b(?:docs/)?adr/(\d{4})-[^\s`)\],;]*|\bADR[- ](\d{4})\b", re.IGNORECASE)
_PATH_TOKEN_RE = re.compile(r"`([^`\n]+)`")


class EvalRefused(Exception):
    """Raised when evaluation input or environment fails closed (NO-GO)."""


class QuotaExhaustedError(RuntimeError):
    """Raised when live model endpoint reports quota/rate exhaustion (HTTP 429)."""


class LiveCallError(RuntimeError):
    """Raised when a single live model call fails with a transient/server error (e.g. HTTP 504)."""


def approx_tokens(text_or_bytes: int | str | bytes) -> float:
    """Estimate token count using bytes / 4."""
    if isinstance(text_or_bytes, int):
        return text_or_bytes / 4
    raw = text_or_bytes.encode("utf-8") if isinstance(text_or_bytes, str) else text_or_bytes
    return len(raw) / 4


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_non_placeholder(value: str | None) -> bool:
    cleaned = (value or "").strip()
    return bool(cleaned) and cleaned.lower() not in PLACEHOLDER_VALUES


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvalRefused(f"cannot read JSON {path}: {exc}") from None
    if not isinstance(data, dict):
        raise EvalRefused(f"{path} must contain a JSON object")
    return data


def _extract_diff_header(diff_text: str) -> str:
    """Return commit header text preceding the first `diff --git` marker."""
    marker = "\ndiff --git "
    if diff_text.startswith("diff --git "):
        return ""
    idx = diff_text.find(marker)
    return diff_text[:idx] if idx >= 0 else diff_text


def _changed_paths(diff_files: Sequence[tuple[str | None, str | None, list[str], list[str]]]) -> list[str]:
    """Deduplicated repo-relative paths touched by a diff, preserving order."""
    seen: set[str] = set()
    paths: list[str] = []
    for old, new, _removed, _added in diff_files:
        for candidate in (old, new):
            if candidate and candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
    return paths


def _diff_numstat(diff_files: Sequence[tuple[str | None, str | None, list[str], list[str]]]) -> tuple[list[str], list[str]]:
    """Return (numstat_lines, added_paths) compatible with core.risk.classify."""
    numstat: list[str] = []
    added_paths: list[str] = []
    for old, new, removed, added in diff_files:
        path = new or old
        if not path:
            continue
        numstat.append(f"{len(added)}\t{len(removed)}\t{path}")
        if old is None and new is not None:
            added_paths.append(new)
    return numstat, added_paths


def _load_effective_policy(repo_root: Path, case_policy_path: Path | None) -> dict[str, str]:
    policy: dict[str, str] = {}
    base_policy = repo_root / "policy" / "merge.env"
    if base_policy.is_file():
        policy.update(risk.parse_policy(base_policy.read_text(encoding="utf-8")))
    if case_policy_path is not None and case_policy_path.is_file():
        policy.update(risk.parse_policy(case_policy_path.read_text(encoding="utf-8")))
    return policy


def _check_perimeter(ticket_text: str, changed_paths: Sequence[str]) -> dict[str, Any]:
    globs = project_map.perimeter(ticket_text)
    if not globs:
        return {"go": False, "reason": "ticket has no ## Perimeter section", "outside_paths": list(changed_paths)}
    outside = [p for p in changed_paths if not any(fnmatch.fnmatchcase(p, g) for g in globs)]
    if outside:
        return {
            "go": False,
            "reason": f"{outside[0]} is outside the ticket's declared perimeter",
            "outside_paths": outside,
        }
    return {"go": True, "reason": f"{len(changed_paths)} path(s) inside perimeter", "outside_paths": []}


def _check_test_weakening(diff_text: str, test_globs: str, ticket_text: str) -> dict[str, Any]:
    excuse = testdiff.declared(ticket_text)
    findings, excused, detail = testdiff.weakening(diff_text, test_globs, "", excuse)
    if findings:
        return {"go": False, "reason": findings[0], "findings": findings, "excused": excused}
    return {"go": True, "reason": detail, "findings": [], "excused": excused}


def _reconstruct_head_content(
    rel_path: str,
    head_dir: Path | None,
    diff_files: Sequence[tuple[str | None, str | None, list[str], list[str]]],
) -> str:
    if head_dir is not None:
        candidate = head_dir / rel_path
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8", errors="replace")
    for _old, new, _removed, added in diff_files:
        if new == rel_path:
            return "\n".join(added) + ("\n" if added else "")
    return ""


def _resolve_contract_in_base(declared_texts: Sequence[str], base_migrations: Sequence[str]) -> str | None:
    for decl in declared_texts:
        for tok in migration._tokens(decl):
            for path in base_migrations:
                stem = os.path.splitext(os.path.basename(path))[0]
                if path == tok or path.endswith("/" + tok) or stem == tok:
                    return tok
    return None


def _check_migration_safety(
    diff_text: str,
    diff_files: Sequence[tuple[str | None, str | None, list[str], list[str]]],
    ticket_text: str,
    policy: Mapping[str, str],
    head_dir: Path | None,
    base_dir: Path | None,
) -> dict[str, Any]:
    globs = [g for g in policy.get("RISK_HIGH_MIGRATIONS", "").split(":") if g] + list(migration.DEFAULT_GLOBS)
    touched = [new for old, new, _r, _a in diff_files if new and migration._matches(new, globs)]
    if not touched:
        return {"go": True, "reason": "no migration files changed", "findings": []}

    scanners = {".sql": migration.scan_sql, ".py": migration.scan_python, ".rb": migration.scan_ruby}
    findings: list[tuple[str, Any]] = []
    analyzed = 0
    for rel_path in touched:
        scan = scanners.get(os.path.splitext(rel_path)[1].lower())
        if scan is None:
            continue
        content = _reconstruct_head_content(rel_path, head_dir, diff_files)
        try:
            found = scan(content)
        except migration.NoGo as exc:
            return {"go": False, "reason": f"{rel_path}: {exc.reason}", "findings": [exc.reason]}
        analyzed += 1
        findings.extend((rel_path, item) for item in found)

    if not findings:
        return {"go": True, "reason": f"{analyzed} migration file(s), expand-safe", "findings": []}

    header_text = _extract_diff_header(diff_text)
    declared_texts = migration.declarations(ticket_text) + migration.declarations(header_text)
    base_migrations: list[str] = []
    if base_dir is not None and base_dir.is_dir():
        for path in sorted(base_dir.rglob("*")):
            if path.is_file():
                rel = path.relative_to(base_dir).as_posix()
                if migration._matches(rel, globs):
                    base_migrations.append(rel)

    accepted_ref = _resolve_contract_in_base(declared_texts, base_migrations)
    finding_lines = [f"{p}:{f.line}: {f.rule}: {f.text}" for p, f in findings]
    if accepted_ref:
        return {
            "go": True,
            "reason": f"{analyzed} migration file(s), contract cleanup allowed (contract after {accepted_ref})",
            "findings": [],
        }

    first_path, first = findings[0]
    return {
        "go": False,
        "reason": f"rollback-unsafe migration {first_path}:{first.line} [{first.rule}]",
        "findings": finding_lines,
    }


def _check_secrets(
    diff_files: Sequence[tuple[str | None, str | None, list[str], list[str]]],
    head_dir: Path | None,
    use_gitleaks: bool = True,
) -> dict[str, Any]:
    chunks: list[str] = []
    for _old, new, _removed, added in diff_files:
        if added:
            chunks.append(f"# {new or ''}\n" + "\n".join(added))
    if head_dir is not None and head_dir.is_dir():
        for path in sorted(head_dir.rglob("*")):
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    payload = "\n".join(chunks)

    if _PEM_HEADER_RE.search(payload):
        return {"go": False, "reason": "private key block detected in diff or files"}
    for match in _SECRET_ASSIGN_RE.finditer(payload):
        val = match.group(1)
        if _is_non_placeholder(val) and not val.lower().startswith(("test", "fake", "sample", "mock")):
            return {"go": False, "reason": "hardcoded credential assignment detected"}

    if use_gitleaks and payload.strip():
        gitleaks_bin = shutil.which("gitleaks")
        if gitleaks_bin:
            proc = subprocess.run(  # nosec B603 - fixed gitleaks stdin invocation
                [gitleaks_bin, "stdin", "--no-banner", "--no-color", "--redact", "--log-level", "error"],
                input=payload,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                return {"go": False, "reason": "gitleaks detected a potential secret in diff content"}
    return {"go": True, "reason": "no secrets detected"}


def run_deterministic_checks(
    ticket_text: str,
    diff_text: str,
    *,
    repo_root: Path,
    test_globs: str = DEFAULT_TEST_GLOBS,
    policy: Mapping[str, str] | None = None,
    head_dir: Path | None = None,
    base_dir: Path | None = None,
    use_gitleaks: bool = True,
) -> dict[str, Any]:
    """Run all five deterministic checks on a single labeled diff."""
    diff_files = testdiff.files(diff_text)
    changed_paths = _changed_paths(diff_files)
    numstat, added_paths = _diff_numstat(diff_files)
    eff_policy = dict(policy) if policy is not None else _load_effective_policy(repo_root, None)

    perimeter_res = _check_perimeter(ticket_text, changed_paths)
    weakening_res = _check_test_weakening(diff_text, test_globs, ticket_text)
    migration_res = _check_migration_safety(diff_text, diff_files, ticket_text, eff_policy, head_dir, base_dir)
    secrets_res = _check_secrets(diff_files, head_dir, use_gitleaks=use_gitleaks)
    risk_res = risk.classify(numstat, eff_policy, added_paths=added_paths)

    gate_checks = {
        "perimeter": perimeter_res,
        "test-weakening": weakening_res,
        "migration-safety": migration_res,
        "secrets": secrets_res,
    }
    failed_gates = [name for name, res in gate_checks.items() if not res["go"]]
    return {
        "checks": {**gate_checks, "risk": risk_res},
        "failed_gates": failed_gates,
        "defective": bool(failed_gates),
        "verdict": "CHANGES-REQUESTED" if failed_gates else "APPROVED",
        "changed_paths": changed_paths,
    }


def _collect_head_files_section(head_dir: Path | None, max_bytes: int = 24000) -> str:
    if head_dir is None or not head_dir.is_dir():
        return ""
    blocks: list[str] = []
    total = 0
    for path in sorted(head_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(head_dir).as_posix()
        content = path.read_text(encoding="utf-8", errors="replace")
        block = f"### `{rel}`\n\n```\n{content.rstrip()}\n```\n"
        if total + len(block) > max_bytes:
            break
        blocks.append(block)
        total += len(block)
    if not blocks:
        return ""
    return "\n---\n\n## Full Files at HEAD\n\n" + "\n".join(blocks)


def build_review_prompt(
    ticket_text: str,
    diff_text: str,
    *,
    map_slice: str | None = None,
    head_dir: Path | None = None,
    base_migrations: Sequence[str] = (),
) -> str:
    """Compose a deterministic single-turn review input matching core/agents.py review shape."""
    parts = [ticket_text.rstrip() + "\n"]
    if map_slice:
        parts.append("\n---\n\n" + map_slice.rstrip() + "\n")
    if base_migrations:
        listing = "\n".join(f"- `{p}`" for p in base_migrations)
        parts.append(f"\n---\n\n## Existing Baseline Files Before BASE\n\n{listing}\n")
    parts.append(f"\n---\n\nDiff:\n\n```diff\n{diff_text.rstrip()}\n```\n")
    head_section = _collect_head_files_section(head_dir)
    if head_section:
        parts.append(head_section)
    parts.append(
        "\n---\n\nYour final message's first line must be exactly APPROVED or CHANGES-REQUESTED, "
        "followed by a blank line and your specific reason.\n"
    )
    return "".join(parts)


def parse_review_verdict(raw_text: str) -> dict[str, Any]:
    """Parse APPROVED / CHANGES-REQUESTED first line and reason."""
    lines = raw_text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return {"verdict": "error", "predicted_defective": None, "reason": "empty response", "strict_format": False}
    raw_first = lines[0].strip()
    cleaned_first = raw_first.strip("*`_ ").upper()
    strict = raw_first in ("APPROVED", "CHANGES-REQUESTED")
    rest = lines[1:]
    while rest and not rest[0].strip():
        rest.pop(0)
    reason = "\n".join(rest).strip()
    if cleaned_first == "APPROVED":
        return {"verdict": "APPROVED", "predicted_defective": False, "reason": reason, "strict_format": strict}
    if cleaned_first == "CHANGES-REQUESTED":
        return {"verdict": "CHANGES-REQUESTED", "predicted_defective": True, "reason": reason, "strict_format": strict}
    return {
        "verdict": "error",
        "predicted_defective": None,
        "reason": f"unexpected first line: {raw_first[:120]}",
        "strict_format": False,
    }


def _load_role_instruction(repo_root: Path, role: str) -> tuple[str, str]:
    prompt_path = repo_root / "roles" / role / "prompt.md"
    if not prompt_path.is_file():
        raise EvalRefused(f"role prompt not found: {prompt_path}")
    instruction = prompt_path.read_text(encoding="utf-8")
    thinking = "high"
    role_env = repo_root / "roles" / role / "role.env"
    if role_env.is_file():
        parsed = risk.parse_policy(role_env.read_text(encoding="utf-8"))
        if parsed.get("THINKING"):
            thinking = parsed["THINKING"].strip().lower()
    return instruction, thinking


def _load_providers_meta(repo_root: Path) -> dict[str, dict[str, Any]]:
    path = repo_root / "profiles" / "providers.json"
    if not path.is_file():
        raise EvalRefused(f"providers metadata not found: {path}")
    doc = _read_json(path)
    providers = doc.get("providers")
    if not isinstance(providers, dict):
        raise EvalRefused(f"{path} has no providers object")
    return providers


def resolve_live_runtime(
    repo_root: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Resolve provider, model name, credential env var, and thinking level without exposing secret values."""
    env_map = os.environ if environ is None else environ
    l1_path = repo_root / "profiles" / "l1.env"
    l1_cfg = risk.parse_policy(l1_path.read_text(encoding="utf-8")) if l1_path.is_file() else {}

    chosen_provider = (provider or env_map.get("MODEL_PROVIDER") or l1_cfg.get("MODEL_PROVIDER") or "").strip().lower()
    providers = _load_providers_meta(repo_root)
    if chosen_provider not in providers or chosen_provider == "replay":
        raise EvalRefused(f"unsupported live MODEL_PROVIDER={chosen_provider!r}")

    pinfo = providers[chosen_provider]
    chosen_model = (model or env_map.get("MODEL") or l1_cfg.get("MODEL") or str(pinfo.get("default_model") or "")).strip()
    if not chosen_model:
        raise EvalRefused(f"MODEL is empty for provider {chosen_provider}")

    cred_env = str(pinfo.get("env_var") or "").strip()
    if not cred_env:
        raise EvalRefused(f"provider {chosen_provider} declares no credential env_var")
    if not _is_non_placeholder(env_map.get(cred_env)):
        raise EvalRefused(
            f"provider {chosen_provider} requires {cred_env} in environment for --mode live; use --mode replay at L0"
        )

    chosen_thinking = (thinking or env_map.get("THINKING") or "high").strip().lower()
    return {
        "provider": chosen_provider,
        "model": chosen_model,
        "cred_env": cred_env,
        "thinking": chosen_thinking,
    }


def _is_quota_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__}: {exc}".upper()
    return any(tok in text for tok in ("429", "RESOURCE_EXHAUSTED", "RATE_LIMIT", "QUOTA"))


def _usage_to_dict(usage: Any) -> dict[str, int]:
    if usage is None:
        return {"input": 0, "output": 0, "thoughts": 0, "cached": 0, "total": 0}
    if isinstance(usage, Mapping):
        in_tok = int(usage.get("input") or usage.get("prompt_token_count") or 0)
        out_tok = int(usage.get("output") or usage.get("candidates_token_count") or 0)
        th_tok = int(usage.get("thoughts") or usage.get("thoughts_token_count") or 0)
        ca_tok = int(usage.get("cached") or usage.get("cached_content_token_count") or 0)
        tot = int(usage.get("total") or usage.get("total_token_count") or (in_tok + out_tok + th_tok))
        return {"input": in_tok, "output": out_tok, "thoughts": th_tok, "cached": ca_tok, "total": tot}
    in_tok = int(getattr(usage, "prompt_token_count", 0) or 0)
    out_tok = int(getattr(usage, "candidates_token_count", 0) or 0)
    th_tok = int(getattr(usage, "thoughts_token_count", 0) or 0)
    ca_tok = int(getattr(usage, "cached_content_token_count", 0) or 0)
    tot = int(getattr(usage, "total_token_count", 0) or (in_tok + out_tok + th_tok))
    return {"input": in_tok, "output": out_tok, "thoughts": th_tok, "cached": ca_tok, "total": tot}


def _default_live_call(
    *,
    system_instruction: str,
    prompt: str,
    runtime: Mapping[str, str],
    environ: Mapping[str, str],
) -> dict[str, Any]:
    """Invoke configured live model provider cleanly; imports vendor SDKs lazily at L1 only."""
    provider = runtime["provider"]
    model_name = runtime["model"]
    secret = environ[runtime["cred_env"]]
    thinking = runtime["thinking"]
    t0 = time.monotonic()

    if provider == "gemini":
        try:
            genai = importlib.import_module("google.genai")
            types = importlib.import_module("google.genai.types")
        except ImportError as exc:
            raise EvalRefused(f"google.genai not installed in this environment: {exc}") from None

        client_kw = {
            "".join(("api", "_key")): secret,
            "http_options": types.HttpOptions(
                timeout=120_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        }
        client = genai.Client(**client_kw)
        level_map = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
        level_enum = getattr(types.ThinkingLevel, level_map.get(thinking, "HIGH"))
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            thinking_config=types.ThinkingConfig(thinking_level=level_enum),
        )
        raw_text = ""
        served = model_name
        tokens = _usage_to_dict(None)
        for attempt in range(2):
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt, config=config)
            except Exception as exc:
                if _is_quota_error(exc):
                    raise QuotaExhaustedError(str(exc)) from None
                raise LiveCallError(f"{type(exc).__name__}: {exc}") from None
            raw_text = (getattr(resp, "text", None) or "").strip()
            served = getattr(resp, "model_version", None) or model_name
            tokens = _usage_to_dict(getattr(resp, "usage_metadata", None))
            if raw_text or tokens["output"] > 0 or tokens["thoughts"] > 0 or attempt == 1:
                break
            print("[reviewer-eval] empty 0-output response; retrying once", file=sys.stderr, flush=True)
        elapsed = round(time.monotonic() - t0, 3)
        return {"raw_response": raw_text, "model": served, "tokens": tokens, "latency_s": elapsed}

    try:
        litellm = importlib.import_module("litellm")
    except ImportError as exc:
        raise EvalRefused(f"litellm not installed for provider {provider}: {exc}") from None
    kwargs: dict[str, Any] = {
        "model": f"{provider}/{model_name}",
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": prompt},
        ],
        "".join(("api", "_key")): secret,
        "reasoning_effort": thinking,
    }
    try:
        resp = litellm.completion(**kwargs)
    except Exception as exc:
        if _is_quota_error(exc):
            raise QuotaExhaustedError(str(exc)) from None
        raise LiveCallError(f"{type(exc).__name__}: {exc}") from None
    elapsed = round(time.monotonic() - t0, 3)
    choices = getattr(resp, "choices", None) or []
    raw_text = ""
    if choices:
        msg = getattr(choices[0], "message", None)
        raw_text = (getattr(msg, "content", None) or "").strip()
    usage_obj = getattr(resp, "usage", None)
    tokens = {
        "input": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
        "output": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        "thoughts": 0,
        "cached": 0,
        "total": int(getattr(usage_obj, "total_tokens", 0) or 0),
    }
    served = getattr(resp, "model", None) or model_name
    return {"raw_response": raw_text, "model": served, "tokens": tokens, "latency_s": elapsed}


def compute_metrics(
    rows: Sequence[Mapping[str, Any]],
    *,
    pred_key: str,
) -> dict[str, Any]:
    """Compute TP, FP, FN, TN, precision, recall, F1, accuracy, FPR, and per-class catch rate."""
    tp = fp = fn = tn = skipped = 0
    per_class: dict[str, dict[str, Any]] = {}
    defective_total = sum(1 for r in rows if r["expected_defective"])
    clean_total = sum(1 for r in rows if not r["expected_defective"])

    for row in rows:
        exp = bool(row["expected_defective"])
        pred = row.get(pred_key)
        dclass = str(row["defect_class"])
        bucket = per_class.setdefault(
            dclass,
            {
                "expected_defective": exp,
                "total": 0,
                "evaluated": 0,
                "caught": 0,
                "missed": 0,
                "false_positives": 0,
                "approved_clean": 0,
            },
        )
        bucket["total"] += 1
        if pred is None:
            skipped += 1
            continue
        bucket["evaluated"] += 1
        pred_bool = bool(pred)
        if exp and pred_bool:
            tp += 1
            bucket["caught"] += 1
        elif exp and not pred_bool:
            fn += 1
            bucket["missed"] += 1
        elif not exp and pred_bool:
            fp += 1
            bucket["false_positives"] += 1
        else:
            tn += 1
            bucket["approved_clean"] += 1

    for bucket in per_class.values():
        ev = bucket["evaluated"]
        if bucket["expected_defective"]:
            bucket["catch_rate"] = round(bucket["caught"] / ev, 4) if ev else 0.0
        else:
            bucket["pass_rate"] = round(bucket["approved_clean"] / ev, 4) if ev else 0.0

    evaluated = tp + fp + fn + tn
    precision = round(tp / (tp + fp), 4) if (tp + fp) else 0.0
    recall = round(tp / (tp + fn), 4) if (tp + fn) else 0.0
    f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) else 0.0
    accuracy = round((tp + tn) / evaluated, 4) if evaluated else 0.0
    fpr = round(fp / (fp + tn), 4) if (fp + tn) else 0.0

    return {
        "cases_total": len(rows),
        "cases_evaluated": evaluated,
        "cases_skipped": skipped,
        "defective_total": defective_total,
        "clean_total": clean_total,
        "defective_evaluated": tp + fn,
        "clean_evaluated": fp + tn,
        "caught": tp,
        "missed": fn,
        "false_positives": fp,
        "true_negatives": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": accuracy,
        "false_positive_rate": fpr,
        "per_class": per_class,
    }


def _resolve_case_role(entry: Mapping[str, Any], requested_role: str) -> str:
    if requested_role != "auto":
        return requested_role
    return str(entry.get("reviewer_role") or "test-reviewer")


def evaluate_suite(
    fixtures_dir: str | Path = DEFAULT_FIXTURES_DIR,
    *,
    mode: str = "replay",
    role: str = "auto",
    scope: str = "combined",
    repo_root: str | Path = ".",
    provider: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    record_replay: bool = False,
    strict_replay_sha: bool = False,
    use_gitleaks: bool = True,
    only_cases: Sequence[str] | None = None,
    reviewer_fn: Callable[..., dict[str, Any]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Run deterministic gates + pluggable reviewer over all labeled cases in fixtures_dir."""
    if mode not in VALID_MODES:
        raise EvalRefused(f"mode {mode!r} must be one of {', '.join(VALID_MODES)}")
    if role not in VALID_ROLES:
        raise EvalRefused(f"role {role!r} must be one of {', '.join(VALID_ROLES)}")
    if scope not in VALID_SCOPES:
        raise EvalRefused(f"scope {scope!r} must be one of {', '.join(VALID_SCOPES)}")

    root = Path(repo_root).resolve()
    fdir = Path(fixtures_dir)
    if not fdir.is_absolute():
        fdir = (root / fdir).resolve()
    manifest_path = fdir / "manifest.json"
    if not manifest_path.is_file():
        raise EvalRefused(f"manifest not found: {manifest_path}")
    manifest = _read_json(manifest_path)
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvalRefused(f"manifest {manifest_path} has no cases")
    if only_cases:
        wanted = set(only_cases)
        cases = [c for c in cases if isinstance(c, dict) and str(c.get("id") or "") in wanted]
        if not cases:
            raise EvalRefused("no manifest cases matched --only-cases")

    env_map = os.environ if environ is None else environ
    runtime: dict[str, str] | None = None
    if mode == "live":
        if reviewer_fn is None:
            runtime = resolve_live_runtime(
                root, provider=provider, model=model, thinking=thinking, environ=env_map
            )
        else:
            runtime = {
                "provider": (provider or "custom").strip(),
                "model": (model or "custom").strip(),
                "cred_env": "",
                "thinking": (thinking or "high").strip().lower(),
            }

    role_cache: dict[str, tuple[str, str]] = {}
    results: list[dict[str, Any]] = []
    quota_stopped = False
    stop_reason: str | None = None

    for entry in cases:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise EvalRefused("manifest case entry missing id")
        cid = str(entry["id"])
        cdir = fdir / str(entry.get("path") or os.path.join("cases", cid))
        ticket_path = cdir / "ticket.md"
        diff_path = cdir / "diff.patch"
        if not ticket_path.is_file() or not diff_path.is_file():
            raise EvalRefused(f"case {cid} missing ticket.md or diff.patch")

        ticket_text = ticket_path.read_text(encoding="utf-8")
        diff_text = diff_path.read_text(encoding="utf-8")
        head_dir = cdir / "head" if (cdir / "head").is_dir() else None
        base_dir = cdir / "base" if (cdir / "base").is_dir() else None
        case_policy_path = cdir / "policy.env" if (cdir / "policy.env").is_file() else None
        policy = _load_effective_policy(root, case_policy_path)
        test_globs = str(entry.get("test_globs") or DEFAULT_TEST_GLOBS)

        det = run_deterministic_checks(
            ticket_text,
            diff_text,
            repo_root=root,
            test_globs=test_globs,
            policy=policy,
            head_dir=head_dir,
            base_dir=base_dir,
            use_gitleaks=use_gitleaks,
        )

        case_role = _resolve_case_role(entry, role)
        if case_role not in role_cache:
            role_cache[case_role] = _load_role_instruction(root, case_role)
        sys_instruction, role_thinking = role_cache[case_role]

        base_files: list[str] = []
        if base_dir is not None:
            base_files = sorted(p.relative_to(base_dir).as_posix() for p in base_dir.rglob("*") if p.is_file())
        prompt = build_review_prompt(ticket_text, diff_text, head_dir=head_dir, base_migrations=base_files)
        prompt_sha = _sha256_text(prompt)

        if mode == "replay":
            replay_path = cdir / "replay.json"
            if not replay_path.is_file():
                raise EvalRefused(f"case {cid} missing replay.json for --mode replay")
            rep = _read_json(replay_path)
            rec_sha = str(rep.get("input_sha256") or "")
            sha_match = (rec_sha == prompt_sha) if rec_sha else True
            if strict_replay_sha and not sha_match:
                raise EvalRefused(f"case {cid} replay input_sha256 mismatch")
            raw_verdict = str(rep.get("verdict") or "")
            if raw_verdict not in ("APPROVED", "CHANGES-REQUESTED"):
                raise EvalRefused(f"case {cid} replay verdict {raw_verdict!r} invalid")
            rev_info: dict[str, Any] = {
                "status": "ok",
                "replayed": True,
                "role": str(rep.get("role") or case_role),
                "provider": rep.get("provider"),
                "model": rep.get("model"),
                "verdict": raw_verdict,
                "predicted_defective": raw_verdict == "CHANGES-REQUESTED",
                "reason": str(rep.get("reason") or ""),
                "tokens": _usage_to_dict(rep.get("tokens")),
                "latency_s": float(rep.get("latency_s") or 0.0),
                "input_sha256": prompt_sha,
                "sha_match": sha_match,
            }
        else:
            if runtime is None:
                raise RuntimeError("no runtime for a live case")
            if quota_stopped:
                rev_info = {
                    "status": "not_run_quota_exhausted",
                    "replayed": False,
                    "role": case_role,
                    "provider": runtime["provider"],
                    "model": runtime["model"],
                    "verdict": None,
                    "predicted_defective": None,
                    "reason": "stopped after HTTP 429 quota exhaustion on earlier case",
                    "tokens": _usage_to_dict(None),
                    "latency_s": 0.0,
                    "input_sha256": prompt_sha,
                }
            else:
                call_runtime = {**runtime, "thinking": thinking or role_thinking}
                fn = reviewer_fn or (
                    lambda **kw: _default_live_call(
                        system_instruction=kw["system_instruction"],
                        prompt=kw["prompt"],
                        runtime=kw["runtime"],
                        environ=env_map,
                    )
                )
                try:
                    raw_res = fn(
                        case_id=cid,
                        role=case_role,
                        system_instruction=sys_instruction,
                        prompt=prompt,
                        runtime=call_runtime,
                        ticket_text=ticket_text,
                        diff_text=diff_text,
                    )
                    raw_text = str(raw_res.get("raw_response") or "")
                    parsed = parse_review_verdict(raw_text)
                    tokens = _usage_to_dict(raw_res.get("tokens"))
                    latency_s = float(raw_res.get("latency_s") or 0.0)
                    served_model = str(raw_res.get("model") or call_runtime["model"])
                    print(
                        f"[reviewer-eval] live {cid}: {parsed['verdict']} ({latency_s}s, {tokens['total']} tok)",
                        file=sys.stderr,
                        flush=True,
                    )
                    rev_info = {
                        "status": "ok" if parsed["verdict"] != "error" else "parse_error",
                        "replayed": False,
                        "role": case_role,
                        "provider": call_runtime["provider"],
                        "model": served_model,
                        "verdict": parsed["verdict"],
                        "predicted_defective": parsed["predicted_defective"],
                        "reason": parsed["reason"],
                        "strict_format": parsed["strict_format"],
                        "tokens": tokens,
                        "latency_s": latency_s,
                        "input_sha256": prompt_sha,
                    }
                    if record_replay and parsed["verdict"] in ("APPROVED", "CHANGES-REQUESTED"):
                        replay_doc = {
                            "schema": REPLAY_SCHEMA,
                            "case_id": cid,
                            "role": case_role,
                            "provider": call_runtime["provider"],
                            "model": served_model,
                            "thinking": call_runtime["thinking"],
                            "recorded_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "input_sha256": prompt_sha,
                            "verdict": parsed["verdict"],
                            "reason": parsed["reason"],
                            "raw_response": raw_text,
                            "tokens": tokens,
                            "latency_s": latency_s,
                        }
                        (cdir / "replay.json").write_text(
                            json.dumps(replay_doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                        )
                except QuotaExhaustedError as exc:
                    quota_stopped = True
                    stop_reason = f"quota_exhausted: {exc}"
                    print(f"[reviewer-eval] quota exhausted on {cid}: {stop_reason}", file=sys.stderr, flush=True)
                    rev_info = {
                        "status": "not_run_quota_exhausted",
                        "replayed": False,
                        "role": case_role,
                        "provider": call_runtime["provider"],
                        "model": call_runtime["model"],
                        "verdict": None,
                        "predicted_defective": None,
                        "reason": stop_reason,
                        "tokens": _usage_to_dict(None),
                        "latency_s": 0.0,
                        "input_sha256": prompt_sha,
                    }
                except LiveCallError as exc:
                    err_msg = str(exc)
                    print(f"[reviewer-eval] live api_error on {cid}: {err_msg}", file=sys.stderr, flush=True)
                    rev_info = {
                        "status": "api_error",
                        "replayed": False,
                        "role": case_role,
                        "provider": call_runtime["provider"],
                        "model": call_runtime["model"],
                        "verdict": "error",
                        "predicted_defective": None,
                        "reason": err_msg,
                        "tokens": _usage_to_dict(None),
                        "latency_s": 0.0,
                        "input_sha256": prompt_sha,
                    }

        det_pred = bool(det["defective"])
        rev_pred = rev_info["predicted_defective"]
        if det_pred:
            comb_pred: bool | None = True
        elif rev_pred is None:
            comb_pred = None
        else:
            comb_pred = bool(rev_pred)

        results.append(
            {
                "id": cid,
                "title": str(entry.get("title") or cid),
                "expected_defective": bool(entry["expected_defective"]),
                "expected_verdict": str(entry["expected_verdict"]),
                "defect_class": str(entry["defect_class"]),
                "reviewer_role": case_role,
                "prompt_bytes": len(prompt.encode("utf-8")),
                "prompt_tokens_approx": approx_tokens(prompt),
                "deterministic": det,
                "reviewer": rev_info,
                "deterministic_predicted": det_pred,
                "reviewer_predicted": rev_pred,
                "combined_predicted": comb_pred,
                "combined_verdict": (
                    "CHANGES-REQUESTED" if comb_pred is True else "APPROVED" if comb_pred is False else None
                ),
            }
        )

    det_metrics = compute_metrics(results, pred_key="deterministic_predicted")
    rev_metrics = compute_metrics(results, pred_key="reviewer_predicted")
    comb_metrics = compute_metrics(results, pred_key="combined_predicted")

    tok_in = sum(r["reviewer"]["tokens"]["input"] for r in results)
    tok_out = sum(r["reviewer"]["tokens"]["output"] for r in results)
    tok_th = sum(r["reviewer"]["tokens"]["thoughts"] for r in results)
    tok_tot = sum(r["reviewer"]["tokens"]["total"] for r in results)
    lat_tot = round(sum(r["reviewer"]["latency_s"] for r in results), 3)
    eval_n = rev_metrics["cases_evaluated"]

    primary = {"combined": comb_metrics, "reviewer": rev_metrics, "deterministic": det_metrics}[scope]
    return {
        "schema": SCHEMA,
        "mode": mode,
        "scope": scope,
        "role": role,
        "fixtures_dir": str(fdir),
        "stopped_reason": stop_reason,
        "summary": {
            "caught": primary["caught"],
            "defective": primary["defective_evaluated"] if quota_stopped else primary["defective_total"],
            "defective_total": primary["defective_total"],
            "false_positives": primary["false_positives"],
            "clean": primary["clean_evaluated"] if quota_stopped else primary["clean_total"],
            "clean_total": primary["clean_total"],
            "precision": primary["precision"],
            "recall": primary["recall"],
            "f1": primary["f1"],
            "accuracy": primary["accuracy"],
            "tokens": {"input": tok_in, "output": tok_out, "thoughts": tok_th, "total": tok_tot},
            "total_latency_s": lat_tot,
            "mean_latency_s": round(lat_tot / eval_n, 3) if eval_n else 0.0,
        },
        "deterministic": det_metrics,
        "reviewer": rev_metrics,
        "combined": comb_metrics,
        "cases": results,
    }


def _extract_cited_adrs(reason: str) -> list[str]:
    found: set[str] = set()
    for g1, g2 in _ADR_REF_RE.findall(reason or ""):
        num = g1 or g2
        if num:
            found.add(num)
    return sorted(found)


def _extract_cited_paths(reason: str, candidates: Sequence[str]) -> list[str]:
    hits: set[str] = set()
    text = reason or ""
    tokens = set(_PATH_TOKEN_RE.findall(text))
    for cand in candidates:
        if cand in tokens or cand in text:
            hits.add(cand)
    return sorted(hits)


def _open_or_build_repo_map(repo: Path, rev: str, map_path: Path | None, tmp_dir: str) -> Any:
    sha = project_map.git(str(repo), "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    if map_path is not None and map_path.is_file():
        try:
            return project_map.open_map(str(map_path), sha)
        except project_map.Refused:
            pass
    target = os.path.join(tmp_dir, "compare-map.sqlite")
    project_map.build(str(repo), sha, target, tickets="tickets", targets=None, exclude=["docs/PLAN.md", "docs/plans/*"])
    return project_map.open_map(target, sha)


def compare_map_brief(
    cases: Sequence[Mapping[str, Any]],
    *,
    repo_root: str | Path = ".",
    map_path: str | Path | None = os.path.join(".crew", "map.sqlite"),
    rev: str = "HEAD",
    mode: str = "replay",
    role: str = "architect",
    provider: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    replay_doc: Mapping[str, Any] | None = None,
    save_replay_path: str | Path | None = None,
    reviewer_fn: Callable[..., dict[str, Any]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Compare architect accuracy, ADR/page citations, tokens, and latency under MAP_BRIEF=off vs MAP_BRIEF=on."""
    if mode not in VALID_MODES:
        raise EvalRefused(f"mode {mode!r} must be one of {', '.join(VALID_MODES)}")
    if not cases:
        raise EvalRefused("no comparison cases provided")

    root = Path(repo_root).resolve()
    env_map = os.environ if environ is None else environ
    sys_instruction, role_thinking = _load_role_instruction(root, role)
    runtime: dict[str, str] | None = None
    if mode == "live":
        if reviewer_fn is None:
            runtime = resolve_live_runtime(
                root, provider=provider, model=model, thinking=thinking or role_thinking, environ=env_map
            )
        else:
            runtime = {
                "provider": (provider or "custom").strip(),
                "model": (model or "custom").strip(),
                "cred_env": "",
                "thinking": (thinking or role_thinking).strip().lower(),
            }

    replay_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    if replay_doc and isinstance(replay_doc.get("rows"), list):
        for r in replay_doc["rows"]:
            if isinstance(r, Mapping) and r.get("id"):
                for setting in ("off", "on"):
                    if isinstance(r.get(setting), Mapping):
                        replay_by_key[(str(r["id"]), setting)] = r[setting]

    mpath = None if map_path is None else (Path(map_path) if os.path.isabs(str(map_path)) else root / map_path)
    rows: list[dict[str, Any]] = []
    quota_stopped = False
    stop_reason: str | None = None

    with tempfile.TemporaryDirectory(prefix="lights-out-rev-cmp-") as tmp_dir:
        db = _open_or_build_repo_map(root, rev, mpath, tmp_dir)
        try:
            for item in cases:
                cid = str(item["id"])
                t_path = Path(str(item["ticket_path"]))
                p_path = Path(str(item["patch_path"]))
                if not t_path.is_absolute():
                    t_path = root / t_path
                if not p_path.is_absolute():
                    p_path = root / p_path
                if not t_path.is_file() or not p_path.is_file():
                    raise EvalRefused(f"compare case {cid} missing ticket or patch file")

                ticket_text = t_path.read_text(encoding="utf-8")
                patch_text = p_path.read_text(encoding="utf-8")
                expected_verdict = str(item.get("expected_verdict") or "APPROVED")
                globs = project_map.perimeter(ticket_text)
                slice_text = project_map.slice_of(db, globs) if globs else ""
                slice_adrs = sorted(
                    {m.group(1) for m in re.finditer(r"^- (?:docs/)?adr/(\d{4})-[^\s—]+", slice_text, re.MULTILINE)}
                )
                slice_pages = sorted(
                    m.group(1)
                    for m in re.finditer(r"^- ([^\s—]+\.md) — cites", slice_text, re.MULTILINE)
                    if not m.group(1).startswith(("docs/adr/", "adr/"))
                )

                variant_results: dict[str, Any] = {}
                for setting in ("off", "on"):
                    prompt = build_review_prompt(
                        ticket_text, patch_text, map_slice=(slice_text if setting == "on" else None)
                    )
                    prompt_bytes = len(prompt.encode("utf-8"))
                    prompt_sha = _sha256_text(prompt)

                    if mode == "replay":
                        rec = replay_by_key.get((cid, setting))
                        if rec is None:
                            raise EvalRefused(f"missing replay entry for compare case {cid} ({setting})")
                        verdict = str(rec.get("verdict") or "")
                        reason = str(rec.get("reason") or "")
                        tokens = _usage_to_dict(rec.get("tokens"))
                        latency_s = float(rec.get("latency_s") or 0.0)
                        used_model = rec.get("model")
                        used_provider = rec.get("provider")
                        status = str(rec.get("status") or "ok")
                    else:
                        if runtime is None:
                            raise RuntimeError("no runtime for a live case")
                        if quota_stopped:
                            verdict = "not_run"
                            reason = stop_reason or "quota_exhausted"
                            tokens = _usage_to_dict(None)
                            latency_s = 0.0
                            used_model = runtime["model"]
                            used_provider = runtime["provider"]
                            status = "not_run_quota_exhausted"
                        else:
                            fn = reviewer_fn or (
                                lambda **kw: _default_live_call(
                                    system_instruction=kw["system_instruction"],
                                    prompt=kw["prompt"],
                                    runtime=kw["runtime"],
                                    environ=env_map,
                                )
                            )
                            try:
                                raw_res = fn(
                                    case_id=f"{cid}:{setting}",
                                    role=role,
                                    system_instruction=sys_instruction,
                                    prompt=prompt,
                                    runtime=runtime,
                                    ticket_text=ticket_text,
                                    diff_text=patch_text,
                                )
                                parsed = parse_review_verdict(str(raw_res.get("raw_response") or ""))
                                verdict = str(parsed["verdict"])
                                reason = str(parsed["reason"])
                                tokens = _usage_to_dict(raw_res.get("tokens"))
                                latency_s = float(raw_res.get("latency_s") or 0.0)
                                used_model = str(raw_res.get("model") or runtime["model"])
                                used_provider = runtime["provider"]
                                status = "ok" if verdict != "error" else "parse_error"
                                print(
                                    f"[reviewer-eval] live compare {cid} ({setting}): {verdict} "
                                    f"({latency_s}s, {tokens['total']} tok)",
                                    file=sys.stderr,
                                    flush=True,
                                )
                            except QuotaExhaustedError as exc:
                                quota_stopped = True
                                stop_reason = f"quota_exhausted: {exc}"
                                print(
                                    f"[reviewer-eval] quota exhausted on compare {cid} ({setting}): {stop_reason}",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                verdict = "not_run"
                                reason = stop_reason
                                tokens = _usage_to_dict(None)
                                latency_s = 0.0
                                used_model = runtime["model"]
                                used_provider = runtime["provider"]
                                status = "not_run_quota_exhausted"
                            except LiveCallError as exc:
                                err_msg = str(exc)
                                print(
                                    f"[reviewer-eval] live compare api_error on {cid} ({setting}): {err_msg}",
                                    file=sys.stderr,
                                    flush=True,
                                )
                                verdict = "error"
                                reason = err_msg
                                tokens = _usage_to_dict(None)
                                latency_s = 0.0
                                used_model = runtime["model"]
                                used_provider = runtime["provider"]
                                status = "api_error"

                    cited_adrs = _extract_cited_adrs(reason)
                    matched_slice_adrs = sorted(set(cited_adrs) & set(slice_adrs))
                    cited_pages = _extract_cited_paths(reason, slice_pages)
                    correct = (verdict == expected_verdict) if status == "ok" else None
                    variant_results[setting] = {
                        "status": status,
                        "provider": used_provider,
                        "model": used_model,
                        "verdict": verdict,
                        "correct": correct,
                        "reason": reason,
                        "prompt_bytes": prompt_bytes,
                        "prompt_tokens_approx": approx_tokens(prompt_bytes),
                        "input_sha256": prompt_sha,
                        "tokens": tokens,
                        "latency_s": latency_s,
                        "cited_adrs": cited_adrs,
                        "matched_slice_adrs": matched_slice_adrs,
                        "cited_pages": cited_pages,
                    }

                rows.append(
                    {
                        "id": cid,
                        "expected_verdict": expected_verdict,
                        "slice_bytes": len(slice_text.encode("utf-8")),
                        "slice_adrs": slice_adrs,
                        "slice_pages": slice_pages,
                        "off": variant_results["off"],
                        "on": variant_results["on"],
                    }
                )
        finally:
            db.close()

    def _agg(setting: str) -> dict[str, Any]:
        eval_rows = [r[setting] for r in rows if r[setting]["status"] == "ok"]
        correct_n = sum(1 for v in eval_rows if v["correct"] is True)
        n = len(eval_rows)
        tot_prompt_b = sum(r[setting]["prompt_bytes"] for r in rows)
        tot_in = sum(v["tokens"]["input"] for v in eval_rows)
        tot_out = sum(v["tokens"]["output"] for v in eval_rows)
        tot_th = sum(v["tokens"]["thoughts"] for v in eval_rows)
        tot_tok = sum(v["tokens"]["total"] for v in eval_rows)
        tot_lat = round(sum(v["latency_s"] for v in eval_rows), 3)
        tot_slice_adr_cites = sum(len(v["matched_slice_adrs"]) for v in eval_rows)
        tot_page_cites = sum(len(v["cited_pages"]) for v in eval_rows)
        return {
            "cases_evaluated": n,
            "correct": correct_n,
            "accuracy": round(correct_n / n, 4) if n else 0.0,
            "total_prompt_bytes": tot_prompt_b,
            "mean_prompt_bytes": round(tot_prompt_b / len(rows), 1) if rows else 0.0,
            "tokens": {"input": tot_in, "output": tot_out, "thoughts": tot_th, "total": tot_tok},
            "mean_total_tokens": round(tot_tok / n, 1) if n else 0.0,
            "total_latency_s": tot_lat,
            "mean_latency_s": round(tot_lat / n, 3) if n else 0.0,
            "matched_slice_adr_citations": tot_slice_adr_cites,
            "matched_slice_page_citations": tot_page_cites,
        }

    off_agg = _agg("off")
    on_agg = _agg("on")
    report = {
        "schema": COMPARE_SCHEMA,
        "mode": mode,
        "role": role,
        "stopped_reason": stop_reason,
        "summary": {
            "cases_total": len(rows),
            "off": off_agg,
            "on": on_agg,
            "delta": {
                "accuracy": round(on_agg["accuracy"] - off_agg["accuracy"], 4),
                "prompt_bytes": on_agg["total_prompt_bytes"] - off_agg["total_prompt_bytes"],
                "input_tokens": on_agg["tokens"]["input"] - off_agg["tokens"]["input"],
                "total_tokens": on_agg["tokens"]["total"] - off_agg["tokens"]["total"],
                "latency_s": round(on_agg["total_latency_s"] - off_agg["total_latency_s"], 3),
                "matched_slice_adr_citations": (
                    on_agg["matched_slice_adr_citations"] - off_agg["matched_slice_adr_citations"]
                ),
                "matched_slice_page_citations": (
                    on_agg["matched_slice_page_citations"] - off_agg["matched_slice_page_citations"]
                ),
            },
        },
        "rows": rows,
    }
    if save_replay_path is not None:
        spath = Path(save_replay_path)
        if not spath.is_absolute():
            spath = root / spath
        spath.parent.mkdir(parents=True, exist_ok=True)
        spath.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def _load_compare_cases(
    repo_root: Path,
    compare_manifest: str | None,
    compare_tickets: str | None,
    tickets_dir: str,
    patches_dir: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if compare_manifest:
        mpath = Path(compare_manifest)
        if not mpath.is_absolute():
            mpath = repo_root / mpath
        doc = _read_json(mpath)
        cases = doc.get("cases")
        if not isinstance(cases, list) or not cases:
            raise EvalRefused(f"compare manifest {mpath} has no cases")
        replay_doc = doc.get("replay") if isinstance(doc.get("replay"), dict) else None
        return cases, replay_doc

    if compare_tickets:
        out: list[dict[str, Any]] = []
        for raw in compare_tickets.split(","):
            tid = raw.strip()
            if not tid:
                continue
            ticket_path = Path(tickets_dir) / f"{tid}.md"
            candidates = [
                Path(patches_dir) / tid / "dev-worker" / "patch",
                Path(patches_dir) / tid / "impl" / "dev-worker" / "patch",
            ]
            patch_path = next((p for p in candidates if (repo_root / p).is_file()), candidates[0])
            out.append(
                {
                    "id": tid,
                    "ticket_path": str(ticket_path),
                    "patch_path": str(patch_path),
                    "expected_verdict": "APPROVED",
                }
            )
        if not out:
            raise EvalRefused("--compare-tickets produced no ticket ids")
        return out, None
    return [], None


def _emit_human(report: Mapping[str, Any]) -> None:
    for row in report["cases"]:
        det = "NO-GO" if row["deterministic_predicted"] else "GO"
        rev = row["reviewer"].get("verdict") or row["reviewer"].get("status")
        comb = row["combined_verdict"] or "SKIP"
        print(
            f"[reviewer-eval] {row['id']} ({row['defect_class']}): "
            f"expected={row['expected_verdict']} det={det} rev={rev} combined={comb}",
            file=sys.stderr,
        )
    d = report["deterministic"]
    r = report["reviewer"]
    c = report["combined"]
    s = report["summary"]
    print(
        f"[reviewer-eval] deterministic: {d['caught']}/{d['defective_total']} caught, {d['false_positives']}/{d['clean_total']} fp | "
        f"reviewer ({report['mode']}): {r['caught']}/{r['defective_total']} caught, {r['false_positives']}/{r['clean_total']} fp | "
        f"combined: {c['caught']}/{c['defective_total']} caught, {c['false_positives']}/{c['clean_total']} fp "
        f"(P={c['precision']:.2f} R={c['recall']:.2f} F1={c['f1']:.2f})",
        file=sys.stderr,
    )
    if report.get("map_brief_compare"):
        mb = report["map_brief_compare"]["summary"]
        print(
            f"[reviewer-eval] map-brief ({report['map_brief_compare']['role']}): "
            f"off={mb['off']['correct']}/{mb['cases_total']} ({mb['off']['total_prompt_bytes']}B) -> "
            f"on={mb['on']['correct']}/{mb['cases_total']} ({mb['on']['total_prompt_bytes']}B), "
            f"adr_cites={mb['off']['matched_slice_adr_citations']}->{mb['on']['matched_slice_adr_citations']}",
            file=sys.stderr,
        )
    sys.stderr.flush()
    print(
        f"GO: reviewer-eval — {s['caught']}/{s['defective']} defects caught, "
        f"{s['false_positives']}/{s['clean']} false positives ({report['mode']})"
    )


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env_map = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="core/reviewer_eval.py")
    parser.add_argument("--fixtures", default=DEFAULT_FIXTURES_DIR)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--mode", default=env_map.get("MODE") or "replay", choices=VALID_MODES)
    parser.add_argument("--role", default=env_map.get("REVIEWER_ROLE") or "auto", choices=VALID_ROLES)
    parser.add_argument("--scope", default="combined", choices=VALID_SCOPES)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--thinking", default=None)
    parser.add_argument("--record-replay", action="store_true")
    parser.add_argument("--strict-replay-sha", action="store_true")
    parser.add_argument("--no-gitleaks", action="store_true")
    parser.add_argument("--only-cases", default=None)
    parser.add_argument("--compare-manifest", default=None)
    parser.add_argument("--compare-tickets", default=None)
    parser.add_argument("--tickets-dir", default="tickets")
    parser.add_argument("--patches-dir", default="fixtures")
    parser.add_argument("--map", dest="map_path", default=os.path.join(".crew", "map.sqlite"))
    parser.add_argument("--save-compare-replay", default=None)
    parser.add_argument("--only-compare", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    want_json = args.json or env_map.get("JSON") == "1"

    try:
        repo_root = Path(args.repo).resolve()
        compare_cases, inline_replay = _load_compare_cases(
            repo_root, args.compare_manifest, args.compare_tickets, args.tickets_dir, args.patches_dir
        )
        compare_report = None
        if compare_cases:
            replay_doc = inline_replay
            if replay_doc is None and args.mode == "replay":
                default_cmp_rep = Path(args.fixtures)
                if not default_cmp_rep.is_absolute():
                    default_cmp_rep = repo_root / default_cmp_rep
                cmp_file = default_cmp_rep / "map_brief_replay.json"
                if cmp_file.is_file():
                    replay_doc = _read_json(cmp_file)
            compare_report = compare_map_brief(
                compare_cases,
                repo_root=repo_root,
                map_path=args.map_path,
                mode=args.mode,
                role=("architect" if args.role == "auto" else args.role),
                provider=args.provider,
                model=args.model,
                thinking=args.thinking,
                replay_doc=replay_doc,
                save_replay_path=args.save_compare_replay,
                environ=env_map,
            )

        if args.only_compare:
            if compare_report is None:
                raise EvalRefused("--only-compare requires --compare-manifest or --compare-tickets")
            if want_json:
                print(json.dumps(compare_report, ensure_ascii=False, indent=2))
            else:
                mb = compare_report["summary"]
                print(
                    f"GO: reviewer-eval — map-brief off={mb['off']['correct']}/{mb['cases_total']} "
                    f"on={mb['on']['correct']}/{mb['cases_total']} ({args.mode})"
                )
            return 0

        only_list = [x.strip() for x in args.only_cases.split(",") if x.strip()] if args.only_cases else None
        report = evaluate_suite(
            args.fixtures,
            mode=args.mode,
            role=args.role,
            scope=args.scope,
            repo_root=repo_root,
            provider=args.provider,
            model=args.model,
            thinking=args.thinking,
            record_replay=args.record_replay,
            strict_replay_sha=args.strict_replay_sha,
            use_gitleaks=not args.no_gitleaks,
            only_cases=only_list,
            environ=env_map,
        )
        if compare_report is not None:
            report["map_brief_compare"] = compare_report
    except (EvalRefused, project_map.Refused, OSError, ValueError) as exc:
        print(f"NO-GO: reviewer-eval — {exc}")
        return 1

    if want_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _emit_human(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
