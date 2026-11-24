"""Continuous ops loop helpers (docs/adr/0109): stdlib only.

Deterministic rollback-to-incident creation reusing core/incident.py, plus
opt-in postmortem dispatch when AUTO_POSTMORTEM_ON_ROLLBACK=on. Strictly
vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess  # nosec B404 - core/postmortem.sh when opted in; git diff-tree for perimeters (docs/adr/0121)
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    from . import incident, releases, spec
except ImportError:  # run as a script: core/ is sys.path[0]
    import incident
    import releases
    import spec

Refused = incident.Refused

# Provenance for both bounds: docs/adr/0121, "Thresholds and provenance".
MAX_LOG_LINES = 200
MAX_LOG_LINE_CHARS = 320


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "release"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_window(record: Mapping[str, Any], env: Mapping[str, str]) -> tuple[str, str]:
    given_from = (env.get("FROM") or "").strip()
    given_to = (env.get("TO") or "").strip()
    if given_from and given_to:
        return given_from, given_to
    stamps: list[str] = []
    for stage in record.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        for key in ("started_at", "judged_at"):
            val = stage.get(key)
            if isinstance(val, str) and incident.STAMP.fullmatch(val):
                stamps.append(val)
    if stamps:
        return min(stamps), max(stamps)
    at = (env.get("INCIDENT_AT") or "").strip()
    if at and incident.STAMP.fullmatch(at):
        return at, at
    now_utc = _utc_now()
    return now_utc, now_utc


# --- bounded log capture and redaction (docs/adr/0121 §1) ---

_NAME = r"[A-Za-z0-9_]*_(?:KEY|TOKEN|SECRET)"
_ASSIGN = re.compile(rf"\b({_NAME})=(\"[^\"]*\"|'[^']*'|\S*)", re.I)
_JSON_MEMBER = re.compile(rf"(\"{_NAME}\"\s*:\s*)\"(?:[^\"\\]|\\.)*\"", re.I)
_AUTH = re.compile(r"\bAuthorization:\s*(Bearer|Basic)\s+\S+", re.I)
REDACTED = "[REDACTED]"


def redact_line(line: str) -> str:
    """One log line with credential shapes rewritten; never drops the line."""
    line = _AUTH.sub(lambda m: f"Authorization: {m.group(1).capitalize()} {REDACTED}", line)
    line = _JSON_MEMBER.sub(lambda m: f'{m.group(1)}"{REDACTED}"', line)
    return _ASSIGN.sub(lambda m: f"{m.group(1)}={REDACTED}", line)


def collect_log_tail(path: str) -> list[str]:
    """The last MAX_LOG_LINES lines of PATH, redacted, each within MAX_LOG_LINE_CHARS."""
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return []
    if lines and lines[-1] == "":
        lines.pop()
    out = []
    for line in lines[-MAX_LOG_LINES:]:
        line = redact_line(line)
        if len(line) > MAX_LOG_LINE_CHARS:
            line = line[: MAX_LOG_LINE_CHARS - 3] + "..."
        out.append(line)
    return out


def _stage_numbers(stage: Mapping[str, Any]) -> list[dict[str, Any]]:
    slo = stage.get("slo")
    if isinstance(slo, list):
        return [e for e in slo if isinstance(e, dict)]
    numbers = [n for n in stage.get("numbers") or [] if isinstance(n, str)]
    return releases.slo_of(numbers, {})


def exemplars_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """Structured stage numbers of a release record: no model, no clock."""
    failing = None
    breach = None
    stages = []
    for stage in record.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        verdict = stage.get("verdict")
        entries = {e.get("query"): e for e in _stage_numbers(stage)}

        def pick(query: str) -> dict[str, Any] | None:
            e = entries.get(query)
            if e is None:
                return None
            return {k: e.get(k) for k in ("canary", "stable", "delta", "max_delta")}

        traffic = entries.get("traffic")
        stages.append({
            "pct": stage.get("pct"),
            "verdict": verdict,
            "traffic": None if traffic is None else {k: traffic.get(k) for k in ("canary", "stable", "delta", "max_delta")},
            "error_rate": pick("error_rate"),
            "p95": pick("latency_p95"),
        })
        if failing is None and not (isinstance(verdict, str) and verdict.startswith("GO")):
            failing = stage.get("pct")
        if breach is None:
            for e in _stage_numbers(stage):
                delta, limit = e.get("delta"), e.get("max_delta")
                if isinstance(delta, (int, float)) and isinstance(limit, (int, float)) and delta > limit:
                    breach = {"pct": stage.get("pct"), "query": e.get("query"), "delta": delta, "max_delta": limit}
                    break
    return {"failing_stage_pct": failing, "stages": stages, "breach": breach}


def _incident_logs(cfg: Mapping[str, str], explicit: bool) -> list[str] | None:
    """The collected excerpt when no explicit source exists, else None."""
    if explicit:
        return None
    named = (cfg.get("ROLLBACK_LOG_SOURCE") or "").strip()
    for path in (named, os.path.join(".crew", "canary.log")):
        if path and os.path.isfile(path):
            return collect_log_tail(path)
    return None


# --- post-mortem action-ticket perimeters (docs/adr/0121 §2) ---

_SHA = re.compile(r"[0-9a-f]{7,64}")
_STATE_DIRS = ("tickets/", ".crew/")


def safe_glob(path: str) -> bool:
    """A perimeter entry that is a plain repository-relative path."""
    if not path or path.startswith("/") or any(c in path for c in "`\n\r\0"):
        return False
    return ".." not in path.split("/")


def _git(repo_dir: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(  # nosec B603 B607 - fixed git argv, no shell, revision validated as hex
            ["git", "-C", repo_dir, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def changed_files(repo_dir: str, merge: str) -> list[str]:
    """Files a commit changed against its first parent, sorted; [] for anything off."""
    if not isinstance(merge, str) or not _SHA.fullmatch(merge):
        return []
    parents = _git(repo_dir, "rev-list", "--parents", "-n", "1", merge)
    if parents is None:
        return []
    shas = parents.split()[1:]
    base = ["diff-tree", "--no-commit-id", "--name-only", "-r", "--no-renames"]
    out = _git(repo_dir, *base, shas[0], merge) if shas else _git(repo_dir, *base, "--root", merge)
    if out is None:
        return []
    return sorted({p for p in out.splitlines() if p and not p.startswith(_STATE_DIRS)})


def _map_tests(map_file: str, paths: list[str]) -> list[str] | None:
    """Tests joined to PATHS by a `tests` edge in the map database; None when unreadable."""
    if not map_file or not os.path.isfile(map_file):
        return None
    try:
        db = sqlite3.connect(f"file:{map_file}?mode=ro", uri=True)
        try:
            marks = ",".join("?" * len(paths)) or "''"
            rows = db.execute(
                "SELECT DISTINCT t.name FROM edge e JOIN node t ON t.id = e.src "  # nosec B608 - placeholders only
                f"JOIN node f ON f.id = e.dst WHERE e.kind = 'tests' AND f.name IN ({marks})", paths).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return None
    return sorted(r[0] for r in rows)


def _neighbour_tests(repo_dir: str, paths: list[str]) -> list[str]:
    found = set()
    for path in paths:
        here, name = os.path.split(path)
        stem, ext = os.path.splitext(name)
        if ext != ".py" or stem.startswith("test_"):
            continue
        for candidate in (os.path.join("tests", f"test_{stem}.py"), os.path.join(here, "tests", f"test_{stem}.py"),
                          os.path.join(here, f"test_{stem}.py")):
            if os.path.isfile(os.path.join(repo_dir, candidate)):
                found.add(candidate)
    return sorted(found)


def _inject(text: str, globs: list[str]) -> str:
    block = [spec.PERIMETER, ""] + [f"- `{g}`" for g in globs] + [""]
    lines = text.splitlines()
    at = next((i for i, line in enumerate(lines) if line.startswith("## Notes")), None)
    if at is None:
        return "\n".join(lines).rstrip("\n") + "\n\n" + "\n".join(block)
    return "\n".join(lines[:at] + block + lines[at:]) + "\n"


def _readiness(text: str) -> tuple[str, str | None]:
    why = spec.check(text)
    if why is None:
        return "spec-ready", None
    if spec.PERIMETER in why:
        return "needs-perimeter", why
    return "needs-spec", why


def derive_action_perimeters(
    ticket_paths: list[str],
    chain: Mapping[str, Any] | None,
    repo_dir: str,
    map_file: str | None = None,
    env: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Give each post-mortem ticket lacking a `## Perimeter` one derived from the merge."""
    cfg = os.environ if env is None else env
    promote = cfg.get("AUTO_PROMOTE_POSTMORTEM_TICKETS") == "on"
    derived: list[str] | None = None
    results = []
    for path in ticket_paths:
        res: dict[str, Any] = {"ticket": path, "status": "needs-perimeter", "perimeter": [],
                               "readiness": None, "reason": None}
        results.append(res)
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            res["reason"] = f"cannot read {path}"
            res["readiness"] = "needs-perimeter" if promote else None
            continue
        existing = spec.section(text, spec.PERIMETER)
        if existing is not None:
            res["status"] = "kept"
            res["perimeter"] = [line[2:].strip().replace("`", "") for line in existing if line.startswith("- ")]
        else:
            if derived is None:
                merge = (chain or {}).get("merge") if isinstance(chain, Mapping) else None
                changed = changed_files(repo_dir, merge) if merge else []
                tests = _map_tests(map_file or "", changed) if changed else None
                if tests is None:
                    tests = _neighbour_tests(repo_dir, changed)
                derived = sorted({g for g in changed + tests if safe_glob(g)})
            if not derived:
                res["reason"] = "no perimeter derivable from the rolled-back merge"
            else:
                new_text = _inject(text, derived)
                if not any(line.startswith("- ") for line in (spec.section(new_text, spec.PERIMETER) or [])):
                    res["reason"] = "derived perimeter failed validation"
                else:
                    tmp = f"{path}.tmp-{os.getpid()}"
                    with open(tmp, "w", encoding="utf-8") as fh:
                        fh.write(new_text)
                    os.replace(tmp, path)
                    text = new_text
                    res["status"] = "injected"
                    res["perimeter"] = list(derived)
        if promote and res["status"] != "needs-perimeter":
            res["readiness"], res["reason"] = _readiness(text)
        elif promote:
            res["readiness"] = "needs-perimeter"
    return results


def _postmortem_perimeters(doc: Mapping[str, Any], cfg: Mapping[str, str]) -> list[dict[str, Any]] | None:
    """Perimeters for the tickets the post mortem filed, when they are local files."""
    out_dir = (cfg.get("POSTMORTEM_OUT") or "").strip() or os.path.join(".crew", "postmortem")
    pm_path = os.path.join(out_dir, str(doc.get("name")), "postmortem.json")
    tickets_dir = (cfg.get("TICKETS_DIR") or "").strip() or "tickets"
    try:
        with open(pm_path, encoding="utf-8") as fh:
            actions = json.load(fh).get("actions") or []
    except (OSError, ValueError, AttributeError):
        return None
    paths = []
    for action in actions:
        ticket = str(action.get("ticket") or "") if isinstance(action, dict) else ""
        candidate = os.path.join(tickets_dir, f"{ticket}.md")
        if ticket and re.fullmatch(r"[A-Za-z0-9_-]+", ticket) and os.path.isfile(candidate):
            paths.append(candidate)
    if not paths:
        return None
    return derive_action_perimeters(paths, doc.get("chain"), (cfg.get("REPO_DIR") or "").strip() or ".",
                                    (cfg.get("MAP_FILE") or "").strip() or None, cfg)


def _run_postmortem(incident_path: str, env: Mapping[str, str]) -> dict[str, Any]:
    """Run core/postmortem.sh when opted in; never fail the caller if replay recording is absent."""
    script = os.path.join("core", "postmortem.sh")
    if not os.path.isfile(script):
        msg = f"[ops-loop] postmortem skipped: {script} not found"
        print(msg, file=sys.stderr)
        return {"status": "skipped", "reason": "postmortem script not found"}

    pm_env = dict(env)
    pm_env["INCIDENT"] = incident_path
    proc = subprocess.run(  # nosec B603 B607 - fixed core/postmortem.sh script, ADR 0052
        ["sh", script],
        env=pm_env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else ""
    if proc.returncode == 0:
        print(f"[ops-loop] postmortem: {line}", file=sys.stderr)
        return {"status": "ok", "line": line}

    combined = f"{line}\n{proc.stderr or ''}".lower()
    if "norecording" in combined or "no recording" in combined or "no_recording" in combined or "did not finish" in combined:
        reason = line or "ops-triage replay recording absent or harness did not finish"
        print(
            f"[ops-loop] postmortem skipped (replay recording absent or role unfinished): {reason}",
            file=sys.stderr,
        )
        return {"status": "skipped", "reason": reason}

    reason = line or f"core/postmortem.sh exited {proc.returncode}"
    print(f"[ops-loop] postmortem not completed: {reason}", file=sys.stderr)
    return {"status": "skipped", "reason": reason}


def on_rollback(
    record_path: str,
    env: Mapping[str, str] | None = None,
    *,
    strict: bool = True,
    release_hook: bool = False,
) -> dict[str, Any]:
    """Create a deterministic incident document for a rolled-back release record."""
    cfg = os.environ if env is None else env
    record = incident.release_record(record_path)
    if record.get("verdict") != "rolled-back":
        raise Refused(f"{record_path} has verdict {record.get('verdict')!r}, expected 'rolled-back'")

    start, end = _derive_window(record, cfg)
    slug = _slugify(str(record["version"]))
    given_stamp = (cfg.get("INCIDENT_STAMP") or "").strip()
    stamp = given_stamp if incident.NAME.fullmatch(given_stamp) else re.sub(r"[^a-z0-9]+", "", end.lower())
    name = f"rollback-{slug}-{stamp}"

    # RELEASE_TICKET takes precedence over TICKET because Makefile exports TICKET ?= 0001 globally.
    if release_hook:
        ticket = (cfg.get("RELEASE_TICKET") or "").strip()
    else:
        ticket = (cfg.get("RELEASE_TICKET") or cfg.get("TICKET") or "").strip()
    store = (cfg.get("STATE_STORE") or "").strip()

    inc_env: dict[str, str] = {
        "NAME": name,
        "FROM": start,
        "TO": end,
        "RELEASE_RECORD": str(record_path),
    }

    logs_path = (cfg.get("LOGS") or "").strip()
    if logs_path:
        inc_env["LOGS"] = logs_path
    elif ticket:
        candidate = os.path.join(".crew", ticket, "release.log")
        if os.path.isfile(candidate):
            inc_env["LOGS"] = candidate
    collected = _incident_logs(cfg, "LOGS" in inc_env)

    if ticket and store:
        inc_env["TICKET"] = ticket
        inc_env["STATE_STORE"] = store
    elif ticket and not store:
        print(f"[ops-loop] ticket {ticket} present without STATE_STORE; chain left null", file=sys.stderr)

    try:
        doc = incident.incident(inc_env)
    except incident.Refused as exc:
        if strict or "TICKET" not in inc_env:
            raise
        print(f"[ops-loop] chain unavailable ({exc}); writing incident with chain=null", file=sys.stderr)
        inc_env.pop("TICKET", None)
        inc_env.pop("STATE_STORE", None)
        doc = incident.incident(inc_env)

    doc["logs"] = collected if collected is not None else [redact_line(line) for line in doc["logs"]]
    doc["exemplars"] = exemplars_of(record)

    out_dir = (cfg.get("INCIDENT_OUT") or "").strip() or os.path.join(".crew", "incidents")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{doc['name']}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"[ops-loop] incident written: {out_path}", file=sys.stderr)

    pm_result = None
    if cfg.get("AUTO_POSTMORTEM_ON_ROLLBACK") == "on":
        pm_result = _run_postmortem(out_path, cfg)
        if pm_result.get("status") == "ok":
            perimeters = _postmortem_perimeters(doc, cfg)
            if perimeters is not None:
                pm_result["perimeters"] = perimeters

    return {
        "incident_path": out_path,
        "incident": doc,
        "postmortem": pm_result,
    }


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core/ops_loop.py")
    sub = parser.add_subparsers(dest="cmd", required=True)
    rb = sub.add_parser("on-rollback")
    rb.add_argument("--record", default=os.path.join(".crew", "release.json"))
    args = parser.parse_args(argv)

    if args.cmd == "on-rollback":
        try:
            res = on_rollback(args.record, env)
        except Refused as exc:
            print(f"ops-loop: {exc}", file=sys.stderr)
            return 1
        print(res["incident_path"])
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
