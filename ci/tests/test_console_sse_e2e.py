"""End-to-end integration tests for Console SSE `/v1/events` streaming (ADR 0124)."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN_18 = ROOT / "fixtures" / "logbook" / "golden-1.8.json"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_sse_block(raw: str) -> tuple[str | None, dict | None]:
    event_name = None
    data_lines = []
    for line in raw.splitlines():
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data_lines.append(line.partition(":")[2].strip())
    payload = json.loads("\n".join(data_lines)) if data_lines else None
    return event_name, payload


def _read_sse_message(resp: http.client.HTTPResponse) -> str:
    lines: list[str] = []
    while True:
        raw = resp.fp.readline()
        if not raw:
            break
        decoded = raw.decode("utf-8")
        lines.append(decoded)
        if decoded in ("\n", "\r\n") and lines:
            break
    return "".join(lines)


class ConsoleSseEndToEndTest(unittest.TestCase):
    def _start_server(self, cwd: Path, extra_env: dict[str, str]) -> tuple[subprocess.Popen[str], int]:
        port = _free_port()
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("CONSOLE_") and k != "SSE_HEARTBEAT_S"
        }
        env.update(
            {
                "CONSOLE_LISTEN": str(port),
                "CONSOLE_PORT": str(port),
                **extra_env,
            }
        )
        proc = subprocess.Popen(
            ["python3", str(cwd / "console" / "serve.py")],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                self.fail(f"console/serve.py exited early ({proc.returncode}): {err}")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    return proc, port
            except OSError:
                time.sleep(0.05)
        proc.kill()
        proc.wait(timeout=5)
        self.fail("console/serve.py did not bind within timeout")

    @staticmethod
    def _stop_server(proc: subprocess.Popen[str]) -> None:
        proc.kill()
        proc.wait(timeout=5)
        if proc.stderr:
            proc.stderr.close()

    def test_snapshot_mode_events_returns_single_frame_and_closes_cleanly(self) -> None:
        proc, port = self._start_server(
            ROOT,
            {"CONSOLE_SNAPSHOT": str(GOLDEN_18)},
        )
        try:
            t0 = time.monotonic()
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/events", timeout=3) as resp:
                body = resp.read().decode("utf-8")
                elapsed = time.monotonic() - t0
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream; charset=utf-8")
                self.assertEqual(resp.headers.get("Cache-Control"), "no-store")
                self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
                self.assertIn("default-src 'self'", resp.headers.get("Content-Security-Policy") or "")
                self.assertLess(elapsed, 2.0, "snapshot /v1/events must close immediately without blocking")

            event_name, payload = _parse_sse_block(body)
            want_etag = '"' + hashlib.sha256(GOLDEN_18.read_bytes()).hexdigest() + '"'
            self.assertEqual(event_name, "logbook")
            self.assertIsNotNone(payload)
            assert payload is not None
            self.assertEqual(payload["etag"], want_etag)
            self.assertEqual(payload["mode"], "snapshot")
            self.assertIsNone(payload["read_at"])

            # POST /v1/events must be rejected as 404 (not a write route)
            post_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/events",
                data=b"{}",
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": f"http://127.0.0.1:{port}",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(post_req, timeout=3)
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()
        finally:
            self._stop_server(proc)

    def test_proxy_auth_enforces_401_without_identity_header_and_200_with_header(self) -> None:
        proc, port = self._start_server(
            ROOT,
            {
                "CONSOLE_SNAPSHOT": str(GOLDEN_18),
                "CONSOLE_AUTH": "proxy",
                "CONSOLE_IDENTITY_HEADER": "X-Test-User",
                "CONSOLE_LAUNCHERS": "alice",
            },
        )
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/events", timeout=3)
            self.assertEqual(ctx.exception.code, 401)
            ctx.exception.close()

            authed = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/events",
                headers={"X-Test-User": "viewer@example.org"},
            )
            with urllib.request.urlopen(authed, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.headers.get("Content-Type"), "text/event-stream; charset=utf-8")
                event_name, payload = _parse_sse_block(resp.read().decode("utf-8"))
                self.assertEqual(event_name, "logbook")
                assert payload is not None
                self.assertEqual(payload["mode"], "snapshot")
        finally:
            self._stop_server(proc)

    def test_live_mode_streams_updates_keepalive_errors_and_handles_disconnect(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            shutil.copytree(ROOT / "console", tmp / "console")
            (tmp / "core").mkdir()
            current_src = tmp / "current-logbook.json"
            fail_flag = tmp / "fail-reader"
            delay_file = tmp / "delay-s"

            base_doc = json.loads(GOLDEN_18.read_text(encoding="utf-8"))
            current_src.write_text(json.dumps(base_doc), encoding="utf-8")
            delay_file.write_text("0.45\n", encoding="utf-8")

            reader_sh = tmp / "core" / "logbook.sh"
            reader_sh.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                "if [ -f delay-s ]; then sleep \"$(cat delay-s)\"; rm -f delay-s; fi\n"
                "if [ -f fail-reader ]; then echo 'NO-GO: logbook — simulated store error'; exit 1; fi\n"
                "cp current-logbook.json \"$LOGBOOK_FILE\"\n"
                "echo \"GO: logbook — $LOGBOOK_FILE\"\n",
                encoding="utf-8",
            )
            reader_sh.chmod(0o755)

            proc, port = self._start_server(
                tmp,
                {
                    "CONSOLE_POLL_S": "0.12",
                    "SSE_HEARTBEAT_S": "0.22",
                },
            )
            try:
                # 1. Before first read completes, ?once=1 emits event: pending cleanly without blocking
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/events?once=1", timeout=2) as resp:
                    self.assertEqual(resp.status, 200)
                    ev0, data0 = _parse_sse_block(resp.read().decode("utf-8"))
                    self.assertIn(ev0, ("pending", "logbook"))
                    assert data0 is not None
                    self.assertEqual(data0["mode"], "live")
                    if ev0 == "pending":
                        self.assertIsNone(data0["etag"])

                # Wait for initial live read to finish
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/console.json", timeout=2) as c_resp:
                        c_data = json.loads(c_resp.read().decode("utf-8"))
                        if c_data.get("read_at"):
                            break
                    time.sleep(0.05)
                else:
                    self.fail("live server did not finish initial read")

                # 2. Live ?once=1 returns event: logbook with live ETag and closes immediately
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/events?once=1", timeout=2) as once_resp:
                    ev_once, data_once = _parse_sse_block(once_resp.read().decode("utf-8"))
                    self.assertEqual(ev_once, "logbook")
                    assert data_once is not None
                    etag_1 = data_once["etag"]
                    self.assertTrue(etag_1.startswith('"') and etag_1.endswith('"'))
                    self.assertEqual(data_once["mode"], "live")
                    self.assertIsNotNone(data_once["read_at"])

                # 3. Persistent streaming connection receives initial frame, keepalive, updated ETag, and error frame
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=4)
                conn.request("GET", "/v1/events")
                stream_resp = conn.getresponse()
                self.assertEqual(stream_resp.status, 200)
                self.assertEqual(stream_resp.getheader("Content-Type"), "text/event-stream; charset=utf-8")

                first_block = _read_sse_message(stream_resp)
                ev1, payload1 = _parse_sse_block(first_block)
                self.assertEqual(ev1, "logbook")
                assert payload1 is not None
                self.assertEqual(payload1["etag"], etag_1)

                # Keepalive comment arrives within SSE_HEARTBEAT_S when logbook is unchanged
                hb_block = _read_sse_message(stream_resp)
                self.assertIn(": keepalive", hb_block)

                # Mutate logbook -> Condition wakes SSE stream immediately with new ETag
                updated_doc = json.loads(json.dumps(base_doc))
                updated_doc["tickets"][0]["title"] = "Updated via live SSE test"
                tmp_write = tmp / "next-logbook.json"
                tmp_write.write_text(json.dumps(updated_doc), encoding="utf-8")
                tmp_write.replace(current_src)

                pushed_etag = None
                for _ in range(8):
                    msg = _read_sse_message(stream_resp)
                    if "event: logbook" in msg:
                        _, pushed = _parse_sse_block(msg)
                        if pushed and pushed.get("etag") != etag_1:
                            pushed_etag = pushed["etag"]
                            break
                self.assertIsNotNone(pushed_etag, "SSE stream did not push updated logbook ETag")
                self.assertNotEqual(pushed_etag, etag_1)

                # Conditional GET /v1/logbook.json matches pushed_etag (200 on old tag, 304 on pushed tag)
                req_old = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/logbook.json",
                    headers={"If-None-Match": etag_1},
                )
                with urllib.request.urlopen(req_old, timeout=2) as r200:
                    self.assertEqual(r200.status, 200)
                    self.assertEqual(r200.headers.get("ETag"), pushed_etag)

                req_cur = urllib.request.Request(
                    f"http://127.0.0.1:{port}/v1/logbook.json",
                    headers={"If-None-Match": str(pushed_etag)},
                )
                with self.assertRaises(urllib.error.HTTPError) as r304:
                    urllib.request.urlopen(req_cur, timeout=2)
                self.assertEqual(r304.exception.code, 304)
                r304.exception.close()

                # Reader error updates read_error and notifies SSE stream while retaining last good ETag
                fail_flag.write_text("1\n", encoding="utf-8")
                saw_error = False
                for _ in range(8):
                    msg = _read_sse_message(stream_resp)
                    if "event: logbook" in msg:
                        _, err_payload = _parse_sse_block(msg)
                        if err_payload and err_payload.get("read_error"):
                            saw_error = True
                            self.assertEqual(err_payload["etag"], pushed_etag)
                            self.assertIn("simulated store error", err_payload["read_error"])
                            break
                self.assertTrue(saw_error, "SSE stream did not emit frame on read_error transition")

                # Abrupt client disconnect leaves server healthy with zero crashes
                conn.close()
                time.sleep(0.25)
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/console.json", timeout=2) as health:
                    self.assertEqual(health.status, 200)
            finally:
                self._stop_server(proc)


if __name__ == "__main__":
    unittest.main()
