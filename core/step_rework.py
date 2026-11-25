# SPDX-License-Identifier: Apache-2.0
"""Rework tower step: append rejection verdict and relaunch crew or escalate to needs-human."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_STEP = "step-rework"


def _build_entry(doc: dict[str, Any], ticket: str, attempt: int, judged: str) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    mainline = doc.get("mainline") if isinstance(doc.get("mainline"), dict) else {}
    ci_doc = doc.get("ci") if isinstance(doc.get("ci"), dict) else {}
    review = doc.get("review") if isinstance(doc.get("review"), dict) else {}

    ci_lines = ci_doc.get("lines")
    ci_seconds = ci_doc.get("seconds")
    seconds = ci_seconds if ci_seconds is not None else None

    if mainline.get("verdict") == "NO-GO" or ci_doc.get("verdict") == "NO-GO":
        v = mainline if mainline.get("verdict") == "NO-GO" else {**ci_doc, "judge": "gates"}
        failed_val = v.get("failed")
        sast_val = v.get("sast")
        details_val = v.get("details")
        return {
            "attempt": attempt,
            "judge": v.get("judge"),
            "failed": failed_val if failed_val not in (None, False) else None,
            "head_sha": judged,
            "line": v.get("line"),
            "details": details_val if details_val is not None else [],
            "sast": sast_val if sast_val not in (None, False) else None,
            "lines": ci_lines,
            "seconds": seconds,
            "at": now_utc,
        }

    if review.get("verdict") == "changes-requested":
        reason = str(review.get("reason") or "")
        return {
            "attempt": attempt,
            "judge": "review",
            "failed": None,
            "sast": None,
            "head_sha": judged,
            "line": f"CHANGES-REQUESTED — {review.get('role')}",
            "details": reason.split("\n"),
            "lines": ci_lines,
            "seconds": seconds,
            "at": now_utc,
        }

    cmd_util.fail_msg(
        _STEP,
        f"ticket {ticket} is at ci-failed with neither a gates NO-GO nor a changes-requested review",
    )


def _format_verdict_md(entry: dict[str, Any], next_attempt: int, max_attempts: int) -> str:
    judge = entry.get("judge")
    judge_label = {"gates": "gates", "review": "reviewer"}.get(str(judge), "tower")
    fresh = judge in ("base", "main")
    lines = [
        f"## Rework: attempt {next_attempt} of {max_attempts}\n",
        f"Your previous attempt (head {entry['head_sha']}) was not accepted. The verdict",
        f"below is everything the {judge_label} said about it. Your workspace {'starts' if fresh else 'already'}",
        (
            "from the head of main, without that attempt: redo the ticket on it, addressing every point the verdict raises,"
            if fresh
            else "holds that attempt: change it to address every point the verdict raises,"
        ),
        "within the ticket above, and nothing else.\n",
        "Verdict:\n",
        f"    {entry['line']}",
    ]
    details = entry.get("details") or []
    if details:
        lines.append("\nDetails:\n")
        for item in details:
            lines.append(f"    {item}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Record rework entry, escalate at cap/spend stop, or relaunch crew and emit pr-opened JSON."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    ticket = cmd_util.need_env(cur_env, "TICKET", "TICKET not set")
    doc_file = cmd_util.need_env(
        cur_env, "DOC_FILE", "DOC_FILE not set; run this via core/tower.sh, not directly"
    )
    role = cur_env.get("ROLE") or "dev-worker"

    policy = cmd_util.parse_env_file("policy/rework.env")
    max_attempts_raw = policy.get("REWORK_MAX_ATTEMPTS") or ""
    if not max_attempts_raw:
        cmd_util.fail_msg(_STEP, "policy/rework.env sets no REWORK_MAX_ATTEMPTS")
    max_attempts = int(max_attempts_raw)

    doc = cmd_util.read_json(doc_file)
    if not isinstance(doc, dict):
        doc = {}
    attempt = int(doc.get("attempt") or 1)
    judged = ((doc.get("pr") or {}).get("head_sha")) or ""
    if not judged:
        cmd_util.fail_msg(_STEP, f"{doc_file} has no pr.head_sha")

    entry = _build_entry(doc, ticket, attempt, str(judged))
    rework = list(doc.get("rework") or []) + [entry]

    spend_stop = cur_env.get("SPEND_STOP") or ""
    if spend_stop or attempt >= max_attempts:
        prefix = f"{spend_stop}; " if spend_stop else ""
        sys.stderr.write(
            f"[rework] ticket {ticket}: attempt {attempt} of {max_attempts} rejected ({entry['line']}); "
            f"{prefix}a human is needed\n"
        )
        sys.stderr.flush()
        payload: dict[str, Any] = {"state": "needs-human", "rework": rework}
        if spend_stop:
            payload["budget"] = {"line": spend_stop}
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()
        return 0

    next_attempt = attempt + 1
    fresh = entry.get("judge") in ("base", "main")
    verdict_md = _format_verdict_md(entry, next_attempt, max_attempts)

    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False, suffix=".md"
    ) as tf:
        tf.write(verdict_md)
        verdict_path = Path(tf.name)

    try:
        sys.stderr.write(
            f"[rework] ticket {ticket}: attempt {attempt} rejected by the {entry['judge']}; "
            f"attempt {next_attempt} of {max_attempts}\n"
        )
        sys.stderr.flush()

        crew_env = dict(cur_env)
        crew_env["TICKET"] = ticket
        crew_env["ROLE"] = role
        crew_env["ATTEMPT"] = str(next_attempt)
        crew_env["REWORK_FILE"] = str(verdict_path)
        crew_env["REWORK_HEAD"] = str(judged)
        crew_env["REWORK_FRESH"] = "1" if fresh else ""

        crew_proc = cmd_util.run_sh(
            "core/crew.sh", env=crew_env, stdout_to_stderr=True
        )
        if crew_proc.returncode != 0:
            raise SystemExit(crew_proc.returncode)
    finally:
        verdict_path.unlink(missing_ok=True)

    crew_dir = Path(".crew") / ticket
    pr_env = cmd_util.parse_env_file(crew_dir / "pr-opened.env")
    task_proc = cmd_util.run_py("core/tower.py", "task", "--patch", str(crew_dir / "out" / "run.json"), env=cur_env, capture_stdout=True)
    if task_proc.returncode != 0:
        raise SystemExit(task_proc.returncode)
    task = json.loads(task_proc.stdout)

    out_payload = {
        "state": "pr-opened",
        "attempt": next_attempt,
        "rework": rework,
        "ci": None,
        "review": None,
        "mainline": None,
        "pr": {
            "number": int(pr_env["PR_NUMBER"]),
            "url": pr_env["PR_URL"],
            "base_sha": pr_env["BASE_SHA"],
            "head_sha": pr_env["HEAD_SHA"],
        },
        "task": {"role": role, **task},
    }
    sys.stdout.write(json.dumps(out_payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
