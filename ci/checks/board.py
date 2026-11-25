# SPDX-License-Identifier: Apache-2.0
"""ci-board: the board tells the truth about the tower and about the plane.

Cold check (no plane, no socket), from docs/adr/0035 §2-§4 and §6, plus
docs/adr/0053 §4:
  a. board/index.html's embedded tower-machine JSON equals core/tower.py's
     PRECONDITION and NEXT tables (docs/adr/0052 §3).
  b. With nothing to read, every probe in /state.json answers ok:false with a
     non-empty error and no value; kill_switch stays None/None.
  c. board/serve.py calls no adapter verb except get, evaluate, and status.
  d. GET / serves the page, GET /state.json serves a JSON object.
  e. With BOARD_STATIC=1 (§6), GET / serves the page and GET /state.json
     answers 404.
  f. board/index.html's GATES array equals Makefile's GATES in order.

Run by ci/board.sh through `verdict` (docs/adr/0094).
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ALLOWED_VERBS = {"get", "evaluate", "status"}
_TOWER_SCRIPT_RE = re.compile(r'<script type="application/json" id="tower-machine">(.*?)</script>', re.DOTALL)
_MAKEFILE_GATES_RE = re.compile(r"^GATES := (.+)$", re.MULTILINE)
_HTML_GATES_RE = re.compile(r"^const GATES = \[(.*)\];$", re.MULTILINE)
_ADAPTER_VERB_RE = re.compile(r'adapter\("[A-Z_]*",\s*"([a-z-]*)"\)')


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_http(url: str, *, timeout_s: float = 10.0) -> tuple[int, str]:
    deadline = time.monotonic() + timeout_s
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as err:
            return err.code, err.read().decode("utf-8", errors="replace")
        except OSError as err:
            last_err = err
            time.sleep(0.2)
    raise TimeoutError(f"GET {url} did not answer within {timeout_s}s: {last_err}")


def _http_get(url: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=5.0) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", errors="replace")


def check_tower_table(repo_root: Path) -> str | None:
    """Verify board/index.html embeds core/tower.py's PRECONDITION and NEXT tables."""
    page = (repo_root / "board" / "index.html").read_text(encoding="utf-8")
    match = _TOWER_SCRIPT_RE.search(page)
    if not match:
        return "the board's tower table disagrees with core/tower.py\nboard/index.html embeds no tower-machine table"
    board_data = json.loads(match.group(1))
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json, sys; sys.path.insert(0, 'core'); import tower; "
            "print(json.dumps({'precondition': tower.PRECONDITION, 'next': tower.NEXT}))",
        ],
        cwd=repo_root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"the board's tower table disagrees with core/tower.py\n{proc.stderr.strip()}"
    tower_data = json.loads(proc.stdout)
    for key in ("precondition", "next"):
        if board_data.get(key) != tower_data[key]:
            return f"the board's tower table disagrees with core/tower.py\n{key}: board {board_data.get(key)} vs core/tower.py {tower_data[key]}"
    return None


def check_gates_order(repo_root: Path) -> str | None:
    """Verify board/index.html's GATES match Makefile's GATES in exact order."""
    mk = (repo_root / "Makefile").read_text(encoding="utf-8")
    mk_match = _MAKEFILE_GATES_RE.search(mk)
    if not mk_match:
        return "the Makefile has no GATES := line"
    want = mk_match.group(1).split()
    page = (repo_root / "board" / "index.html").read_text(encoding="utf-8")
    html_match = _HTML_GATES_RE.search(page)
    have = [tok.strip().strip('"\'') for tok in html_match.group(1).split(",") if tok.strip()] if html_match else []
    if have != want:
        return f"the board's GATES ({' '.join(have)}) are not the Makefile's ({' '.join(want)})"
    return None


def check_read_only_verbs(repo_root: Path) -> str | None:
    """Verify board/serve.py invokes only read-only adapter verbs."""
    src = (repo_root / "board" / "serve.py").read_text(encoding="utf-8")
    verbs = sorted(set(_ADAPTER_VERB_RE.findall(src)))
    if not verbs:
        return "board/serve.py calls no port verb at all"
    for verb in verbs:
        if verb not in ALLOWED_VERBS:
            return f"board/serve.py calls the {verb} verb; the board is read-only"
    return None


def check_cold_snapshot(repo_root: Path, tmp: Path) -> str | None:
    """Verify snapshot() reports ok:false and no default values when nothing is readable."""
    env = {
        **os.environ,
        "PLANE_HOST": "127.0.0.1",
        "DOCKER_HOST": f"unix://{tmp / 'no.sock'}",
        "STATE_FILE": str(tmp),
        "RELEASE_RECORD": str(tmp / "release.json"),
        "PROMOTE_OUT": str(tmp / "promote"),
        "WORKLOAD_KILL_SWITCH": "flag",
    }
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('serve', 'board/serve.py')\n"
        "serve = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(serve)\n"
        "snap = serve.snapshot()\n"
        "for key in ('tickets', 'delivery', 'freeze', 'record', 'brief'):\n"
        "    p = snap[key]\n"
        "    if p.get('ok') is not False or not p.get('error') or 'value' in p:\n"
        "        sys.exit(f'{key} with nothing to read answered {p}')\n"
        "ks = snap['kill_switch']\n"
        "if ks['canary'] is not None or ks['stable'] is not None:\n"
        "    sys.exit(f'kill switch evaluated with no version from the plane: {ks}')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        return f"a probe with nothing to read did not say so\n{detail}"
    return None


def check_http_modes(repo_root: Path, tmp: Path) -> str | None:
    """Verify GET /, GET /state.json, and BOARD_STATIC=1 behavior."""
    # (d) live server mode with unreachable plane
    port = _free_port()
    live_env = {
        **os.environ,
        "PLANE_HOST": "127.0.0.1",
        "DOCKER_HOST": f"unix://{tmp / 'no.sock'}",
        "STATE_FILE": str(tmp),
        "BOARD_LISTEN": str(port),
        "BOARD_POLL_S": "60",
    }
    proc = subprocess.Popen(
        [sys.executable, "board/serve.py"],
        cwd=repo_root,
        env=live_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            status, page = _wait_http(f"http://127.0.0.1:{port}/")
        except TimeoutError as err:
            proc.terminate()
            _, stderr_text = proc.communicate(timeout=5)
            return f"board/serve.py did not answer on :{port}\n{stderr_text or err}"
        if status != 200 or 'id="tower-machine"' not in page:
            return "GET / did not serve board/index.html"
        st_status, st_body = _http_get(f"http://127.0.0.1:{port}/state.json")
        if st_status != 200:
            return f"GET /state.json did not serve a JSON object (HTTP {st_status})"
        try:
            parsed = json.loads(st_body)
        except ValueError:
            return "GET /state.json did not serve a JSON object"
        if not isinstance(parsed, dict):
            return "GET /state.json did not serve a JSON object"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    # (e) static server mode
    static_port = _free_port()
    static_env = {**os.environ, "BOARD_STATIC": "1", "PORT": str(static_port)}
    sproc = subprocess.Popen(
        [sys.executable, "board/serve.py"],
        cwd=repo_root,
        env=static_env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            status, page = _wait_http(f"http://127.0.0.1:{static_port}/")
        except TimeoutError as err:
            sproc.terminate()
            _, stderr_text = sproc.communicate(timeout=5)
            return f"board/serve.py with BOARD_STATIC=1 did not answer on :{static_port}\n{stderr_text or err}"
        if status != 200 or 'id="tower-machine"' not in page:
            return "BOARD_STATIC=1: GET / did not serve board/index.html"
        code, _ = _http_get(f"http://127.0.0.1:{static_port}/state.json")
        if code != 404:
            return f"BOARD_STATIC=1: GET /state.json answered {code}, want 404"
    finally:
        if sproc.poll() is None:
            sproc.terminate()
            try:
                sproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sproc.kill()
                sproc.wait(timeout=5)
    return None


def judge(root: Path | None = None) -> tuple[str, bool]:
    """Run all board truth checks under ROOT; return (verdict_text, ok)."""
    repo_root = Path(root) if root is not None else Path.cwd()
    if not (repo_root / "board" / "index.html").is_file():
        return "board/index.html not found", False
    if not (repo_root / "board" / "serve.py").is_file():
        return "board/serve.py not found", False

    for check in (check_tower_table, check_gates_order, check_read_only_verbs):
        err = check(repo_root)
        if err is not None:
            return err, False

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for fn in (check_cold_snapshot, check_http_modes):
            err = fn(repo_root, tmp_path)
            if err is not None:
                return err, False
    return "", True


def main() -> int:
    text, ok = judge()
    if text:
        print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
