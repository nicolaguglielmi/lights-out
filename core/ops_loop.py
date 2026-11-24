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
import subprocess  # nosec B404 - invokes core/postmortem.sh when opted in
import sys
from datetime import datetime, timezone
from typing import Any, Mapping

try:
    from . import incident
except ImportError:  # run as a script: core/ is sys.path[0]
    import incident

Refused = incident.Refused


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

    out_dir = (cfg.get("INCIDENT_OUT") or "").strip() or os.path.join(".crew", "incidents")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{doc['name']}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"[ops-loop] incident written: {out_path}", file=sys.stderr)

    pm_result = None
    if cfg.get("AUTO_POSTMORTEM_ON_ROLLBACK") == "on":
        pm_result = _run_postmortem(out_path, cfg)

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
