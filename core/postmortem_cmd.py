# SPDX-License-Identifier: Apache-2.0
"""Run ops-triage over an incident, file action tickets, and render postmortem."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_GATE = "postmortem"


def _action_body(
    problem: str, acceptance: str, incident: str, release: str, verdict: str
) -> str:
    return (
        f"## Problem\n\n{problem}\n\n"
        f"Found by the post mortem of incident `{incident}` "
        f"(release `{release}`, `{verdict}`).\n\n"
        f"## Acceptance criteria\n\n{acceptance}\n\n"
        "## Notes\n\n"
        "Written by the ops-triage role from the incident document alone. "
        "It has not seen the code, so this ticket declares no perimeter: "
        "a human adds one before it is assigned.\n"
    )


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Validate INCIDENT, invoke ops-triage, file tracker tickets, and render output."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    incident_file = cmd_util.need_env(
        cur_env, "INCIDENT", "INCIDENT not set; ./go postmortem INCIDENT=<file>", gate=_GATE
    )
    agent_harness = cmd_util.need_env(
        cur_env, "AGENT_HARNESS", "AGENT_HARNESS not set; run this via ./go, not directly"
    )
    tracker = cmd_util.need_env(
        cur_env, "TRACKER", "TRACKER not set; run this via ./go, not directly"
    )
    postmortem_out = cur_env.get("POSTMORTEM_OUT") or ".crew/postmortem"

    inc_path = Path(incident_file)
    if not inc_path.is_file():
        cmd_util.nogo(_GATE, f"no incident at {incident_file}")

    val_proc = cmd_util.run_py(
        "core/postmortem.py", "validate", incident_file, env=cur_env, capture_stdout=True
    )
    if val_proc.returncode != 0:
        cmd_util.nogo(_GATE, val_proc.stdout.rstrip("\r\n"))

    inc_data = cmd_util.read_json(inc_path)
    incident = str(inc_data["name"])
    release = str(inc_data["release"]["version"])
    verdict = str(inc_data["release"]["verdict"])

    shutil.rmtree(Path(postmortem_out) / incident, ignore_errors=True)

    role_env = cmd_util.load_role_env("ops-triage", cur_env, gate=_GATE)
    harness = role_env.get("HARNESS") or agent_harness
    harness_run = Path("adapters") / harness / "run.sh"
    if not harness_run.is_file():
        cmd_util.nogo(_GATE, f"adapters/{harness}/run.sh not found for the ops-triage role")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "out").mkdir(parents=True, exist_ok=True)
        run_env = dict(role_env)
        run_env["ROLE"] = "ops-triage"
        run_env["INPUT"] = incident_file
        run_env["OUT"] = str(work / "out")
        run_env["THINKING"] = role_env.get("THINKING") or "low"
        run_env["TIMEOUT_S"] = role_env.get("TIMEOUT_S") or "900"

        if cmd_util.run_sh(harness_run, env=run_env, stdout_to_stderr=True).returncode != 0:
            cmd_util.nogo(_GATE, f"the ops-triage role did not finish ({harness})")

        calls_path = work / "out" / "calls.json"
        if not calls_path.is_file():
            cmd_util.nogo(_GATE, "the ops-triage role left no calls.json")
        calls = cmd_util.read_json(calls_path)
        sequence = " ".join(
            str(item.get("name", "")) for item in calls if isinstance(item, dict)
        )
        if sequence != "file_postmortem":
            cmd_util.nogo(
                _GATE, f"expected exactly file_postmortem, got: {sequence or 'nothing'}"
            )

        checked_path = work / "checked.json"
        check_proc = cmd_util.run_py(
            "core/postmortem.py",
            "check",
            incident_file,
            str(calls_path),
            str(checked_path),
            env=cur_env,
            capture_stdout=True,
        )
        if check_proc.returncode != 0:
            cmd_util.nogo(_GATE, check_proc.stdout.rstrip("\r\n"))

        checked = cmd_util.read_json(checked_path)
        actions = checked.get("actions", [])
        by_num = {a.get("n"): a for a in actions if isinstance(a, dict)}
        count = len(actions)
        tickets: dict[str, str] = {}

        for n in range(1, count + 1):
            action = by_num[n]
            body = _action_body(
                str(action["title"] if "problem" not in action else action["problem"]),
                str(action["acceptance"]),
                incident,
                release,
                verdict,
            )
            put_proc = cmd_util.run_sh(
                Path("adapters") / tracker / "put.sh",
                str(action["title"]),
                f"postmortem {incident} action {n}",
                env=cur_env,
                input_text=body,
                capture_stdout=True,
                capture_stderr=True,
            )
            if put_proc.returncode != 0:
                err_lines = put_proc.stderr.splitlines()
                last_err = err_lines[-1] if err_lines else ""
                cmd_util.nogo(_GATE, f"action {n} has no ticket: {last_err}")
            tickets[str(n)] = put_proc.stdout.rstrip("\r\n")

        tickets_path = work / "tickets.json"
        tickets_path.write_text(json.dumps(tickets), encoding="utf-8")

        dest = Path(postmortem_out) / incident
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(work / "out", dest / "out", dirs_exist_ok=True)
        render_proc = cmd_util.run_py(
            "core/postmortem.py",
            "render",
            str(checked_path),
            str(tickets_path),
            str(work / "out" / "run.json"),
            str(dest),
            env=cur_env,
        )
        if render_proc.returncode != 0:
            raise SystemExit(render_proc.returncode)

        sys.stderr.write(f"[postmortem] {incident} written to {dest}/postmortem.md\n")
        sys.stderr.flush()
        ticket_list = " ".join(tickets.values())
        return cmd_util.go(_GATE, f"{count} action item(s): tickets {ticket_list}")


if __name__ == "__main__":
    raise SystemExit(main())
