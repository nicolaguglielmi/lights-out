# SPDX-License-Identifier: Apache-2.0
"""CI tower step: check perimeter, run base-restored gates in worktree, post gates status."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util
except ImportError:
    import cmd_util

_STEP = "step-ci"


def _parse_seconds(seconds_path: Path) -> dict[str, int | float] | None:
    if not seconds_path.is_file():
        return None
    out: dict[str, int | float] = {}
    for line in seconds_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split(" ")
        if len(parts) < 2:
            continue
        key, raw = parts[0], parts[1]
        num: int | float = float(raw) if "." in raw or "e" in raw.lower() else int(raw)
        out[key] = num
    return out or None


def _parse_sast(failed: str, gates_dir: Path) -> list[dict[str, Any]] | None:
    if failed != "sast":
        return None
    sast_path = gates_dir / "sast.json"
    if not sast_path.is_file():
        return None
    try:
        data = cmd_util.read_json(sast_path)
    except (OSError, ValueError):
        return None
    findings = data.get("new") if isinstance(data, dict) else None
    if not isinstance(findings, list) or not findings:
        return None
    return [
        {"rule": f.get("rule"), "file": f.get("file"), "line": f.get("line")}
        for f in findings
        if isinstance(f, dict)
    ] or None


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Validate PR head on code host, run isolated gates, post commit status, emit step JSON."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    ticket = cmd_util.need_env(cur_env, "TICKET", "TICKET not set")
    cmd_util.need_env(cur_env, "PR_NUMBER", "PR_NUMBER not set")
    base_sha = cmd_util.need_env(cur_env, "BASE_SHA", "BASE_SHA not set")
    head_sha = cmd_util.need_env(cur_env, "HEAD_SHA", "HEAD_SHA not set")
    code_host = cmd_util.need_env(cur_env, "CODE_HOST", "CODE_HOST not set; run this via ./go, not directly")
    tracker = cmd_util.need_env(cur_env, "TRACKER", "TRACKER not set; run this via ./go, not directly")
    workload = cmd_util.need_env(cur_env, "WORKLOAD", "WORKLOAD not set; run this via ./go, not directly")
    workload_contract = cmd_util.need_env(
        cur_env, "WORKLOAD_CONTRACT", "WORKLOAD_CONTRACT not set; run this via ./go, not directly"
    )
    gates_out = cur_env.get("GATES_OUT") or ".gates-out"

    branch = f"ticket/{ticket}"
    clone_proc = cmd_util.run_sh(
        Path("adapters") / code_host / "clone-url.sh", env=cur_env, capture_stdout=True
    )
    if clone_proc.returncode != 0:
        cmd_util.fail_msg(_STEP, f"could not fetch {branch} from {code_host}")
    clone_url = clone_proc.stdout.rstrip("\r\n")

    if cmd_util.git_cmd(workload, "fetch", "-q", clone_url, branch, env=cur_env).returncode != 0:
        cmd_util.fail_msg(_STEP, f"could not fetch {branch} from {code_host}")
    fetched = cmd_util.git_cmd(
        workload, "rev-parse", "FETCH_HEAD", env=cur_env, capture_stdout=True
    ).stdout.rstrip("\r\n")
    if fetched != head_sha:
        cmd_util.fail_msg(
            _STEP, f"{branch} on {code_host} is {fetched}, the PR was opened at {head_sha}"
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wt = tmp_path / "wt"
        try:
            mb_proc = cmd_util.git_cmd(
                workload, "merge-base", base_sha, head_sha, env=cur_env, capture_stdout=True
            )
            if mb_proc.returncode != 0:
                cmd_util.fail_msg(_STEP, f"no merge-base between {base_sha} and {head_sha}")
            merge_base = mb_proc.stdout.rstrip("\r\n")

            ticket_proc = cmd_util.run_sh(
                Path("adapters") / tracker / "get.sh", ticket, env=cur_env, capture_stdout=True
            )
            if ticket_proc.returncode != 0:
                cmd_util.fail_msg(_STEP, f"{tracker} has no ticket {ticket}")
            ticket_path = tmp_path / "ticket.md"
            ticket_path.write_text(ticket_proc.stdout, encoding="utf-8")

            diff_proc = cmd_util.git_cmd(
                workload, "diff", "--no-renames", "--name-only", merge_base, head_sha,
                env=cur_env, capture_stdout=True,
            )
            if diff_proc.returncode != 0:
                raise SystemExit(diff_proc.returncode)
            perm_proc = cmd_util.run_sh(
                "core/perimeter.sh",
                str(ticket_path),
                env=cur_env,
                input_text=diff_proc.stdout,
                capture_stdout=True,
                capture_stderr=True,
            )
            perimeter = (perm_proc.stdout + perm_proc.stderr).rstrip("\r\n")
            perimeter_rc = perm_proc.returncode

            gates_out_dir = Path(gates_out)
            gates_out_dir.mkdir(parents=True, exist_ok=True)
            details_file = tmp_path / "details"
            details_file.write_text("", encoding="utf-8")
            gates_dir: Path | None = None

            if perimeter_rc != 0:
                out = "NO-GO: perimeter — " + perimeter.replace("\n", " ")
                details_file.write_text(f"{perimeter}\n", encoding="utf-8")
                (tmp_path / "lines").write_text(f"{out}\n", encoding="utf-8")
                (gates_out_dir / "verdict.env").write_text(
                    "GATES=NO-GO\nFAILED=perimeter\nRISK_LEVEL=unknown\nRISK_DECISION=unknown\n",
                    encoding="utf-8",
                )
            else:
                wt_proc = cmd_util.git_cmd(
                    workload, "worktree", "add", "--detach", str(wt), "FETCH_HEAD",
                    env=cur_env, suppress_stdout=True, capture_stderr=True,
                )
                if wt_proc.returncode != 0:
                    cmd_util.fail_msg(_STEP, f"git worktree add failed: {wt_proc.stderr.rstrip()}")

                pref_proc = cmd_util.git_cmd(
                    workload_contract, "rev-parse", "--show-prefix", env=cur_env, capture_stdout=True
                )
                if pref_proc.returncode != 0:
                    cmd_util.fail_msg(
                        _STEP, f"{workload_contract} is not inside a git repository"
                    )
                contract = pref_proc.stdout.rstrip("\r\n")

                if cur_env.get("WORKLOAD_ATTACHED"):
                    judges = [f"{contract}workload.env", f"{contract}policy.env", f"{contract}runners"]
                else:
                    judges = [
                        "Makefile", "profiles", "gates", "ci", "core", "policy",
                        f"{contract}workload.env", f"{contract}policy.env", f"{contract}runners",
                    ]
                present = [
                    j for j in judges
                    if cmd_util.git_cmd(
                        wt, "cat-file", "-e", f"{base_sha}:{j}",
                        env=cur_env, suppress_stdout=True, suppress_stderr=True,
                    ).returncode == 0
                    or (wt / j).exists()
                ]
                if present:
                    rest_proc = cmd_util.git_cmd(
                        wt, "restore", f"--source={base_sha}", "--staged", "--worktree", "--", *present,
                        env=cur_env,
                    )
                    if rest_proc.returncode != 0:
                        joined = " ".join(present)
                        cmd_util.fail_msg(_STEP, f"could not take {joined} from the base {base_sha}")

                if cur_env.get("WORKLOAD_ATTACHED"):
                    gates_dir = tmp_path / "gates-out"
                    make_proc = cmd_util.run_cmd(
                        [
                            "make", "--no-print-directory", "gates",
                            f"WORKLOAD={wt}", f"GATES_OUT={gates_dir}",
                            f"BASE={base_sha}", f"HEAD={head_sha}",
                            "TOOLCHAIN_ISOLATE=1", f"GATES_TICKET={ticket_path}",
                        ],
                        env=cur_env,
                        capture_stdout=True,
                    )
                else:
                    gates_dir = wt / gates_out
                    make_proc = cmd_util.run_cmd(
                        [
                            "make", "--no-print-directory", "gates",
                            f"BASE={base_sha}", f"HEAD={head_sha}",
                            "TOOLCHAIN_ISOLATE=1", f"GATES_TICKET={ticket_path}",
                        ],
                        env=cur_env,
                        cwd=wt,
                        capture_stdout=True,
                    )
                out = make_proc.stdout.rstrip("\r\n")
                src_verdict = gates_dir / "verdict.env"
                dst_verdict = gates_out_dir / "verdict.env"
                if src_verdict.is_file():
                    shutil.copyfile(src_verdict, dst_verdict)
                else:
                    dst_verdict.write_text("", encoding="utf-8")

            sys.stderr.write(f"[ci] gates: {out}\n")
            sys.stderr.flush()

            verdict_map = cmd_util.parse_env_file(gates_out_dir / "verdict.env")
            verdict = verdict_map.get("GATES", "")
            if not verdict:
                cmd_util.fail_msg(_STEP, "gates left no verdict")
            level = verdict_map.get("RISK_LEVEL") or "unknown"
            decision = verdict_map.get("RISK_DECISION") or "unknown"
            failed = verdict_map.get("FAILED", "")

            if failed and failed != "perimeter" and gates_dir is not None:
                cand = gates_dir / f"{failed}.details"
                if cand.is_file():
                    shutil.copyfile(cand, details_file)
            details = details_file.read_text(encoding="utf-8").splitlines()

            if verdict == "GO" and decision == "auto-merge":
                state, status = "ci-passed", "success"
            elif verdict == "GO":
                state, status = "review", "success"
            else:
                state, status = "ci-failed", "failure"

            if cmd_util.run_sh(
                Path("adapters") / code_host / "status.sh",
                head_sha,
                status,
                "gates",
                out,
                env=cur_env,
            ).returncode != 0:
                cmd_util.fail_msg(_STEP, "could not post the status")

            if state == "review":
                sys.stderr.write(
                    f"[ci] risk {level}: review requested; test-reviewer picks it up next (docs/adr/0021)\n"
                )
                sys.stderr.flush()

            meta_dir = gates_dir if gates_dir is not None else tmp_path
            seconds = _parse_seconds(meta_dir / "seconds")
            sast = _parse_sast(failed, meta_dir)
            lines_path = meta_dir / "lines"
            lines = lines_path.read_text(encoding="utf-8").splitlines() if lines_path.is_file() else None

            payload = {
                "state": state,
                "ci": {
                    "verdict": verdict,
                    "failed": failed or None,
                    "risk_level": level,
                    "decision": decision,
                    "head_sha": head_sha,
                    "line": out,
                    "details": details,
                    "sast": sast,
                    "lines": lines,
                    "seconds": seconds,
                },
            }
            sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            return 0
        finally:
            cmd_util.git_cmd(
                workload, "worktree", "remove", "--force", str(wt),
                env=cur_env, suppress_stdout=True, suppress_stderr=True,
            )


if __name__ == "__main__":
    raise SystemExit(main())
