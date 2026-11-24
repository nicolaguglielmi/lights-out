"""End-to-end HTTP tests for M85 Control-Plane Workstation routes in console/serve.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _write_exec(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _http_req(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raw = err.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        return err.code, payload


class _RunningConsole:
    def __init__(self, cwd: Path, env: dict[str, str]) -> None:
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        run_env = dict(env, CONSOLE_LISTEN=str(self.port), CONSOLE_PORT=str(self.port))
        self.proc = subprocess.Popen(
            ["python3", "-B", "console/serve.py"],
            cwd=cwd,
            env=run_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def wait_ready(self, headers: dict[str, str] | None = None, expect_live: bool = False) -> None:
        deadline = time.monotonic() + 15.0
        last_err = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = self.proc.stderr.read() if self.proc.stderr else ""
                raise RuntimeError(f"console/serve.py exited early ({self.proc.returncode}): {err}")
            try:
                code, body = _http_req(f"{self.base}/v1/console.json", headers=headers)
                if code == 200 and (not expect_live or body.get("read_at")):
                    return
            except Exception as exc:
                last_err = str(exc)
            time.sleep(0.1)
        raise RuntimeError(f"console/serve.py did not become ready: {last_err}")

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        if self.proc.stderr:
            self.proc.stderr.close()


class ConsoleWorkstationE2ETest(unittest.TestCase):
    def _build_live_root(self, *, delay_assign_s: float = 0.0, charts_configured: bool = True) -> Path:
        td = tempfile.mkdtemp(prefix="ws-e2e-")
        self.addCleanup(shutil.rmtree, td, True)
        root = Path(td)
        (root / "console").mkdir()
        shutil.copy2(ROOT / "console" / "serve.py", root / "console" / "serve.py")
        shutil.copy2(ROOT / "console" / "index.html", root / "console" / "index.html")
        (root / "core").mkdir()
        for f in (ROOT / "core").glob("*.py"):
            (root / "core" / f.name).symlink_to(f)
        shutil.copy2(ROOT / "core" / "console_ops.sh", root / "core" / "console_ops.sh")
        (root / "core" / "console_ops.sh").chmod(0o755)
        (root / "gates").mkdir()
        _write_exec(root / "gates" / "spec.sh", "#!/bin/sh\necho 'GO: spec'\n")

        book_src = ROOT / "fixtures" / "logbook" / "golden-1.8.json"
        (root / "fixtures" / "logbook").mkdir(parents=True)
        shutil.copy2(book_src, root / "fixtures" / "logbook" / "golden-1.8.json")

        _write_exec(
            root / "core" / "logbook.sh",
            f"#!/bin/sh\nset -eu\ncp {json.dumps(str(root / 'fixtures' / 'logbook' / 'golden-1.8.json'))} \"$LOGBOOK_FILE\"\necho 'GO: logbook'\n",
        )
        _write_exec(root / "core" / "promote.sh", "#!/bin/sh\necho 'GO: promote'\n")

        effects = root / "effects.log"
        sleep_line = f"sleep {delay_assign_s}; " if delay_assign_s > 0 else ""
        (root / "Makefile").write_text(
            "assign:\n"
            f"\t@{sleep_line}printf 'assign %s\\n' \"$(TICKET)\" >> effects.log\n"
            "\t@echo 'GO: assign'\n"
            "resume:\n"
            "\t@printf 'resume %s\\n' \"$(TICKET)\" >> effects.log\n"
            "\t@echo 'GO: resume'\n"
            "watch:\n"
            "\t@printf 'watch %s\\n' \"$(ONCE)\" >> effects.log\n"
            "\t@echo 'GO: watch'\n",
            encoding="utf-8",
        )
        _write_exec(
            root / "adapters" / "stub-flags" / "set.sh",
            "#!/bin/sh\nset -eu\nprintf 'flag %s %s\\n' \"$1\" \"$2\" >> effects.log\n",
        )
        _write_exec(
            root / "adapters" / "stub-delivery" / "rollback.sh",
            "#!/bin/sh\nset -eu\nprintf 'rollback\\n' >> effects.log\n",
        )
        if charts_configured:
            _write_exec(
                root / "adapters" / "stub-charts" / "query-range.sh",
                "#!/bin/sh\nset -eu\nprintf '{\"status\":\"ok\",\"series\":[{\"name\":\"qps\",\"points\":[[100,1.2],[115,2.4]]}]}\\n'\n",
            )
        return root

    def test_snapshot_and_public_refuse_all_writes_with_zero_subprocesses(self) -> None:
        root = self._build_live_root()
        ran = root / "ran"
        for name in ("logbook.sh", "promote.sh", "console_ops.sh"):
            _write_exec(root / "core" / name, f"#!/bin/sh\necho {name} >> {json.dumps(str(ran))}\nexit 1\n")
        (root / "console" / "snapshot").mkdir(parents=True)
        shutil.copy2(root / "fixtures" / "logbook" / "golden-1.8.json", root / "console" / "snapshot" / "logbook.json")

        for extra_env in (
            {"CONSOLE_SNAPSHOT": "fixtures/logbook/golden-1.8.json", "CONSOLE_AUTH": "loopback"},
            {"CONSOLE_SNAPSHOT": "console/snapshot/logbook.json", "CONSOLE_AUTH": "public"},
        ):
            srv = _RunningConsole(root, {"PATH": os.environ.get("PATH", ""), **extra_env})
            try:
                srv.wait_ready()
                c_code, c_body = _http_req(f"{srv.base}/v1/charts.json")
                self.assertEqual(c_code, 200)
                self.assertFalse(c_body["ok"])
                self.assertIn("snapshot", c_body["error"])
                hdrs = {"Content-Type": "application/json", "Origin": srv.base}
                for route, payload in (
                    ("/v1/assign", {"ticket": "0005"}),
                    ("/v1/resume", {"ticket": "0004"}),
                    ("/v1/watch-once", {}),
                    ("/v1/rollback", {"reason": "drill"}),
                    ("/v1/freeze", {"state": "on", "reason": "drill"}),
                ):
                    code, _ = _http_req(f"{srv.base}{route}", method="POST", body=json.dumps(payload).encode(), headers=hdrs)
                    self.assertEqual(code, 403, f"{extra_env} POST {route}")
                self.assertFalse(ran.exists())
            finally:
                srv.close()

    def test_live_loopback_and_proxy_authorization_and_press_contention(self) -> None:
        root = self._build_live_root(delay_assign_s=0.4, charts_configured=True)
        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "CONSOLE_POLL_S": "0.2",
            "FLAGS": "stub-flags",
            "DELIVERY": "stub-delivery",
            "CHARTS": "stub-charts",
            "WORKLOAD_KILL_SWITCH": "canary-kill",
            "CONSOLE_OPS_LOG": str(root / ".crew" / "console-ops.jsonl"),
        }

        # 1. Proxy mode auth & RBAC gates (clean pre-M87 hook behavior)
        proxy_srv = _RunningConsole(
            root,
            {**base_env, "CONSOLE_AUTH": "proxy", "CONSOLE_IDENTITY_HEADER": "X-User", "CONSOLE_LAUNCHERS": "alice"},
        )
        try:
            proxy_srv.wait_ready(headers={"X-User": "alice"}, expect_live=True)
            self.assertEqual(_http_req(f"{proxy_srv.base}/v1/charts.json")[0], 401)
            code, charts = _http_req(f"{proxy_srv.base}/v1/charts.json", headers={"X-User": "bob"})
            self.assertEqual(code, 200)
            self.assertTrue(charts["ok"])

            # Non-launcher bob gets 403 on privileged rollback & freeze, but may run assign/watch-once
            hdr_bob = {"Content-Type": "application/json", "Origin": proxy_srv.base, "X-User": "bob"}
            self.assertEqual(
                _http_req(f"{proxy_srv.base}/v1/rollback", method="POST", body=b'{"reason":"nope"}', headers=hdr_bob)[0],
                403,
            )
            self.assertEqual(
                _http_req(f"{proxy_srv.base}/v1/freeze", method="POST", body=b'{"state":"on","reason":"nope"}', headers=hdr_bob)[0],
                403,
            )
            # Alice runs rollback & freeze cleanly
            hdr_alice = {"Content-Type": "application/json", "Origin": proxy_srv.base, "X-User": "alice"}
            rc_code, rb = _http_req(
                f"{proxy_srv.base}/v1/rollback", method="POST", body=b'{"reason":"p95 breach"}', headers=hdr_alice
            )
            self.assertEqual(rc_code, 200)
            self.assertEqual(rb["exit"], 0)
            fr_code, fr = _http_req(
                f"{proxy_srv.base}/v1/freeze",
                method="POST",
                body=b'{"state":"off","reason":"all clear"}',
                headers=hdr_alice,
            )
            self.assertEqual(fr_code, 200)
            self.assertEqual(fr["state"], "off")
            self.assertEqual(fr["exit"], 0)
        finally:
            proxy_srv.close()

        # 2. Loopback mode contention (409) & state checks
        (root / "effects.log").write_text("", encoding="utf-8")
        loop_srv = _RunningConsole(root, base_env)
        try:
            loop_srv.wait_ready(expect_live=True)
            hdrs = {"Content-Type": "application/json", "Origin": loop_srv.base}

            # State checks
            self.assertEqual(
                _http_req(f"{loop_srv.base}/v1/assign", method="POST", body=b'{"ticket":"0002"}', headers=hdrs)[0],
                409,
            )
            self.assertEqual(
                _http_req(f"{loop_srv.base}/v1/resume", method="POST", body=b'{"ticket":"0002"}', headers=hdrs)[0],
                409,
            )
            self.assertEqual(
                _http_req(f"{loop_srv.base}/v1/assign", method="POST", body=b'{"ticket":"../bad"}', headers=hdrs)[0],
                400,
            )

            # Contention check while slow assign is in flight
            first: dict[str, int] = {}

            def run_first() -> None:
                c, _ = _http_req(f"{loop_srv.base}/v1/assign", method="POST", body=b'{"ticket":"0005"}', headers=hdrs)
                first["code"] = c

            th = threading.Thread(target=run_first)
            th.start()
            time.sleep(0.12)
            second_code, _ = _http_req(f"{loop_srv.base}/v1/watch-once", method="POST", body=b"{}", headers=hdrs)
            th.join(timeout=5)
            self.assertEqual(first.get("code"), 200)
            self.assertEqual(second_code, 409)

            self.assertEqual(
                _http_req(f"{loop_srv.base}/v1/resume", method="POST", body=b'{"ticket":"0004"}', headers=hdrs)[0],
                200,
            )
            self.assertEqual(
                _http_req(f"{loop_srv.base}/v1/watch-once", method="POST", body=b"{}", headers=hdrs)[0],
                200,
            )
            effects = (root / "effects.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(effects, ["assign 0005", "resume 0004", "watch 1"])
        finally:
            loop_srv.close()


if __name__ == "__main__":
    unittest.main()
