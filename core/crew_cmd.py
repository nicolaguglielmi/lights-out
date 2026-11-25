# SPDX-License-Identifier: Apache-2.0
"""Vendor-free ticket-to-PR crew driver for dev-worker and rework attempts."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_GATE = "crew"


def _git_output(repo: Path, *args: str, env: dict[str, str]) -> str:
    proc = cmd_util.git_cmd(repo, *args, env=env, capture_stdout=True)
    if proc.returncode != 0:
        raise SystemExit(proc.returncode or 1)
    return proc.stdout.rstrip("\r\n")


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Bootstrap host/tracker, run agent harness in cloned workspace, verify, push, and open PR."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    ticket = cmd_util.need_env(
        cur_env, "TICKET", "TICKET not set; run this via ./go assign TICKET=<id>"
    )
    role = cur_env.get("ROLE") or "dev-worker"
    agent_harness = cmd_util.need_env(
        cur_env, "AGENT_HARNESS", "AGENT_HARNESS not set; run this via ./go, not directly"
    )
    code_host = cmd_util.need_env(
        cur_env, "CODE_HOST", "CODE_HOST not set; run this via ./go, not directly"
    )
    tracker = cmd_util.need_env(
        cur_env, "TRACKER", "TRACKER not set; run this via ./go, not directly"
    )
    workload = cmd_util.need_env(
        cur_env, "WORKLOAD", "WORKLOAD not set; run this via ./go, not directly"
    )
    attempt_str = cur_env.get("ATTEMPT") or "1"
    attempt = int(attempt_str)
    rework_file = ""
    rework_head = ""
    if attempt > 1:
        rework_file = cmd_util.need_env(
            cur_env, "REWORK_FILE", f"REWORK_FILE not set for attempt {attempt_str}"
        )
        rework_head = cmd_util.need_env(
            cur_env, "REWORK_HEAD", f"REWORK_HEAD not set for attempt {attempt_str}"
        )

    prompt_path = Path("roles") / role / "prompt.md"
    if not prompt_path.is_file():
        role_md = Path("roles") / role / "ROLE.md"
        try:
            lines = role_md.read_text(encoding="utf-8").splitlines()
            reason = lines[0] if lines else ""
        except OSError:
            reason = f"no roles/{role}/prompt.md and no ROLE.md"
        cmd_util.nogo(_GATE, f"role {role} has no prompt yet", reason)

    role_env = cmd_util.load_role_env(role, cur_env, gate=_GATE)
    harness = cur_env.get("CREW_HARNESS") or role_env.get("HARNESS") or agent_harness

    if cur_env.get("WORKLOAD_ATTACHED") and harness == "replay":
        remote = cmd_util.git_cmd(
            workload, "remote", "get-url", "origin", capture_stdout=True, suppress_stderr=True
        )
        origin = remote.stdout.rstrip("\r\n") if remote.returncode == 0 and remote.stdout.strip() else workload
        cmd_util.nogo(
            _GATE,
            f"the attached repository {origin} has no recordings to replay at L0; run the crew at L1: "
            "PROFILE=l1 and the key of MODEL_PROVIDER in .env (see .env.example)",
        )

    workload_contract = cmd_util.need_env(
        cur_env, "WORKLOAD_CONTRACT", "WORKLOAD_CONTRACT not set; run this via ./go, not directly"
    )
    brief = Path(workload_contract) / "worker.md"
    if not brief.is_file():
        cmd_util.nogo(
            _GATE,
            f"{brief} is missing; the worker would start from no brief (docs/adr/0037 §6)",
        )

    crew_dir = Path.cwd() / ".crew" / ticket
    shutil.rmtree(crew_dir, ignore_errors=True)
    crew_dir.mkdir(parents=True, exist_ok=True)

    boot_env = dict(cur_env)
    boot_env["CODE_HOST_KEEP_MAIN"] = "1" if attempt > 1 else ""
    if cmd_util.run_sh(
        Path("adapters") / code_host / "bootstrap.sh", env=boot_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.nogo(_GATE, f"{code_host} bootstrap failed")

    ticket_proc = cmd_util.run_sh(
        Path("adapters") / tracker / "get.sh", ticket, env=cur_env, capture_stdout=True
    )
    if ticket_proc.returncode != 0:
        cmd_util.nogo(_GATE, f"no such ticket: {ticket}")
    (crew_dir / "ticket.md").write_text(ticket_proc.stdout, encoding="utf-8")

    clone_url_proc = cmd_util.run_sh(
        Path("adapters") / code_host / "clone-url.sh", env=cur_env, capture_stdout=True
    )
    if clone_url_proc.returncode != 0:
        cmd_util.nogo(_GATE, f"clone from {code_host} failed")
    clone_url = clone_url_proc.stdout.rstrip("\r\n")

    workspace = crew_dir / "workspace"
    if cmd_util.run_cmd(
        ["git", "clone", "-q", clone_url, str(workspace)],
        env=cur_env,
        stdout_to_stderr=True,
    ).returncode != 0:
        cmd_util.nogo(_GATE, f"clone from {code_host} failed")

    if attempt > 1 and not cur_env.get("REWORK_FRESH"):
        if cmd_util.git_cmd(
            workspace, "fetch", "-q", "origin", f"ticket/{ticket}",
            env=cur_env, stdout_to_stderr=True,
        ).returncode != 0:
            cmd_util.nogo(_GATE, f"ticket/{ticket} not found on {code_host} for attempt {attempt_str}")
        fetched = _git_output(workspace, "rev-parse", "FETCH_HEAD", env=cur_env)
        if fetched != rework_head:
            cmd_util.nogo(
                _GATE,
                f"ticket/{ticket} on {code_host} is {fetched}, the judged head was {rework_head}",
            )
        base = _git_output(workspace, "merge-base", "HEAD", "FETCH_HEAD", env=cur_env)
        if cmd_util.git_cmd(
            workspace, "checkout", "-q", "-b", f"ticket/{ticket}", "FETCH_HEAD",
            env=cur_env, stdout_to_stderr=True,
        ).returncode != 0:
            raise SystemExit(1)
        start = fetched
    else:
        base = _git_output(workspace, "rev-parse", "HEAD", env=cur_env)
        if cmd_util.git_cmd(
            workspace, "checkout", "-q", "-b", f"ticket/{ticket}",
            env=cur_env, stdout_to_stderr=True,
        ).returncode != 0:
            raise SystemExit(1)
        start = base

    agents_proc = cmd_util.run_py(
        "core/agents.py",
        "crew",
        "--contract", workload_contract,
        "--repo", str(workspace),
        "--rev", base,
        "--role", role,
        "--harness", harness,
        "--member", agent_harness,
        "--out", str(crew_dir / "mcp"),
        env=cur_env,
        capture_stdout=True,
    )
    if agents_proc.returncode != 0:
        cmd_util.nogo(
            _GATE, cmd_util.strip_prefix(agents_proc.stdout.rstrip("\r\n"), "NO-GO: agents — ")
        )
    crew_mcp = agents_proc.stdout.rstrip("\r\n")

    prep_script = Path(workload_contract) / "crew-prepare.sh"
    if prep_script.is_file():
        prep_proc = cmd_util.run_sh(prep_script, str(workspace), env=cur_env)
        if prep_proc.returncode != 0:
            raise SystemExit(prep_proc.returncode)

    raw_map_brief = cur_env.get("MAP_BRIEF")
    map_brief = raw_map_brief if raw_map_brief else "off"
    if map_brief not in ("on", "off"):
        cmd_util.nogo(_GATE, f"MAP_BRIEF is '{raw_map_brief or ''}': on or off (docs/adr/0054 §5)")

    parts: list[Path] = [brief]
    if map_brief == "on":
        map_proc = cmd_util.run_py(
            "core/map.py",
            "slice",
            "--map", cur_env.get("MAP_FILE", ""),
            "--base", base,
            "--ticket", str(crew_dir / "ticket.md"),
            env=cur_env,
            capture_stdout=True,
            capture_stderr=True,
        )
        (crew_dir / "slice.err").write_text(map_proc.stderr, encoding="utf-8")
        if map_proc.returncode != 0:
            err_lines = map_proc.stderr.splitlines()
            cmd_util.nogo(_GATE, err_lines[-1] if err_lines else "")
        slice_md = crew_dir / "slice.md"
        slice_md.write_text(map_proc.stdout, encoding="utf-8")
        parts.append(slice_md)
    if attempt > 1:
        parts.append(Path(rework_file))

    input_chunks = [(crew_dir / "ticket.md").read_text(encoding="utf-8")]
    for part in parts:
        input_chunks.append("\n---\n\n")
        input_chunks.append(part.read_text(encoding="utf-8"))
    (crew_dir / "input.md").write_text("".join(input_chunks), encoding="utf-8")

    (crew_dir / "out").mkdir(parents=True, exist_ok=True)
    sys.stderr.write(f"[crew] ticket {ticket}: role {role}, harness {harness}, attempt {attempt_str}\n")
    sys.stderr.flush()

    budget_proc = cmd_util.run_py(
        "core/spend.py", "token-budget", f"roles/{role}/role.env", env=cur_env, capture_stdout=True
    )
    if budget_proc.returncode != 0:
        raise SystemExit(budget_proc.returncode)

    run_env = dict(role_env)
    run_env["ROLE"] = role
    run_env["TICKET"] = ticket
    run_env["TICKET_FILE"] = str(crew_dir / "input.md")
    run_env["ATTEMPT"] = attempt_str
    run_env["WORKSPACE"] = str(workspace)
    run_env["BASE"] = start
    run_env["OUT"] = str(crew_dir / "out")
    run_env["TOKEN_BUDGET"] = budget_proc.stdout.rstrip("\r\n")
    if crew_mcp:
        run_env["CREW_MCP"] = crew_mcp
    else:
        run_env.pop("CREW_MCP", None)

    if cmd_util.run_sh(
        Path("adapters") / harness / "run.sh", env=run_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.nogo(_GATE, f"{harness} run failed for ticket {ticket}")

    if cmd_util.run_sh(
        "core/harness-check.sh", str(crew_dir / "out"), str(workspace), start, env=cur_env
    ).returncode != 0:
        cmd_util.nogo(_GATE, "worker produced no change or an incomplete artefact")

    diff_proc = cmd_util.git_cmd(
        workspace, "diff", "--no-renames", "--name-only", f"{base}..HEAD",
        env=cur_env, capture_stdout=True,
    )
    if diff_proc.returncode != 0:
        raise SystemExit(diff_proc.returncode)

    perim_proc = cmd_util.run_sh(
        "core/perimeter.sh",
        str(crew_dir / "ticket.md"),
        env=cur_env,
        input_text=diff_proc.stdout,
        capture_stdout=True,
        capture_stderr=True,
    )
    perim_text = (perim_proc.stdout + perim_proc.stderr).rstrip("\r\n")
    if perim_proc.returncode != 0:
        cmd_util.nogo(_GATE, perim_text)

    if cmd_util.run_sh(
        Path("adapters") / code_host / "push.sh",
        str(workspace),
        f"ticket/{ticket}",
        env=cur_env,
        stdout_to_stderr=True,
    ).returncode != 0:
        cmd_util.nogo(_GATE, f"push to {code_host} failed")

    pr_proc = cmd_util.run_sh(
        Path("adapters") / code_host / "open-pr.sh",
        f"ticket/{ticket}",
        str(crew_dir / "out" / "pr.json"),
        env=cur_env,
        capture_stdout=True,
    )
    if pr_proc.returncode != 0:
        cmd_util.nogo(_GATE, f"open-pr on {code_host} failed")
    pr_line = pr_proc.stdout.rstrip("\r\n")
    pr_number, _, pr_url = pr_line.partition(" ")
    head_sha = _git_output(workspace, "rev-parse", "HEAD", env=cur_env)

    (crew_dir / "pr-opened.env").write_text(
        f"PR_NUMBER={pr_number}\nPR_URL={pr_url}\nBASE_SHA={base}\nHEAD_SHA={head_sha}\n",
        encoding="utf-8",
    )
    sys.stderr.write(f"[crew] ticket {ticket}: PR #{pr_number} {pr_url}\n")
    sys.stderr.flush()
    return cmd_util.go(_GATE)


if __name__ == "__main__":
    raise SystemExit(main())
