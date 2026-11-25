"""End-to-end CLI subprocess tests for core/demo_loop.sh and core/demo_loop.py (ADR 0132)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DemoLoopE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_sh(self, *args: str, out_dir: Path) -> subprocess.CompletedProcess[str]:
        env = dict(self.env, DEMO_LOOP_OUT=str(out_dir))
        return subprocess.run(
            ["sh", "core/demo_loop.sh", *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )

    def test_shell_entrypoint_go_and_signed_provenance_and_json_mode(self) -> None:
        out_sh = self.tmp / "out-sh"
        proc = self._run_sh(out_dir=out_sh)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        stdout_lines = proc.stdout.strip().splitlines()
        self.assertEqual(len(stdout_lines), 1, proc.stdout)
        self.assertRegex(
            stdout_lines[0],
            r"^GO: demo-loop — closed loop \(alert -> rollback -> postmortem -> ticket -> gates -> canary -> 100%\) in \d+s \(budget 300s\)$",
        )
        self.assertIn("[demo-loop] phase 1:", proc.stderr)
        self.assertIn("[demo-loop] phase 5:", proc.stderr)

        att = out_sh / "v0.2.1.intoto.json"
        self.assertTrue(att.is_file())
        if shutil.which("cosign"):
            bundle = out_sh / "v0.2.1.intoto.json.bundle"
            pub = out_sh / "cosign.pub"
            self.assertTrue(bundle.is_file())
            self.assertTrue(pub.is_file())
            ver = subprocess.run(
                [sys.executable, "core/provenance.py", "verify", "--statement", str(att), "--bundle", str(bundle), "--key", str(pub)],
                cwd=ROOT,
                env=self.env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ver.returncode, 0, ver.stdout + ver.stderr)

        out_json = self.tmp / "out-json"
        jproc = subprocess.run(
            [sys.executable, "core/demo_loop.py", "--json", "--out", str(out_json)],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        self.assertEqual(jproc.returncode, 0, jproc.stdout + jproc.stderr)
        parsed = json.loads(jproc.stdout)
        self.assertEqual(parsed["schema"], "lights-out.demo-loop/1")
        self.assertEqual(parsed["verdict"], "GO")
        self.assertEqual(parsed["phases"]["canary_promote"]["delivery_weight"], 100)
        self.assertTrue((out_json / "console-frozen.html").is_file())

    def test_inject_break_stages_fail_closed_and_clean_stale_go_artifacts(self) -> None:
        # Seed out_dir with stale GO artifacts to prove a subsequent NO-GO cleans them
        out_dir = self.tmp / "shared-out"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "console-frozen.html").write_text("stale", encoding="utf-8")
        (out_dir / "v0.2.1.intoto.json").write_text("stale", encoding="utf-8")

        expected_fragments = {
            "triage": "triage rejected alert",
            "spec": "spec gate rejected ticket",
            "gates": "gate test failed",
            "canary": "canary v0.2.1 failed",
        }
        for stage, fragment in expected_fragments.items():
            with self.subTest(stage=stage):
                proc = self._run_sh("--inject-break", stage, out_dir=out_dir)
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                lines = proc.stdout.strip().splitlines()
                self.assertEqual(len(lines), 1, proc.stdout)
                self.assertTrue(lines[0].startswith("NO-GO: demo-loop — "), lines[0])
                self.assertIn(fragment, lines[0])

                summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
                self.assertEqual(summary["verdict"], "NO-GO")
                self.assertEqual(summary["inject_break"], stage)
                self.assertFalse((out_dir / "console-frozen.html").exists())
                self.assertFalse((out_dir / "v0.2.1.intoto.json").exists())


if __name__ == "__main__":
    unittest.main()
