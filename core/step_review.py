# SPDX-License-Identifier: Apache-2.0
"""Review tower step: build review input, run reviewer harness, post review status."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util, jury
except ImportError:
    import cmd_util
    import jury

_STEP = "step-review"


def _resolve_providers_path(cur_env: dict[str, str]) -> Path | None:
    """Locate providers.json in CWD or relative to the repository checkout."""
    explicit = (cur_env.get("PROVIDERS_FILE") or "").strip()
    if explicit:
        path = Path(explicit)
        return path if path.is_file() else None
    local = Path("profiles") / "providers.json"
    if local.is_file():
        return local
    repo_rel = Path(__file__).resolve().parents[1] / "profiles" / "providers.json"
    return repo_rel if repo_rel.is_file() else None


def select_review_jury(
    cur_env: dict[str, str],
    *,
    ticket: str,
    role: str,
    doc: dict[str, Any] | None,
    default_harness: str,
) -> dict[str, Any] | None:
    """Consult core.jury.select_reviewer for reviewer roles; fail closed on Refused."""
    if role not in jury.REVIEWER_ROLES:
        return None
    providers_path = _resolve_providers_path(cur_env)
    if providers_path is None:
        return None
    providers_cfg = jury.load_providers(str(providers_path))
    crew_dir = (cur_env.get("CREW_DIR") or ".crew").strip() or ".crew"
    run_path = Path(crew_dir) / ticket / "out" / "run.json"
    run_obj: dict[str, Any] | None = None
    if run_path.is_file():
        try:
            loaded = cmd_util.read_json(run_path)
            if isinstance(loaded, dict):
                run_obj = loaded
        except (OSError, ValueError):
            run_obj = None
    run_prov, run_harness = jury._extract_run_actor(run_obj)
    doc_prov, doc_harness = jury._extract_doc_author(doc)
    doc_risk = jury._extract_doc_risk(doc)
    author_provider = (cur_env.get("AUTHOR_PROVIDER") or run_prov or doc_prov or "").strip()
    author_harness = (
        cur_env.get("AUTHOR_HARNESS") or run_harness or doc_harness or default_harness or ""
    ).strip()
    risk = (cur_env.get("RISK_LEVEL") or cur_env.get("RISK") or doc_risk or "low").strip()
    try:
        return jury.select_reviewer(author_provider, author_harness, risk, providers_cfg, cur_env)
    except jury.Refused as exc:
        cmd_util.fail_msg(_STEP, f"jury refused: {exc}")


def prepare_review_workspace(
    cur_env: dict[str, str],
    *,
    ticket: str,
    base_sha: str,
    head_sha: str,
    code_host: str,
    tracker: str,
    role: str,
) -> tuple[Path, dict[str, str], dict[str, Any], str]:
    """Verify role/head, clone workspace, build input document, and return metadata."""
    if not (Path("roles") / role / "prompt.md").is_file():
        cmd_util.fail_msg(_STEP, f"role {role} has no roles/{role}/prompt.md yet")

    role_env = cmd_util.load_role_env(role, cur_env, step=_STEP)
    workload = cur_env.get("WORKLOAD") or "."
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

    review_dir = Path.cwd() / ".crew" / ticket / "review"
    shutil.rmtree(review_dir, ignore_errors=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    workspace = review_dir / "workspace"
    if cmd_util.run_cmd(
        ["git", "clone", "-q", clone_url, str(workspace)],
        env=cur_env,
        stdout_to_stderr=True,
    ).returncode != 0:
        cmd_util.fail_msg(_STEP, f"clone from {code_host} failed")
    if cmd_util.git_cmd(
        workspace, "checkout", "-q", head_sha, env=cur_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.fail_msg(_STEP, f"checkout of {head_sha} failed")

    ticket_proc = cmd_util.run_sh(
        Path("adapters") / tracker / "get.sh", ticket, env=cur_env, capture_stdout=True
    )
    if ticket_proc.returncode != 0:
        cmd_util.fail_msg(_STEP, f"no such ticket: {ticket}")
    (review_dir / "ticket.md").write_text(ticket_proc.stdout, encoding="utf-8")

    contract = cur_env.get("WORKLOAD_CONTRACT") or workload
    agents_proc = cmd_util.run_py(
        "core/agents.py",
        "review",
        "--contract", contract,
        "--role", role,
        "--repo", str(workspace),
        "--base", base_sha,
        "--head", head_sha,
        "--ticket", str(review_dir / "ticket.md"),
        "--out", str(review_dir / "input.md"),
        env=cur_env,
        capture_stdout=True,
    )
    if agents_proc.returncode != 0:
        msg = agents_proc.stdout.rstrip("\r\n") or "step-review: core/agents.py could not build the input"
        sys.stderr.write(f"{msg}\n")
        sys.stderr.flush()
        raise SystemExit(1)

    instructions = json.loads(agents_proc.stdout)
    input_sha256 = cmd_util.sha256_file(review_dir / "input.md")
    return review_dir, role_env, instructions, input_sha256


def parse_verdict(final_path: Path, *, harness: str) -> tuple[str, str, str, str]:
    """Parse ``final.md`` into ``(verdict, state, status, reason)`` or fail closed."""
    if not final_path.is_file():
        cmd_util.fail_msg(_STEP, f"{harness} produced no final.md")
    lines = final_path.read_text(encoding="utf-8").splitlines()
    first_line = lines[0] if lines else ""
    rest = lines[1:]
    while rest and rest[0] == "":
        rest.pop(0)
    reason = "\n".join(rest).rstrip("\r\n")

    if first_line == "APPROVED":
        verdict, state, status = "approved", "ci-passed", "success"
    elif first_line == "CHANGES-REQUESTED":
        verdict, state, status = "changes-requested", "ci-failed", "failure"
    else:
        cmd_util.fail_msg(
            _STEP,
            f"final.md's first line is {first_line or 'empty'}, want APPROVED or CHANGES-REQUESTED",
        )
    if not reason:
        cmd_util.fail_msg(_STEP, f"{verdict} verdict has an empty reason")
    return verdict, state, status, reason


def run_single_review(
    cur_env: dict[str, str],
    review_dir: Path,
    role_env: dict[str, str],
    *,
    role: str,
    harness: str,
    ticket: str,
    attempt: str,
    input_sha256: str,
    instructions: dict[str, Any],
    jury_decision: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Invoke a single reviewer harness run and return ``(state, status, review_payload)``."""
    out_dir = review_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    review_head = cmd_util.git_cmd(
        review_dir / "workspace", "rev-parse", "HEAD", env=cur_env, capture_stdout=True
    ).stdout.rstrip("\r\n")

    run_env = dict(role_env)
    if jury_decision and jury_decision.get("cross_vendor"):
        if jury_decision.get("reviewer_provider"):
            run_env["MODEL_PROVIDER"] = jury_decision["reviewer_provider"]
        if jury_decision.get("reviewer_model"):
            run_env["MODEL"] = jury_decision["reviewer_model"]

    budget_proc = cmd_util.run_py(
        "core/spend.py", "token-budget", f"roles/{role}/role.env", env=run_env, capture_stdout=True
    )
    if budget_proc.returncode != 0:
        raise SystemExit(budget_proc.returncode)

    run_env["ROLE"] = role
    run_env["TICKET"] = ticket
    run_env["ATTEMPT"] = attempt
    run_env["WORKSPACE"] = str(review_dir / "workspace")
    run_env["BASE"] = review_head
    run_env["OUT"] = str(out_dir)
    run_env["TICKET_FILE"] = str(review_dir / "input.md")
    run_env["TOKEN_BUDGET"] = budget_proc.stdout.rstrip("\r\n")

    if cmd_util.run_sh(
        Path("adapters") / harness / "run.sh", env=run_env, stdout_to_stderr=True
    ).returncode != 0:
        cmd_util.fail_msg(_STEP, f"{harness} run failed for ticket {ticket}")

    verdict, state, status, reason = parse_verdict(out_dir / "final.md", harness=harness)

    recorded_sha256 = ""
    run_json = out_dir / "run.json"
    if run_json.is_file():
        try:
            rec = cmd_util.read_json(run_json)
            if isinstance(rec, dict):
                if isinstance(rec.get("recorded"), dict):
                    recorded_sha256 = rec["recorded"].get("input_sha256") or ""
                if jury_decision is not None:
                    rec["jury"] = jury_decision
                    run_json.write_text(json.dumps(rec, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError):
            recorded_sha256 = ""
    input_matches = (recorded_sha256 == input_sha256) if (harness == "replay" and recorded_sha256) else True

    task_proc = cmd_util.run_py("core/tower.py", "task", str(run_json), env=cur_env, capture_stdout=True)
    if task_proc.returncode != 0:
        raise SystemExit(task_proc.returncode)
    task = json.loads(task_proc.stdout)

    review_obj = {
        **task,
        "role": role,
        "verdict": verdict,
        "reason": reason,
        "input_sha256": input_sha256,
        "input_matches_recorded": input_matches,
        "instructions": instructions,
    }
    if jury_decision is not None:
        review_obj["jury"] = jury_decision
    return state, status, review_obj


def post_review_status(
    cur_env: dict[str, str],
    *,
    code_host: str,
    head_sha: str,
    status: str,
    verdict: str,
    reason: str,
) -> None:
    """Publish the review verdict as the PR's ``review`` commit status."""
    first_reason_line = reason.splitlines()[0] if reason else ""
    if cmd_util.run_sh(
        Path("adapters") / code_host / "status.sh",
        head_sha,
        status,
        "review",
        f"{verdict}: {first_reason_line}",
        env=cur_env,
    ).returncode != 0:
        cmd_util.fail_msg(_STEP, "could not post the status")


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    """Prepare workspace, run review role, publish commit status, and emit step JSON."""
    _ = argv
    cur_env = dict(os.environ if env is None else env)
    ticket = cmd_util.need_env(cur_env, "TICKET", "TICKET not set")
    cmd_util.need_env(cur_env, "PR_NUMBER", "PR_NUMBER not set")
    base_sha = cmd_util.need_env(cur_env, "BASE_SHA", "BASE_SHA not set")
    head_sha = cmd_util.need_env(cur_env, "HEAD_SHA", "HEAD_SHA not set")
    code_host = cmd_util.need_env(cur_env, "CODE_HOST", "CODE_HOST not set; run this via ./go, not directly")
    tracker = cmd_util.need_env(cur_env, "TRACKER", "TRACKER not set; run this via ./go, not directly")
    agent_harness = cmd_util.need_env(
        cur_env, "AGENT_HARNESS", "AGENT_HARNESS not set; run this via ./go, not directly"
    )
    role = cur_env.get("ROLE") or "test-reviewer"

    review_dir, role_env, instructions, input_sha256 = prepare_review_workspace(
        cur_env,
        ticket=ticket,
        base_sha=base_sha,
        head_sha=head_sha,
        code_host=code_host,
        tracker=tracker,
        role=role,
    )
    default_harness = role_env.get("HARNESS") or agent_harness
    attempt = "1"
    doc: dict[str, Any] | None = None
    doc_file = cur_env.get("DOC_FILE") or ""
    if doc_file:
        loaded = cmd_util.read_json(doc_file)
        if isinstance(loaded, dict):
            doc = loaded
            if loaded.get("attempt") is not None:
                attempt = str(loaded["attempt"])

    jury_decision = select_review_jury(
        cur_env,
        ticket=ticket,
        role=role,
        doc=doc,
        default_harness=default_harness,
    )
    if (
        jury_decision
        and jury_decision.get("cross_vendor")
        and jury_decision.get("reviewer_harness")
        and not role_env.get("HARNESS")
    ):
        harness = jury_decision["reviewer_harness"]
        sys.stderr.write(f"[review] jury: {jury_decision['reason']}\n")
        sys.stderr.flush()
    else:
        harness = default_harness

    state, status, review_obj = run_single_review(
        cur_env,
        review_dir,
        role_env,
        role=role,
        harness=harness,
        ticket=ticket,
        attempt=attempt,
        input_sha256=input_sha256,
        instructions=instructions,
        jury_decision=jury_decision,
    )
    post_review_status(
        cur_env,
        code_host=code_host,
        head_sha=head_sha,
        status=status,
        verdict=review_obj["verdict"],
        reason=review_obj["reason"],
    )
    payload = {"state": state, "review": review_obj}
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
