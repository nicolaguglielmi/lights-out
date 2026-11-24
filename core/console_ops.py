"""Control-plane workstation operations bridge (docs/adr/0125 §1): stdlib only.

Exposes three subcommands called via `core/console_ops.sh`:
  - `rollback --reason <text> [--by <who>]`: engages `WORKLOAD_KILL_SWITCH`
    when configured via `FLAGS` (`set.sh <flag> on`), executes `DELIVERY`
    `rollback.sh`, and appends an audit JSONL record outside `console/`.
  - `freeze --state on|off --reason <text> [--by <who>]`: toggles the
    release freeze flag (`RELEASE_FREEZE_FLAG` or `release-freeze`) via
    `FLAGS` `set.sh` and appends an audit JSONL record.
  - `charts [--window-s <n>] [--step-s <n>] [--query <expr>]...`: queries the
    `CHARTS` port through `core.obs.query_charts_series` when configured, or
    prints `{"ok": false, "error": "CHARTS is not configured"}` cleanly.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import pathlib
import re
import subprocess  # nosec B404 - explicit argv to port adapters only (ADR 0125 §1)
import sys
import time

try:
    from . import adapters, obs
except ImportError:  # run as a script: core/ is sys.path[0]
    import adapters
    import obs

# Provenance: docs/adr/0125, "Provenance".
OPS_TIMEOUT_S = 30
MAX_REASON_LEN = 512
DEFAULT_WINDOW_S = 900
MIN_WINDOW_S = 60
MAX_WINDOW_S = 86400
DEFAULT_STEP_S = 15
MIN_STEP_S = 1
MAX_STEP_S = 3600
DEFAULT_FREEZE_FLAG = "release-freeze"
DEFAULT_AUDIT_LOG = os.path.join(".crew", "console-ops.jsonl")
_PLAIN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


class Refused(Exception):
    """Raised when an operator action or port contract is invalid."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean_reason(raw: str | None) -> str:
    text = " ".join((raw or "").split())
    if not text:
        raise Refused("reason is required and must be non-empty")
    if len(text) > MAX_REASON_LEN:
        raise Refused(f"reason exceeds {MAX_REASON_LEN} characters")
    return text


def _clean_actor(raw: str | None) -> str:
    actor = " ".join((raw or "").split())
    return actor[:128] if actor else "unauthenticated"


def _timeout_s(env: dict[str, str]) -> int:
    raw = (env.get("CONSOLE_OPS_TIMEOUT_S") or "").strip()
    if not raw:
        return OPS_TIMEOUT_S
    try:
        value = int(raw)
    except ValueError:
        return OPS_TIMEOUT_S
    return max(1, value)


def _audit_path(env: dict[str, str]) -> pathlib.Path:
    raw = (env.get("CONSOLE_OPS_LOG") or DEFAULT_AUDIT_LOG).strip()
    target = pathlib.Path(raw)
    cwd_console = (pathlib.Path.cwd() / "console").resolve()
    resolved = (pathlib.Path.cwd() / target).resolve() if not target.is_absolute() else target.resolve()
    if resolved == cwd_console or cwd_console in resolved.parents or (target.parts and target.parts[0] == "console"):
        raise Refused("CONSOLE_OPS_LOG must be outside console/")
    return resolved


def _append_audit(env: dict[str, str], record: dict[str, object]) -> None:
    path = _audit_path(env)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


def _resolve_port(env: dict[str, str], port: str, override_key: str, script_name: str) -> tuple[str, str]:
    """Return `(label, adapter_dir)` for `port`, honoring `<PORT>_ADAPTER` or `outside/`."""
    override = (env.get(override_key) or "").strip()
    if override:
        raw = override
        allow_dir = True
    else:
        raw = (env.get(port) or "").strip()
        allow_dir = False
    if not raw:
        raise Refused(f"{port} is not configured")

    if raw.startswith(adapters.PREFIX):
        try:
            outside_name = adapters.outside_name(port, raw)
        except adapters.Refused as exc:
            raise Refused(str(exc)) from None
        linked = os.path.join("adapters", "outside", outside_name)
        if os.path.isdir(linked):
            chosen = linked
        else:
            try:
                chosen = str(adapters.adapter_dir(env, port, raw, outside_name))
            except adapters.Refused as exc:
                raise Refused(str(exc)) from None
    elif _PLAIN.fullmatch(raw):
        chosen = os.path.join("adapters", raw)
    elif allow_dir and ".." not in pathlib.Path(raw).parts and os.path.isdir(raw):
        chosen = raw
    else:
        raise Refused(f"{port}={raw!r} is not a plain adapter name or safe directory")

    script = os.path.join(chosen, script_name)
    if not os.path.isfile(script):
        raise Refused(f"{port}: missing {script}")
    return raw, chosen


def _run_adapter(argv: list[str], env: dict[str, str], timeout_s: int) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(  # nosec B603 - explicit argv to adapter script
            argv,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(argv)} timed out after {timeout_s}s"
    except OSError as exc:
        return 127, "", f"{' '.join(argv)} failed: {exc}"


def _cmd_rollback(args: argparse.Namespace, env: dict[str, str]) -> int:
    reason = _clean_reason(args.reason)
    actor = _clean_actor(args.by)
    timeout_s = _timeout_s(env)
    kill_switch = (env.get("WORKLOAD_KILL_SWITCH") or "").strip()

    if kill_switch:
        if not _PLAIN.fullmatch(kill_switch):
            raise Refused("WORKLOAD_KILL_SWITCH is not a plain flag name")
        _, flags_dir = _resolve_port(env, "FLAGS", "FLAGS_ADAPTER", "set.sh")
        rc, out, err = _run_adapter(["sh", os.path.join(flags_dir, "set.sh"), kill_switch, "on"], env, timeout_s)
        if rc != 0:
            _append_audit(env, {"ts": _stamp(), "action": "rollback", "step": "kill-switch",
                                "kill_switch": kill_switch, "by": actor, "reason": reason,
                                "verdict": "failed", "rc": rc})
            msg = err or out or f"exit {rc}"
            raise Refused(f"kill-switch {kill_switch} on failed: {msg}")

    _, delivery_dir = _resolve_port(env, "DELIVERY", "DELIVERY_ADAPTER", "rollback.sh")
    rc, out, err = _run_adapter(["sh", os.path.join(delivery_dir, "rollback.sh")], env, timeout_s)
    if rc != 0:
        _append_audit(env, {"ts": _stamp(), "action": "rollback", "step": "delivery",
                            "kill_switch": kill_switch or None, "by": actor, "reason": reason,
                            "verdict": "failed", "rc": rc})
        msg = err or out or f"exit {rc}"
        raise Refused(f"DELIVERY rollback.sh failed: {msg}")

    _append_audit(env, {"ts": _stamp(), "action": "rollback", "kill_switch": kill_switch or None,
                        "by": actor, "reason": reason, "verdict": "applied", "rc": 0})
    ks_label = kill_switch if kill_switch else "none"
    print(f"GO: rollback — by={actor}; kill_switch={ks_label}; reason={reason}")
    return 0


def _cmd_freeze(args: argparse.Namespace, env: dict[str, str]) -> int:
    state = (args.state or "").strip()
    if state not in ("on", "off"):
        raise Refused("--state must be 'on' or 'off'")
    reason = _clean_reason(args.reason)
    actor = _clean_actor(args.by)
    flag = (env.get("RELEASE_FREEZE_FLAG") or DEFAULT_FREEZE_FLAG).strip()
    if not _PLAIN.fullmatch(flag):
        raise Refused("RELEASE_FREEZE_FLAG is not a plain flag name")

    _, flags_dir = _resolve_port(env, "FLAGS", "FLAGS_ADAPTER", "set.sh")
    rc, out, err = _run_adapter(["sh", os.path.join(flags_dir, "set.sh"), flag, state], env, _timeout_s(env))
    if rc != 0:
        _append_audit(env, {"ts": _stamp(), "action": "freeze", "flag": flag, "state": state,
                            "by": actor, "reason": reason, "verdict": "failed", "rc": rc})
        msg = err or out or f"exit {rc}"
        raise Refused(f"FLAGS set.sh {flag} {state} failed: {msg}")

    _append_audit(env, {"ts": _stamp(), "action": "freeze", "flag": flag, "state": state,
                        "by": actor, "reason": reason, "verdict": "applied", "rc": 0})
    print(f"GO: freeze — state={state}; flag={flag}; by={actor}; reason={reason}")
    return 0


def _chart_queries(args: argparse.Namespace, env: dict[str, str]) -> list[str]:
    if args.query:
        items = [q.strip() for q in args.query if q and q.strip()]
        if items:
            return items
    raw = (env.get("CHARTS_QUERIES") or "").strip()
    if raw:
        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    items = [str(x).strip() for x in parsed if isinstance(x, str) and x.strip()]
                    if items:
                        return items
            except ValueError:
                pass
        items = [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]
        if items:
            return items
    return ["up"]


def _cmd_charts(args: argparse.Namespace, env: dict[str, str]) -> int:
    has_port = bool((env.get("CHARTS_ADAPTER") or env.get("CHARTS") or "").strip())
    if not has_port:
        print(json.dumps({"ok": False, "error": "CHARTS is not configured"}, separators=(",", ":")))
        return 0
    window_s = max(MIN_WINDOW_S, min(MAX_WINDOW_S, int(args.window_s)))
    step_s = max(MIN_STEP_S, min(MAX_STEP_S, int(args.step_s)))
    try:
        charts_label, charts_dir = _resolve_port(env, "CHARTS", "CHARTS_ADAPTER", "query-range.sh")
        queries = _chart_queries(args, env)
        end_ts = int(time.time())
        start_ts = end_ts - window_s
        data = obs.query_charts_series(charts_dir, queries, start_ts, end_ts, step_s=step_s, timeout_s=_timeout_s(env))
        payload = {
            "ok": True,
            "charts": charts_label,
            "window_s": window_s,
            "step_s": step_s,
            "start_ts": start_ts,
            "end_ts": end_ts,
            "series": data["series"],
        }
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    except (Refused, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        print(f"console_ops charts: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="console_ops.py", add_help=True)
    sub = parser.add_subparsers(dest="cmd", required=True)

    rb = sub.add_parser("rollback", help="Engage WORKLOAD_KILL_SWITCH (if set) and run DELIVERY rollback.sh")
    rb.add_argument("--reason", required=True)
    rb.add_argument("--by", default="unauthenticated")

    fr = sub.add_parser("freeze", help="Toggle release freeze flag via FLAGS set.sh")
    fr.add_argument("--state", required=True, choices=("on", "off"))
    fr.add_argument("--reason", required=True)
    fr.add_argument("--by", default="unauthenticated")

    ch = sub.add_parser("charts", help="Query CHARTS port range series")
    ch.add_argument("--window-s", type=int, default=DEFAULT_WINDOW_S)
    ch.add_argument("--step-s", type=int, default=DEFAULT_STEP_S)
    ch.add_argument("--query", action="append", default=[])
    return parser


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    cfg = dict(os.environ if env is None else env)
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        if code != 0:
            print("NO-GO: console-ops — invalid arguments", file=sys.stdout)
        return code

    try:
        if args.cmd == "rollback":
            return _cmd_rollback(args, cfg)
        if args.cmd == "freeze":
            return _cmd_freeze(args, cfg)
        if args.cmd == "charts":
            return _cmd_charts(args, cfg)
        raise Refused(f"unknown command {args.cmd!r}")
    except Refused as exc:
        print(f"NO-GO: {args.cmd} — {exc}")
        print(f"console_ops {args.cmd}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
