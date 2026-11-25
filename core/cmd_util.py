# SPDX-License-Identifier: Apache-2.0
"""Shared stdlib command, env-file, JSON, and gate-output helpers for core/."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess  # nosec B404 - shared core runner; callers pass explicit argv lists
import sys
import threading
from pathlib import Path
from typing import Any, NoReturn, Sequence


def _has_fileno(stream: Any) -> bool:
    """Return True if *stream* is backed by a real OS file descriptor."""
    try:
        stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    return True


def go(name: str, detail: str = "") -> int:
    """Print one GO contract line to stdout and return exit code 0."""
    if detail:
        clean = detail.replace("\n", " ")
        sys.stdout.write(f"GO: {name} — {clean}\n")
    else:
        sys.stdout.write(f"GO: {name}\n")
    sys.stdout.flush()
    return 0


def nogo(name: str, reason: str, *details: str) -> NoReturn:
    """Print one NO-GO contract line, log details, and exit with status 1."""
    clean_reason = reason.replace("\n", " ")
    sys.stdout.write(f"NO-GO: {name} — {clean_reason}\n")
    sys.stdout.flush()
    for line in details:
        sys.stderr.write(f"{line}\n")
    sys.stderr.flush()
    nogo_details = os.environ.get("NOGO_DETAILS", "")
    if nogo_details and details:
        try:
            Path(nogo_details).write_text("\n".join(details) + "\n", encoding="utf-8")
        except OSError:
            pass
    raise SystemExit(1)


def fail_msg(prefix: str, message: str) -> NoReturn:
    """Write ``<prefix>: <message>`` to stderr and exit with status 1."""
    sys.stderr.write(f"{prefix}: {message}\n")
    sys.stderr.flush()
    raise SystemExit(1)


def need_env(
    env: dict[str, str],
    key: str,
    message: str | None = None,
    *,
    gate: str | None = None,
    step: str | None = None,
) -> str:
    """Return non-empty *key* from *env*, or exit cleanly on the configured channel."""
    val = env.get(key, "")
    if val:
        return val
    msg = message or f"{key} not set"
    if gate is not None:
        nogo(gate, msg)
    if step is not None:
        fail_msg(step, msg)
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()
    raise SystemExit(1)


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse simple KEY=VALUE lines (with optional quotes or ``export ``) without a shell."""
    parsed: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            if val[0] == "'":
                val = val[1:-1]
            else:
                try:
                    parts = shlex.split(val)
                    val = parts[0] if len(parts) == 1 else val[1:-1]
                except ValueError:
                    val = val[1:-1]
        parsed[key] = val
    return parsed


def load_role_env(
    role: str,
    env: dict[str, str],
    *,
    gate: str | None = None,
    step: str | None = None,
) -> dict[str, str]:
    """Load ``roles/<role>/role.env`` and return parsed budget/harness keys."""
    role_env_path = Path("roles") / role / "role.env"
    if not role_env_path.is_file():
        msg = f"{role_env_path} not found"
        if gate is not None:
            nogo(gate, msg)
        if step is not None:
            fail_msg(step, msg)
        raise SystemExit(1)
    vars_map = parse_env_file(role_env_path)
    merged = dict(env)
    merged.update(vars_map)
    return merged


def run_cmd(
    argv: Sequence[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    capture_stdout: bool = False,
    capture_stderr: bool = False,
    stdout_to_stderr: bool = False,
    suppress_stdout: bool = False,
    suppress_stderr: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Execute *argv* directly with safe stdout/stderr forwarding even under StringIO."""
    stderr_fd = _has_fileno(sys.stderr)
    stdout_fd = _has_fileno(sys.stdout)

    if capture_stdout:
        out_dest: Any = subprocess.PIPE
    elif stdout_to_stderr:
        out_dest = sys.stderr if stderr_fd else subprocess.PIPE
    elif suppress_stdout:
        out_dest = subprocess.DEVNULL
    else:
        out_dest = sys.stdout if stdout_fd else subprocess.PIPE

    if capture_stderr:
        err_dest: Any = subprocess.PIPE
    elif suppress_stderr:
        err_dest = subprocess.DEVNULL
    else:
        err_dest = sys.stderr if stderr_fd else subprocess.PIPE

    proc = subprocess.run(  # nosec B603 - explicit argv list, no shell
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        input=input_text,
        text=True,
        stdout=out_dest,
        stderr=err_dest,
        check=False,
    )

    if proc.stdout and not capture_stdout:
        target = sys.stderr if stdout_to_stderr else sys.stdout
        target.write(proc.stdout)
        target.flush()
    if proc.stderr and not capture_stderr:
        sys.stderr.write(proc.stderr)
        sys.stderr.flush()
    return proc


def run_tee(
    argv: Sequence[str],
    log_path: str | Path,
    *,
    env: dict[str, str] | None = None,
    cwd: str | Path | None = None,
) -> tuple[int, str]:
    """Run *argv*, capturing stdout and live-teeing stderr to *log_path* and ``sys.stderr``."""
    log = Path(log_path)
    log.parent.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(  # nosec B603 - explicit argv list, no shell
        list(argv),
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as proc:
        proc_stderr = proc.stderr
        proc_stdout = proc.stdout
        if proc_stderr is None or proc_stdout is None:
            raise RuntimeError("subprocess pipes not attached")

        def _pump_stderr() -> None:
            with log.open("w", encoding="utf-8") as fh:
                while True:
                    line = proc_stderr.readline()
                    if not line:
                        break
                    fh.write(line)
                    fh.flush()
                    sys.stderr.write(line)
                    sys.stderr.flush()

        pump = threading.Thread(target=_pump_stderr, daemon=True)
        pump.start()
        stdout_text = proc_stdout.read()
        pump.join()
        rc = proc.wait()
    return rc, stdout_text


def run_sh(script: str | Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``sh <script> <args...>`` with ``run_cmd`` keyword options."""
    return run_cmd(["sh", str(script), *args], **kwargs)


def run_py(script: str | Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``python3 <script> <args...>`` with ``run_cmd`` keyword options."""
    return run_cmd([sys.executable, str(script), *args], **kwargs)


def git_cmd(repo: str | Path, *args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Run ``git -C <repo> <args...>`` with ``run_cmd`` keyword options."""
    return run_cmd(["git", "-C", str(repo), *args], **kwargs)


def sha256_file(path: str | Path) -> str:
    """Return lowercase hex SHA-256 digest of *path*."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: str | Path, content: str) -> None:
    """Atomically write UTF-8 text to *path* via a sibling temporary file."""
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp.{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(dest)


def read_json(path: str | Path) -> Any:
    """Parse JSON from *path*."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def strip_prefix(text: str, prefix: str) -> str:
    """Strip *prefix* from *text* if present (POSIX ``${var#prefix}``)."""
    return text[len(prefix):] if text.startswith(prefix) else text
