# SPDX-License-Identifier: Apache-2.0
"""Human go-for-launch decision gate promoting or rolling back a held canary."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_GATE = "go-for-launch"


def _first_call_arg(calls: Any, tool_name: str, arg_name: str) -> str:
    """Return string value of *arg_name* from the first call matching *tool_name*."""
    if not isinstance(calls, list):
        return ""
    for item in calls:
        if isinstance(item, dict) and item.get("name") == tool_name:
            args = item.get("args")
            if isinstance(args, dict):
                val = args.get(arg_name)
                return "" if val is None else str(val)
            return ""
    return ""


def _resolve_brief(
    cur_env: dict[str, str],
    promote_dir: Path,
    release_record: str,
    version: str,
    agent_harness: str,
    answered_sha: str,
) -> tuple[str, str]:
    """Read, render, or run the release role to obtain ``(brief, author)``."""
    role_env = cmd_util.load_role_env("release", cur_env, gate=_GATE)
    harness = role_env.get("HARNESS") or agent_harness

    if answered_sha:
        brief_path = promote_dir / "brief.md"
        try:
            on_disk = cmd_util.sha256_file(brief_path)
        except OSError:
            on_disk = ""
        if on_disk != answered_sha:
            cmd_util.nogo(_GATE, "the brief on disk is not the one answered; nothing moved")
        brief = brief_path.read_text(encoding="utf-8").rstrip("\r\n")
        author_path = promote_dir / "brief.author"
        try:
            author_lines = author_path.read_text(encoding="utf-8").splitlines()
            author = author_lines[0] if author_lines else ""
        except OSError:
            author = "unknown"
        return brief, author

    shutil.rmtree(promote_dir, ignore_errors=True)
    (promote_dir / "out").mkdir(parents=True, exist_ok=True)

    if agent_harness == "replay":
        author = "core/promote.sh from the record — no model at L0, the release role writes it from L1"
        brief_proc = cmd_util.run_py("core/releases.py", "brief", env=cur_env, capture_stdout=True)
        if brief_proc.returncode != 0:
            raise SystemExit(brief_proc.returncode)
        return brief_proc.stdout.rstrip("\r\n"), author

    author = f"the release role, {harness}"
    run_env = dict(role_env)
    run_env["ROLE"] = "release"
    run_env["INPUT"] = release_record
    run_env["OUT"] = str(promote_dir / "out")
    run_env["THINKING"] = role_env.get("THINKING") or "low"
    run_env["TIMEOUT_S"] = role_env.get("TIMEOUT_S") or "900"
    if cmd_util.run_sh(
        Path("adapters") / harness / "run.sh", env=run_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.nogo(_GATE, f"the release role did not produce a brief ({harness})")

    calls_path = promote_dir / "out" / "calls.json"
    if not calls_path.is_file():
        cmd_util.nogo(_GATE, "the release role left no calls.json")
    calls = cmd_util.read_json(calls_path)
    declined = _first_call_arg(calls, "decline_brief", "reason")
    if declined:
        cmd_util.nogo(_GATE, f"the release role declined: {declined}")
    brief = _first_call_arg(calls, "submit_brief", "brief")
    if not brief:
        cmd_util.nogo(_GATE, "the release role submitted no brief")
    brief_version = _first_call_arg(calls, "submit_brief", "version")
    if brief_version != version:
        cmd_util.nogo(_GATE, f"the brief is for {brief_version}, the record is {version}")
    return brief, author


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Validate held canary, present brief, read human decision, and shift or roll back."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    release_record = cur_env.get("RELEASE_RECORD") or ".crew/release.json"
    delivery_name = cmd_util.need_env(
        cur_env, "DELIVERY", "DELIVERY not set; run this via ./go, not directly"
    )
    agent_harness = cmd_util.need_env(
        cur_env, "AGENT_HARNESS", "AGENT_HARNESS not set; run this via ./go, not directly"
    )
    promote_out = cur_env.get("PROMOTE_OUT") or ".crew/promote"
    answered_sha = cur_env.get("GO_FOR_LAUNCH_BRIEF_SHA256") or ""

    record_path = Path(release_record)
    if not record_path.is_file():
        cmd_util.nogo(_GATE, "no release record; run ./go release", f"looked at {release_record}")
    try:
        record = cmd_util.read_json(record_path)
    except (OSError, ValueError):
        cmd_util.nogo(_GATE, f"{release_record} is not JSON")

    verdict = (record.get("verdict") if isinstance(record, dict) else None) or "none"
    version = (record.get("version") if isinstance(record, dict) else None) or ""
    if not version:
        cmd_util.nogo(_GATE, f"{release_record} names no version")
    if verdict != "held":
        stages = record.get("stages") or []
        stage = "no"
        if isinstance(stages, list) and stages and isinstance(stages[-1], dict):
            pct = stages[-1].get("pct")
            if pct is not None:
                stage = str(pct)
        cmd_util.nogo(_GATE, f"release {version} is {verdict} at {stage}%, nothing to promote")

    delivery_dir = Path("adapters") / delivery_name
    status_proc = cmd_util.run_sh(delivery_dir / "status.sh", env=cur_env, capture_stdout=True)
    if status_proc.returncode != 0:
        cmd_util.nogo(_GATE, f"{delivery_dir}/status.sh could not read the plane; nothing moved")
    plane_proc = cmd_util.run_py(
        "core/releases.py",
        "plane",
        env=cur_env,
        input_text=status_proc.stdout,
        capture_stdout=True,
    )
    if plane_proc.returncode != 0:
        cmd_util.nogo(
            _GATE, plane_proc.stdout.rstrip("\r\n"), status_proc.stdout.rstrip("\r\n")
        )

    promote_dir = Path(promote_out)
    brief, author = _resolve_brief(
        cur_env, promote_dir, release_record, str(version), agent_harness, answered_sha
    )

    if str(version) not in brief:
        cmd_util.nogo(_GATE, f"the brief does not name {version}")
    for st in record.get("stages") or []:
        pct = st.get("pct") if isinstance(st, dict) else None
        if f"{pct}%" not in brief:
            cmd_util.nogo(_GATE, f"the brief does not name the {pct}% stage")

    if not answered_sha:
        (promote_dir / "brief.md").write_text(f"{brief}\n", encoding="utf-8")
        (promote_dir / "brief.author").write_text(f"{author}\n", encoding="utf-8")

    sys.stderr.write(f"[go-for-launch] promotion brief for {version} (by {author}):\n\n{brief}\n\n")
    sys.stderr.flush()

    answer = cur_env.get("GO_FOR_LAUNCH", "")
    if not answer and sys.stdin.isatty():
        sys.stderr.write(f"go-for-launch {version} to 100%? [GO/NO-GO] ")
        sys.stderr.flush()
        answer = sys.stdin.readline().rstrip("\r\n")

    if answer == "NO-GO":
        if cmd_util.run_sh(
            delivery_dir / "rollback.sh", env=cur_env, stdout_to_stderr=True
        ).returncode != 0:
            cmd_util.nogo(_GATE, "human said NO-GO, and the rollback failed")
        ans_proc = cmd_util.run_py("core/releases.py", "answer", "NO-GO", env=cur_env)
        if ans_proc.returncode != 0:
            raise SystemExit(ans_proc.returncode)
        cmd_util.nogo(_GATE, "human said NO-GO", f"{version} rolled back")
    if answer == "":
        cmd_util.nogo(
            _GATE,
            "no human answer (set GO_FOR_LAUNCH=GO or answer on the terminal); nothing moved",
        )
    if answer != "GO":
        cmd_util.nogo(_GATE, f'answer "{answer}" is neither GO nor NO-GO; nothing moved')

    if cmd_util.run_sh(
        delivery_dir / "shift.sh", "100", env=cur_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.nogo(_GATE, "shift to 100% did not take")
    ans_proc = cmd_util.run_py("core/releases.py", "answer", "GO", env=cur_env)
    if ans_proc.returncode != 0:
        raise SystemExit(ans_proc.returncode)
    cmd_util.run_py("core/promote_hint.py", str(version), env=cur_env)
    return cmd_util.go(_GATE)


if __name__ == "__main__":
    raise SystemExit(main())
