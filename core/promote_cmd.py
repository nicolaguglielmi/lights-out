# SPDX-License-Identifier: Apache-2.0
"""Human go-for-launch decision gate promoting or rolling back a held canary."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from . import cmd_util
    from . import releases as releases_mod
except ImportError:
    import cmd_util
    import releases as releases_mod

_GATE = "go-for-launch"
_SAFE_RUN_KEYS = (
    "harness",
    "model",
    "provider",
    "thinking",
    "turns",
    "tokens",
    "duration_s",
    "input_sha256",
    "replayed",
    "failed",
    "stop",
    "source",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sanitize_tokens(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    clean: dict[str, int] = {}
    for k in ("input", "output", "cached", "total"):
        val = raw.get(k)
        if isinstance(val, int) and not isinstance(val, bool):
            clean[k] = val
    return clean or None


def sanitize_brief_run(raw: Any, *, at: str | None = None) -> dict[str, Any] | None:
    """Copy safe telemetry fields from a release role `run.json` without secrets."""
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {"role": "release"}
    for key in _SAFE_RUN_KEYS:
        if key not in raw:
            continue
        if key == "tokens":
            toks = _sanitize_tokens(raw.get("tokens"))
            if toks is not None:
                out["tokens"] = toks
        else:
            val = raw.get(key)
            if isinstance(val, (str, int, float, bool)) or val is None:
                out[key] = val
    recorded = raw.get("recorded")
    if isinstance(recorded, dict):
        rec_clean = {k: recorded[k] for k in _SAFE_RUN_KEYS if k in recorded and k != "tokens"}
        rec_tokens = _sanitize_tokens(recorded.get("tokens"))
        if rec_tokens is not None:
            rec_clean["tokens"] = rec_tokens
        if rec_clean:
            out["recorded"] = rec_clean
    if "replayed" not in out:
        out["replayed"] = out.get("harness") == "replay"
    stamp = raw.get("at")
    out["at"] = stamp if isinstance(stamp, str) and stamp else (at or _utc_now())
    return out


def _capture_brief_run(promote_dir: Path) -> dict[str, Any] | None:
    """Read, sanitize, and persist `promote_dir/out/run.json` to `brief_run.json`."""
    run_path = promote_dir / "out" / "run.json"
    if not run_path.is_file():
        return None
    try:
        raw = cmd_util.read_json(run_path)
    except (OSError, ValueError):
        return None
    brief_run = sanitize_brief_run(raw)
    if brief_run is None:
        return None
    (promote_dir / "brief_run.json").write_text(
        json.dumps(brief_run, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return brief_run


def _load_brief_run(promote_dir: Path) -> dict[str, Any] | None:
    saved = promote_dir / "brief_run.json"
    if saved.is_file():
        try:
            data = cmd_util.read_json(saved)
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    return _capture_brief_run(promote_dir)


def _record_brief_run(
    cur_env: dict[str, str],
    version: str,
    brief_run: dict[str, Any] | None,
) -> None:
    """Attach optional ``brief_run`` to the version's latest release entry in ``RELEASES_LOG``."""
    if not brief_run:
        return
    log_path = Path(cur_env.get("RELEASES_LOG") or ".crew/releases.jsonl")
    if not log_path.is_file():
        return
    try:
        with releases_mod.ReleaseTrainLock(env=dict(cur_env)):
            raw_lines = log_path.read_text(encoding="utf-8").splitlines()
            parsed: list[dict[str, Any] | None] = []
            release_idx: int | None = None
            answer_idx: int | None = None
            for idx, line in enumerate(raw_lines):
                if not line.strip():
                    parsed.append(None)
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    return
                parsed.append(obj)
                if isinstance(obj, dict):
                    if (
                        obj.get("kind") == "release"
                        and isinstance(obj.get("record"), dict)
                        and str(obj["record"].get("version")) == version
                    ):
                        release_idx = idx
                    elif obj.get("kind") == "answer" and str(obj.get("version")) == version:
                        answer_idx = idx
            target_idx = release_idx if release_idx is not None else answer_idx
            if target_idx is None:
                return
            target = parsed[target_idx]
            if not isinstance(target, dict) or target.get("brief_run") == brief_run:
                return
            updated = dict(target, brief_run=brief_run)
            out_lines = [
                json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
                if idx == target_idx
                else raw_lines[idx]
                for idx in range(len(raw_lines))
            ]
            tmp = log_path.with_name(f".{log_path.name}.{os.getpid()}.tmp")
            tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            os.replace(tmp, log_path)
    except (OSError, releases_mod.ReleaseTrainTimeout):
        return


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
    rc = cmd_util.run_sh(
        Path("adapters") / harness / "run.sh", env=run_env, stdout_to_stderr=True
    ).returncode
    brief_run = _capture_brief_run(promote_dir)
    if brief_run is not None:
        _record_brief_run(cur_env, version, brief_run)
    if rc != 0:
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
        _record_brief_run(cur_env, str(version), _load_brief_run(promote_dir))

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
        _record_brief_run(cur_env, str(version), _load_brief_run(promote_dir))
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
    _record_brief_run(cur_env, str(version), _load_brief_run(promote_dir))
    cmd_util.run_py("core/promote_hint.py", str(version), env=cur_env)
    return cmd_util.go(_GATE)


if __name__ == "__main__":
    raise SystemExit(main())
