"""Golden logbook compatibility (1.2-1.8) and live HTTP/wrapper integration tests for M74 TUI (ADR 0114)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from core import tui
from core.tests.test_tui import StrictFakeWindow

ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = ROOT / "fixtures" / "logbook"
GOLDEN_VERSIONS = ("1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "1.8")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class GoldenLogbookCompatibilityTest(unittest.TestCase):
    def test_all_committed_golden_schemas_render_all_five_views_at_80x24(self) -> None:
        for ver in GOLDEN_VERSIONS:
            path = FIXTURES_DIR / f"golden-{ver}.json"
            self.assertTrue(path.is_file(), f"missing {path}")
            book = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(book.get("schema"), f"lights-out/logbook/{ver}")

            for view_name in tui.VIEWS:
                state = tui.make_state(book, view=view_name, source_label=str(path))
                frame = tui.render_frame(state, width=80, height=24)
                self.assertEqual(len(frame.rows), 24, f"schema {ver} view {view_name} height != 24")
                win = StrictFakeWindow(height=24, width=80)
                tui.draw_curses_frame(win, frame, use_color=False)

                text = frame.plain_text()
                self.assertIsNone(ANSI_ESCAPE_RE.search(text))
                self.assertIn(f"1.{ver.split('.')[1]}", text)

            # Navigate through every ticket in Tickets & Gates views on each golden version
            for nav_view in ("tickets", "gates"):
                nav_state = tui.make_state(book, view=nav_view)
                for _ in range(len(book.get("tickets") or []) + 2):
                    nav_state = tui.reduce_key(nav_state, "j")
                    frame = tui.render_frame(nav_state, width=80, height=24)
                    StrictFakeWindow(24, 80)
                    win = StrictFakeWindow(height=24, width=80)
                    tui.draw_curses_frame(win, frame, use_color=False)

    def test_golden_1_2_vs_1_8_specific_schema_evolution_details(self) -> None:
        b12 = json.loads((FIXTURES_DIR / "golden-1.2.json").read_text(encoding="utf-8"))
        b18 = json.loads((FIXTURES_DIR / "golden-1.8.json").read_text(encoding="utf-8"))

        # 1.2 lacks dora, spend, roles, intakes, flow — TUI must degrade honestly without crashing
        s12 = tui.build_summary_json(tui.make_state(b12))
        self.assertEqual(s12["logbook_schema"], "lights-out/logbook/1.2")
        self.assertEqual(len(s12["tickets"]), 8)
        w12 = tui.render_frame(tui.make_state(b12, view="workers"), width=80, height=24).plain_text()
        self.assertIn("1.2", w12)

        # 1.8 includes dora, spend burn, roles, intakes, flow, live run on 0003
        s18 = tui.build_summary_json(tui.make_state(b18))
        self.assertEqual(s18["logbook_schema"], "lights-out/logbook/1.8")
        self.assertEqual(len(s18["tickets"]), 9)
        waiting_ids = {item["id"] for item in s18["waiting_for_you"]}
        self.assertIn("0009", waiting_ids)  # open question
        self.assertIn("0007", waiting_ids)  # needs-human
        self.assertIn("0013", waiting_ids)  # intake-questions

        w18 = tui.render_frame(tui.make_state(b18, view="workers"), width=90, height=28).plain_text()
        self.assertIn("0003", w18)
        self.assertIn("2,210,000", w18)


class ShellWrapperAndLiveHttpTest(unittest.TestCase):
    def test_tui_sh_respects_shell_boundary_and_runs_once_and_json(self) -> None:
        sh_path = ROOT / "core" / "tui.sh"
        self.assertTrue(sh_path.is_file(), "core/tui.sh missing")
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 30, f"core/tui.sh has {len(code_lines)} code lines (> 30)")

        golden = FIXTURES_DIR / "golden-1.8.json"
        env = {**os.environ, "NO_COLOR": "1", "PYTHONPATH": str(ROOT)}
        res_once = subprocess.run(
            ["sh", "core/tui.sh", "--logbook", str(golden), "--once", "--width", "80", "--height", "24"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
        self.assertEqual(res_once.returncode, 0, res_once.stderr)
        self.assertIsNone(ANSI_ESCAPE_RE.search(res_once.stdout))
        self.assertIn("LIGHTS-OUT COCKPIT", res_once.stdout)

        # Also verify LIGHTS_OUT_CLI_ARGS unit-separator forwarding
        forwarded_env = {
            **env,
            "LIGHTS_OUT_CLI_ARGS": "\x1f".join(["--logbook", str(golden), "--json"]),
        }
        res_json = subprocess.run(
            ["sh", "core/tui.sh"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env=forwarded_env,
            timeout=15,
            check=False,
        )
        self.assertEqual(res_json.returncode, 0, res_json.stderr)
        summary = json.loads(res_json.stdout)
        self.assertEqual(summary["schema"], "lights-out/tui-summary/1.0")
        self.assertEqual(summary["logbook_schema"], "lights-out/logbook/1.8")

    def test_live_http_etag_polling_against_real_local_server(self) -> None:
        raw_bytes = (FIXTURES_DIR / "golden-1.8.json").read_bytes()
        tag = '"' + hashlib.sha256(raw_bytes).hexdigest() + '"'
        requests_log: list[tuple[str, str | None, int]] = []

        class LogbookHandler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                return None

            def do_GET(self) -> None:
                inm = self.headers.get("If-None-Match")
                if self.path == "/v1/logbook.json":
                    if inm == tag:
                        requests_log.append((self.path, inm, 304))
                        self.send_response(304)
                        self.send_header("ETag", tag)
                        self.end_headers()
                        return
                    requests_log.append((self.path, inm, 200))
                    self.send_response(200)
                    self.send_header("ETag", tag)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw_bytes)))
                    self.end_headers()
                    self.wfile.write(raw_bytes)
                    return
                self.send_response(404)
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), LogbookHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{port}/v1/logbook.json"
            http_src = tui.HttpSource(url, timeout_s=5.0)
            p1 = http_src.poll()
            self.assertTrue(p1.changed)
            self.assertEqual(p1.etag, tag)
            self.assertIsNone(p1.error)

            p2 = http_src.poll()
            self.assertFalse(p2.changed)
            self.assertEqual(p2.etag, tag)
            self.assertIsNone(p2.error)

            self.assertEqual(requests_log, [("/v1/logbook.json", None, 200), ("/v1/logbook.json", tag, 304)])

            # CLI --url --once against the live server
            res = subprocess.run(
                ["python3", "core/tui.py", "--url", f"http://127.0.0.1:{port}", "--once", "--view", "canary"],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env={**os.environ, "NO_COLOR": "1", "PYTHONPATH": str(ROOT)},
                timeout=15,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("0.5.0-d", res.stdout)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
