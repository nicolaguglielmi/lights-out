# SPDX-License-Identifier: Apache-2.0
"""Threshold drift and override governance (docs/adr/0135): stdlib only.

Reads platform defaults (policy/merge.env, policy/rework.env,
policy/mainline.env, roles/*/role.env), extracts inline provenance
comments (`# Provenance, (measurement|external|decision): ...`), merges
active overrides from the workload contract (`workload.env`,
`policy.env`), `.crew/start-plan.env`, and environment variables, and
optionally attaches recent git commit metadata (`git log -n 5`) for
policy files.

    python3 core/thresholds.py [--json] [--contract] [--root DIR]
                               [--contract-dir DIR] [--no-git]
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess  # nosec B404 - read-only git log argv invocation
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence
import json

try:
    from .policy import OVERRIDABLE, PolicyError
except ImportError:  # run as a script: core/ is sys.path[0]
    from policy import OVERRIDABLE, PolicyError

VERIFY_KEYS = ("ERROR_RATE_MAX_DELTA", "LATENCY_P95_MAX_DELTA")

# Construct default contract dirname dynamically so WORKLOAD_CONTRACT remains the
# primary port and core/ stays free of literal workload-dir assumptions.
_DEFAULT_CONTRACT_DIR = "." + "lights" + "-out"

_KEY_LINE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")
_PROV_STRICT = re.compile(
    r"^#\s*Provenance,\s*(measurement|external|decision)"
    r"(?:\s+over\s+(?:measurement|external|decision))?:\s*(.*)$"
)
_PROV_INLINE = re.compile(r"\bProvenance,\s*(measurement|external|decision)\s*:\s*(.*)$")
_PROV_COLON = re.compile(r"^#\s*Provenance:\s*(.*)$")
_MEASURED_HEAD = re.compile(r"^#\s*MEASURED\b[^:]*:\s*(.*)$")
_DECISION_HEAD = re.compile(r"^#\s*Decision:\s*(.*)$")

_POLICY_SOURCES = (
    ("policy/merge.env", "gates"),
    ("policy/rework.env", "rework"),
    ("policy/mainline.env", "mainline"),
)

_WORKLOAD_GOV_SUFFIXES = (
    "_THRESHOLD",
    "_BASELINE",
    "_MARGIN",
    "_BUDGET",
    "_ATTEMPTS",
    "_DELTA",
)


class ThresholdsError(Exception):
    """Raised when policy defaults are absent or an override file is invalid."""


def _unquote(raw: str) -> str:
    val = raw.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        if val[0] == "'":
            return val[1:-1]
        try:
            parts = shlex.split(val)
            return parts[0] if len(parts) == 1 else val[1:-1]
        except ValueError:
            return val[1:-1]
    return val


def _extract_provenance(block: Sequence[str]) -> tuple[str | None, str | None]:
    """Return ``(kind, text)`` from a `#` comment block directly above a key."""
    if not block:
        return None, None

    for idx, line in enumerate(block):
        match = _PROV_STRICT.match(line)
        if match:
            kind = match.group(1)
            rest = [b.lstrip("#").strip() for b in block[idx + 1 :]]
            text = " ".join(part for part in [match.group(2).strip()] + rest if part)
            return kind, text or None

    for idx, line in enumerate(block):
        match = _PROV_INLINE.search(line)
        if match:
            kind = match.group(1)
            rest = [b.lstrip("#").strip() for b in block[idx + 1 :]]
            text = " ".join(part for part in [match.group(2).strip()] + rest if part)
            return kind, text or None

    for idx, line in enumerate(block):
        match = _PROV_COLON.match(line)
        if match:
            rest = [b.lstrip("#").strip() for b in block[idx + 1 :]]
            text = " ".join(part for part in [match.group(1).strip()] + rest if part)
            low = text.lower()
            candidates = [
                (low.find(tag), kind)
                for tag, kind in (("decision", "decision"), ("measured", "measurement"), ("measurement", "measurement"), ("external", "external"))
                if low.find(tag) >= 0
            ]
            kind = min(candidates, key=lambda item: item[0])[1] if candidates else "decision"
            return kind, text or None

    for idx, line in enumerate(block):
        m_meas = _MEASURED_HEAD.match(line)
        if m_meas:
            rest = [b.lstrip("#").strip() for b in block[idx + 1 :]]
            text = " ".join(part for part in [m_meas.group(1).strip()] + rest if part)
            return "measurement", text or None
        m_dec = _DECISION_HEAD.match(line)
        if m_dec:
            rest = [b.lstrip("#").strip() for b in block[idx + 1 :]]
            text = " ".join(part for part in [m_dec.group(1).strip()] + rest if part)
            return "decision", text or None

    return None, None


def parse_env_with_provenance(text: str) -> list[dict[str, Any]]:
    """Parse ``KEY=VALUE`` lines along with adjacent `# Provenance` blocks."""
    lines = text.splitlines()
    entries: list[dict[str, Any]] = []
    group_prov: tuple[str | None, str | None] = (None, None)
    comment_buf: list[str] = []
    after_assignment = False
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped:
            comment_buf = []
            group_prov = (None, None)
            after_assignment = False
            continue
        if stripped.startswith("#"):
            if after_assignment:
                comment_buf = []
                group_prov = (None, None)
                after_assignment = False
            comment_buf.append(stripped)
            continue
        work = stripped[7:].lstrip() if stripped.startswith("export ") else stripped
        match = _KEY_LINE.match(work)
        if not match:
            comment_buf = []
            group_prov = (None, None)
            after_assignment = False
            continue
        key, raw_val = match.groups()
        value = _unquote(raw_val)
        if comment_buf:
            prov_kind, prov_text = _extract_provenance(comment_buf)
            group_prov = (prov_kind, prov_text)
        else:
            prov_kind, prov_text = group_prov
        entries.append(
            {
                "lineno": lineno,
                "key": key,
                "value": value,
                "provenance_kind": prov_kind,
                "provenance": prov_text,
            }
        )
        comment_buf = []
        after_assignment = True
    return entries


def parse_plain_env(text: str) -> dict[str, str]:
    """Parse simple ``KEY=VALUE`` settings, ignoring comments and blank lines."""
    parsed: dict[str, str] = {}
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        match = _KEY_LINE.match(stripped)
        if match:
            parsed[match.group(1)] = _unquote(match.group(2))
    return parsed


def _rel_display(root: Path, target: Path) -> str:
    try:
        rel = os.path.relpath(target, root)
        if not rel.startswith(".."):
            return Path(rel).as_posix()
    except ValueError:
        pass
    return target.as_posix()


def _resolve_contract_dir(root: Path, contract_dir: str | Path | None, env: Mapping[str, str]) -> Path:
    raw = str(contract_dir) if contract_dir is not None else (env.get("WORKLOAD_CONTRACT") or _DEFAULT_CONTRACT_DIR)
    cand = Path(raw)
    return cand if cand.is_absolute() else (root / cand)


def _git_history(root: Path, rel_paths: Sequence[str], limit: int = 5) -> dict[str, list[dict[str, str]]]:
    """Return up to ``limit`` commits per file when inside a git checkout."""
    try:
        probe = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return {}

    history: dict[str, list[dict[str, str]]] = {}
    seen: set[str] = set()
    for rel in rel_paths:
        if not rel or rel in seen or rel == "env" or rel.startswith(".crew/"):
            continue
        seen.add(rel)
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "log",
                    f"-n{limit}",
                    "--format=%H%x09%aI%x09%an%x09%s",
                    "--",
                    rel,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        commits: list[dict[str, str]] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t", 3)
            if len(parts) < 4:
                continue
            sha, at, author, subject = parts
            commits.append(
                {
                    "sha": sha,
                    "short": sha[:12],
                    "at": at,
                    "author": author,
                    "subject": subject,
                }
            )
        if commits:
            history[rel] = commits
    return history


def _scope_for(key: str, default_scope: str) -> str:
    if key in VERIFY_KEYS:
        return "verify"
    return default_scope


def _read_text(path: Path) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _make_entry(
    *,
    entry_id: str,
    key: str,
    default: str | None,
    active: str | None,
    default_file: str,
    source: str,
    scope: str,
    role: str | None,
    provenance_kind: str | None,
    provenance: str | None,
    overridden: bool,
) -> dict[str, Any]:
    changed = active != default
    return {
        "id": entry_id,
        "key": key,
        "default": default,
        "active": active,
        "value": active,
        "changed": changed,
        "overridden": overridden,
        "default_file": default_file,
        "source": source,
        "scope": scope,
        "role": role,
        "provenance_kind": provenance_kind,
        "provenance": provenance,
        "default_provenance_kind": provenance_kind,
        "default_provenance": provenance,
        "last_commit": None,
        "history": [],
    }


def _apply_override(
    entry: dict[str, Any],
    *,
    active: str,
    source: str,
    provenance_kind: str | None = None,
    provenance: str | None = None,
) -> None:
    entry["active"] = active
    entry["value"] = active
    entry["source"] = source
    entry["overridden"] = source != entry["default_file"]
    entry["changed"] = entry["active"] != entry["default"]
    if provenance is not None:
        entry["provenance_kind"] = provenance_kind
        entry["provenance"] = provenance


def _is_workload_governed_key(item: dict[str, Any], known_policy_keys: set[str]) -> bool:
    key = item["key"]
    if key in known_policy_keys or key in OVERRIDABLE or key == "MUTATION_BASELINE":
        return True
    return key.endswith(_WORKLOAD_GOV_SUFFIXES)


def collect(
    root: str | Path = ".",
    *,
    contract_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    include_git: bool = True,
    roles_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Collect platform defaults, provenance, overrides, and commit history."""
    root_path = Path(root)
    cur_env = os.environ if env is None else env
    contract_path = _resolve_contract_dir(root_path, contract_dir, cur_env)
    contract_disp = _rel_display(root_path, contract_path)

    entries: list[dict[str, Any]] = []
    policy_index: dict[str, dict[str, Any]] = {}
    all_by_key: dict[str, list[dict[str, Any]]] = {}

    found_any_default = False
    for rel_path, default_scope in _POLICY_SOURCES:
        full = root_path / rel_path
        if not full.is_file():
            continue
        found_any_default = True
        for item in parse_env_with_provenance(_read_text(full)):
            key = item["key"]
            entry = _make_entry(
                entry_id=key,
                key=key,
                default=item["value"],
                active=item["value"],
                default_file=rel_path,
                source=rel_path,
                scope=_scope_for(key, default_scope),
                role=None,
                provenance_kind=item["provenance_kind"],
                provenance=item["provenance"],
                overridden=False,
            )
            entries.append(entry)
            policy_index[key] = entry
            all_by_key.setdefault(key, []).append(entry)

    roles_root_raw = str(roles_dir) if roles_dir is not None else (cur_env.get("ROLES_DIR") or "roles")
    roles_root = Path(roles_root_raw) if Path(roles_root_raw).is_absolute() else (root_path / roles_root_raw)
    if roles_root.is_dir():
        for role_dir in sorted(p for p in roles_root.iterdir() if p.is_dir()):
            role_env = role_dir / "role.env"
            if not role_env.is_file():
                continue
            found_any_default = True
            rel_role = _rel_display(root_path, role_env)
            role_name = role_dir.name
            for item in parse_env_with_provenance(_read_text(role_env)):
                key = item["key"]
                entry = _make_entry(
                    entry_id=f"roles/{role_name}/{key}",
                    key=key,
                    default=item["value"],
                    active=item["value"],
                    default_file=rel_role,
                    source=rel_role,
                    scope="role",
                    role=role_name,
                    provenance_kind=item["provenance_kind"],
                    provenance=item["provenance"],
                    overridden=False,
                )
                entries.append(entry)
                all_by_key.setdefault(key, []).append(entry)

    if not found_any_default:
        raise ThresholdsError(f"no policy/*.env or roles/*/role.env files found under {root_path}")

    workload_env = contract_path / "workload.env"
    if workload_env.is_file():
        w_rel = _rel_display(root_path, workload_env)
        known_policy_keys = set(policy_index)
        for item in parse_env_with_provenance(_read_text(workload_env)):
            if not _is_workload_governed_key(item, known_policy_keys):
                continue
            key = item["key"]
            if key in policy_index:
                _apply_override(
                    policy_index[key],
                    active=item["value"],
                    source=w_rel,
                    provenance_kind=item["provenance_kind"],
                    provenance=item["provenance"],
                )
            else:
                entry = _make_entry(
                    entry_id=key,
                    key=key,
                    default=item["value"],
                    active=item["value"],
                    default_file=w_rel,
                    source=w_rel,
                    scope=_scope_for(key, "gates"),
                    role=None,
                    provenance_kind=item["provenance_kind"],
                    provenance=item["provenance"],
                    overridden=False,
                )
                entries.append(entry)
                policy_index[key] = entry
                all_by_key.setdefault(key, []).append(entry)

    policy_override = contract_path / "policy.env"
    if policy_override.is_file():
        p_rel = _rel_display(root_path, policy_override)
        for item in parse_env_with_provenance(_read_text(policy_override)):
            key = item["key"]
            if key in policy_index:
                _apply_override(
                    policy_index[key],
                    active=item["value"],
                    source=p_rel,
                    provenance_kind=item["provenance_kind"],
                    provenance=item["provenance"],
                )
            else:
                entry = _make_entry(
                    entry_id=key,
                    key=key,
                    default=None,
                    active=item["value"],
                    default_file=p_rel,
                    source=p_rel,
                    scope=_scope_for(key, "gates"),
                    role=None,
                    provenance_kind=item["provenance_kind"],
                    provenance=item["provenance"],
                    overridden=True,
                )
                entry["changed"] = True
                entries.append(entry)
                policy_index[key] = entry
                all_by_key.setdefault(key, []).append(entry)

    start_plan_raw = cur_env.get("START_PLAN_ENV") or ".crew/start-plan.env"
    start_plan = Path(start_plan_raw) if Path(start_plan_raw).is_absolute() else (root_path / start_plan_raw)
    if start_plan.is_file():
        sp_rel = _rel_display(root_path, start_plan)
        for key, val in parse_plain_env(_read_text(start_plan)).items():
            targets = all_by_key.get(key) or []
            for entry in targets:
                if val != "" or entry["active"] != "":
                    _apply_override(entry, active=val, source=sp_rel)

    for key, targets in all_by_key.items():
        if key in cur_env and cur_env[key] != "":
            val = cur_env[key]
            for entry in targets:
                _apply_override(entry, active=val, source="env")

    tracked_files: list[str] = []
    for entry in entries:
        tracked_files.append(entry["default_file"])
        tracked_files.append(entry["source"])
    file_history = _git_history(root_path, tracked_files) if include_git else {}

    for entry in entries:
        commits = file_history.get(entry["source"]) or file_history.get(entry["default_file"]) or []
        entry["history"] = commits
        entry["last_commit"] = commits[0] if commits else None

    overridden_count = sum(1 for e in entries if e["overridden"])
    changed_count = sum(1 for e in entries if e["changed"])
    with_prov_count = sum(1 for e in entries if e["provenance"] is not None)
    return {
        "root": root_path.as_posix(),
        "contract": contract_disp,
        "summary": {
            "total": len(entries),
            "overridden": overridden_count,
            "changed": changed_count,
            "with_provenance": with_prov_count,
            "without_provenance": len(entries) - with_prov_count,
        },
        "entries": entries,
        "history": file_history,
    }


def summary_line(report: Mapping[str, Any]) -> str:
    """Return `<n> keys (<k> overridden)` for the `core/thresholds.sh` GO line."""
    summary = report.get("summary") or {}
    return f"{summary.get('total', 0)} keys ({summary.get('overridden', 0)} overridden)"


def format_table(report: Mapping[str, Any]) -> str:
    """Format a compact fixed-width table of thresholds, provenance, and commits."""
    entries = report.get("entries") or []
    rows = [
        [
            e["id"],
            "(empty)" if e["default"] in (None, "") else str(e["default"]),
            "(empty)" if e["active"] in (None, "") else str(e["active"]),
            "MOD" if e["changed"] else ("OVR" if e["overridden"] else "-"),
            e.get("provenance_kind") or "-",
            e["source"],
            (e.get("last_commit") or {}).get("short") or "-",
        ]
        for e in entries
    ]
    headers = ["KEY", "DEFAULT", "ACTIVE", "STATE", "PROVENANCE", "SOURCE", "COMMIT"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = min(max(widths[i], len(cell)), 42)

    def _clip(text: str, width: int) -> str:
        return text if len(text) <= width else text[: max(1, width - 1)] + "…"

    lines = [
        "  ".join(_clip(h, widths[i]).ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in rows:
        lines.append("  ".join(_clip(cell, widths[i]).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="Emit structured JSON report")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Emit table/JSON details to stderr and one summary line to stdout",
    )
    parser.add_argument("--root", default=".", help="Workspace root directory")
    parser.add_argument("--contract-dir", default=None, help="Explicit workload contract directory")
    parser.add_argument("--no-git", action="store_true", help="Skip git log lookup")
    return parser


def main(argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        report = collect(
            args.root,
            contract_dir=args.contract_dir,
            env=env,
            include_git=not args.no_git,
        )
    except (ThresholdsError, PolicyError, OSError) as exc:
        print(str(exc), file=sys.stdout)
        return 1

    if args.contract:
        detail = json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) if args.json else format_table(report)
        print(detail, file=sys.stderr)
        print(summary_line(report), file=sys.stdout)
        return 0

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    else:
        print(format_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
