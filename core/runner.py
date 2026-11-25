"""Continuous orchestrator daemon (docs/adr/0109): stdlib only.

Drains or continuously polls adapters/$EVENT_BUS/{pull,ack}.sh and invokes
core/tower.sh step <ticket> <step>, matching adapters/inproc/run.sh's exact
protocol with lockfile exclusivity and graceful SIGTERM/SIGINT shutdown.
Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import errno
import fcntl
import json
import os
import signal
import subprocess  # nosec B404 - invokes adapter scripts and core/tower.sh (ADR 0052)
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

try:
    from . import releases
except ImportError:  # run as a script: core/ is sys.path[0]
    import releases

DEFAULT_POLL_S = 5.0
DEFAULT_LOCKFILE = os.path.join(".crew", "runner.lock")
DEFAULT_WORKERS = 1
# Provenance, decision (docs/adr/0120): bounds concurrent worker threads in 1..8
# to match single-host container capacity and PR gate fanout without unbounded spawning.
MAX_RUNNER_WORKERS = 8
# Provenance, decision (docs/adr/0120): serialize merge, mainline verification,
# and canary release across the fleet so main and release state never race.
SERIAL_STEPS = ("merge", "mainline", "release")


class LockError(Exception):
    """Raised when another runner instance already holds the lockfile."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(event: str, **fields: Any) -> None:
    entry = {"ts": _utc_now(), "event": event, "pid": os.getpid(), **fields}
    print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")), file=sys.stderr, flush=True)


@contextlib.contextmanager
def acquire_lock(lockfile: str) -> Iterator[Path]:
    """Acquire an exclusive non-blocking flock on lockfile for the runner lifetime."""
    path = Path(lockfile)
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                raise LockError(f"another runner holds lockfile {lockfile}") from None
            raise LockError(f"cannot lock {lockfile}: {exc}") from None
        fh.seek(0)
        fh.truncate()
        fh.write(f"{os.getpid()}\n")
        fh.flush()
        yield path
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def _run_sh(script: str, args: list[str], env: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # nosec B603 B607 - fixed script path with explicit argv list
        ["sh", script, *args],
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        sys.stderr.flush()
    return proc


def resolve_workers(cli_workers: int | None, env: Mapping[str, str]) -> int:
    """Resolve bounded worker count in 1..MAX_RUNNER_WORKERS from --workers or RUNNER_WORKERS."""
    if cli_workers is not None:
        workers = cli_workers
        src = "--workers"
    else:
        raw = (env.get("RUNNER_WORKERS") or "").strip()
        if not raw:
            return DEFAULT_WORKERS
        try:
            workers = int(raw)
        except ValueError:
            raise ValueError(f"RUNNER_WORKERS must be an integer in 1..{MAX_RUNNER_WORKERS}, got {raw!r}") from None
        src = "RUNNER_WORKERS"
    if workers < 1 or workers > MAX_RUNNER_WORKERS:
        raise ValueError(f"{src} must be between 1 and {MAX_RUNNER_WORKERS}, got {workers}")
    return workers


def _parse_message(msg: str) -> tuple[str, str, str]:
    ackid, sep, data = msg.partition(" ")
    if not sep:
        data = ""
    ticket = ""
    step = ""
    try:
        parsed = json.loads(data) if data else None
        if isinstance(parsed, dict):
            t_val = parsed.get("ticket")
            s_val = parsed.get("step")
            ticket = t_val if isinstance(t_val, str) else ""
            step = s_val if isinstance(s_val, str) else ""
    except ValueError:
        pass
    return ackid, ticket, step


def _run_sh_locked(
    script: str,
    args: list[str],
    env: Mapping[str, str],
    log_lock: threading.Lock,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(  # nosec B603 B607 - fixed script path with explicit argv list
        ["sh", script, *args],
        env=dict(env),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.stderr:
        with log_lock:
            sys.stderr.write(proc.stderr)
            sys.stderr.flush()
    return proc


def _drain_once_parallel(
    bus: str,
    env: Mapping[str, str],
    workers: int,
    stop_event: threading.Event | None = None,
) -> tuple[int, int, bool]:
    """Drain queued events in bounded parallel batches (docs/adr/0120)."""
    pull_script = os.path.join("adapters", bus, "pull.sh")
    ack_script = os.path.join("adapters", bus, "ack.sh")
    tower_script = os.path.join("core", "tower.sh")

    log_lock = threading.Lock()
    ack_lock = threading.Lock()
    fleet_serial_lock = threading.Lock()
    ticket_locks: dict[str, threading.Lock] = {}
    ticket_locks_guard = threading.Lock()

    def locked_log(event: str, **fields: Any) -> None:
        with log_lock:
            _log(event, **fields)

    def get_ticket_lock(ticket: str) -> threading.Lock:
        with ticket_locks_guard:
            lock = ticket_locks.get(ticket)
            if lock is None:
                lock = threading.Lock()
                ticket_locks[ticket] = lock
            return lock

    def ack_msg(ackid: str) -> int:
        with ack_lock:
            return _run_sh_locked(ack_script, [ackid], env, log_lock).returncode

    def run_ticket_items(ticket: str, items: list[tuple[str, str]]) -> tuple[int, int, str | None, int]:
        t_processed = 0
        t_failed = 0
        with get_ticket_lock(ticket):
            for ackid, step in items:
                locked_log("step", bus=bus, ticket=ticket, step=step)
                step_ok = True
                if step in SERIAL_STEPS:
                    with fleet_serial_lock:
                        try:
                            with releases.ReleaseTrainLock(env=env):
                                step_env = dict(env)
                                step_env[releases.TRAIN_HELD_ENV] = "1"
                                step_proc = _run_sh_locked(tower_script, ["step", ticket, step], step_env, log_lock)
                                if step_proc.returncode != 0:
                                    step_ok = False
                                    locked_log("step_failed", bus=bus, ticket=ticket, step=step, rc=step_proc.returncode)
                        except (releases.ReleaseTrainTimeout, ValueError) as exc:
                            step_ok = False
                            locked_log("step_failed", bus=bus, ticket=ticket, step=step, reason=str(exc))
                else:
                    step_proc = _run_sh_locked(tower_script, ["step", ticket, step], env, log_lock)
                    if step_proc.returncode != 0:
                        step_ok = False
                        locked_log("step_failed", bus=bus, ticket=ticket, step=step, rc=step_proc.returncode)
                if not step_ok:
                    t_failed += 1
                t_processed += 1
                ack_rc = ack_msg(ackid)
                if ack_rc != 0:
                    return t_processed, t_failed, ackid, ack_rc
        return t_processed, t_failed, None, 0

    processed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while stop_event is None or not stop_event.is_set():
            batch: list[tuple[str, str, str]] = []
            seen_ackids: set[str] = set()
            while len(batch) < workers and (stop_event is None or not stop_event.is_set()):
                pulled = _run_sh_locked(pull_script, [], env, log_lock)
                if pulled.returncode != 0:
                    locked_log("error", bus=bus, stage="pull", rc=pulled.returncode)
                    return processed, failed, True
                msg = (pulled.stdout or "").rstrip("\r\n")
                if not msg:
                    break
                ackid, ticket, step = _parse_message(msg)
                if ackid in seen_ackids:
                    break
                seen_ackids.add(ackid)
                if not ticket or not step:
                    locked_log("drop", bus=bus, ackid=ackid, reason="message missing ticket or step")
                    processed += 1
                    ack_rc = ack_msg(ackid)
                    if ack_rc != 0:
                        locked_log("error", bus=bus, stage="ack", ackid=ackid, rc=ack_rc)
                        return processed, failed, True
                    continue
                batch.append((ackid, ticket, step))
            if not batch:
                break

            by_ticket: dict[str, list[tuple[str, str]]] = {}
            for ackid, ticket, step in batch:
                by_ticket.setdefault(ticket, []).append((ackid, step))

            futures = [pool.submit(run_ticket_items, ticket, items) for ticket, items in by_ticket.items()]
            for fut in futures:
                t_proc, t_fail, err_ack, err_rc = fut.result()
                processed += t_proc
                failed += t_fail
                if err_ack is not None:
                    locked_log("error", bus=bus, stage="ack", ackid=err_ack, rc=err_rc)
                    return processed, failed, True

    locked_log("cycle", bus=bus, processed=processed, failed=failed)
    return processed, failed, False


def drain_once(
    bus: str,
    env: Mapping[str, str],
    stop_event: threading.Event | None = None,
    workers: int = DEFAULT_WORKERS,
) -> tuple[int, int, bool]:
    """Drain queued events once matching adapters/inproc/run.sh.

    Returns (processed, failed, bus_error).
    """
    if workers < 1 or workers > MAX_RUNNER_WORKERS:
        raise ValueError(f"workers must be between 1 and {MAX_RUNNER_WORKERS}, got {workers}")
    if workers > 1:
        return _drain_once_parallel(bus, env, workers, stop_event)

    pull_script = os.path.join("adapters", bus, "pull.sh")
    ack_script = os.path.join("adapters", bus, "ack.sh")
    tower_script = os.path.join("core", "tower.sh")

    processed = 0
    failed = 0
    while stop_event is None or not stop_event.is_set():
        pulled = _run_sh(pull_script, [], env)
        if pulled.returncode != 0:
            _log("error", bus=bus, stage="pull", rc=pulled.returncode)
            return processed, failed, True
        msg = (pulled.stdout or "").rstrip("\r\n")
        if not msg:
            break

        ackid, ticket, step = _parse_message(msg)

        if not ticket or not step:
            _log("drop", bus=bus, ackid=ackid, reason="message missing ticket or step")
        else:
            _log("step", bus=bus, ticket=ticket, step=step)
            step_proc = _run_sh(tower_script, ["step", ticket, step], env)
            if step_proc.returncode != 0:
                failed += 1
                _log("step_failed", bus=bus, ticket=ticket, step=step, rc=step_proc.returncode)

        processed += 1
        acked = _run_sh(ack_script, [ackid], env)
        if acked.returncode != 0:
            _log("error", bus=bus, stage="ack", ackid=ackid, rc=acked.returncode)
            return processed, failed, True

    _log("cycle", bus=bus, processed=processed, failed=failed)
    return processed, failed, False


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    cfg = dict(os.environ if env is None else env)
    parser = argparse.ArgumentParser(prog="core/runner.py")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Drain queued events once and exit")
    mode.add_argument("--poll-s", type=float, default=DEFAULT_POLL_S, help="Poll interval in seconds")
    parser.add_argument("--lockfile", default=DEFAULT_LOCKFILE, help="Lockfile preventing concurrent runners")
    parser.add_argument("--max-cycles", type=int, default=None, help="Optional maximum poll cycles")
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=f"Parallel fleet workers (1..{MAX_RUNNER_WORKERS}, default RUNNER_WORKERS or {DEFAULT_WORKERS})",
    )
    args = parser.parse_args(argv)

    bus = (cfg.get("EVENT_BUS") or "").strip()
    if not bus:
        _log("error", reason="EVENT_BUS not set; run via ./go or set EVENT_BUS")
        return 2
    if args.poll_s <= 0:
        _log("error", reason="--poll-s must be > 0")
        return 2
    try:
        workers = resolve_workers(args.workers, cfg)
    except ValueError as exc:
        _log("error", reason=str(exc))
        return 2

    stop_event = threading.Event()
    received_signal: list[int] = []

    def _handle_signal(signum: int, _frame: Any) -> None:
        # Never write to sys.stderr inside a signal handler (avoids BufferedWriter reentrancy).
        received_signal.append(signum)
        stop_event.set()

    prev_term = signal.getsignal(signal.SIGTERM)
    prev_int = signal.getsignal(signal.SIGINT)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

    try:
        try:
            with acquire_lock(args.lockfile):
                _log(
                    "start",
                    bus=bus,
                    mode="once" if args.once else "poll",
                    poll_s=args.poll_s,
                    lockfile=args.lockfile,
                    workers=workers,
                )
                if args.once:
                    _, failed, bus_error = drain_once(bus, cfg, stop_event, workers=workers)
                    if received_signal:
                        _log("signal", bus=bus, signum=received_signal[0])
                    return 1 if (bus_error or failed > 0) else 0

                cycles = 0
                had_bus_error = False
                while not stop_event.is_set():
                    _, _, bus_error = drain_once(bus, cfg, stop_event, workers=workers)
                    if bus_error:
                        had_bus_error = True
                        break
                    cycles += 1
                    if args.max_cycles is not None and cycles >= args.max_cycles:
                        break
                    stop_event.wait(args.poll_s)
                if received_signal:
                    _log("signal", bus=bus, signum=received_signal[0])
                _log("stop", bus=bus, cycles=cycles)
                return 1 if had_bus_error else 0
        except LockError as exc:
            _log("lock_error", bus=bus, reason=str(exc))
            return 1
    finally:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGTERM, prev_term)
            signal.signal(signal.SIGINT, prev_int)


if __name__ == "__main__":
    sys.exit(main())
