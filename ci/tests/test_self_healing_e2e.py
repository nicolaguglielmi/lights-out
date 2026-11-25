"""End-to-end subprocess tests for the closed self-healing loop (docs/adr/0121).

Three scenarios, each through the real entry points in a temporary
repository: a rollback whose canary log carries credentials (redacted,
bounded, exemplars, still a valid post-mortem input); a rolled-back real git
merge whose post-mortem ticket gets a derived perimeter; and a policy-gated
mitigation that flips a real flags-file kill switch and runs a stub rollback.
"""

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
VERSION = "0.4.0-rc1"

TICKET = """# 0100 — Stop the retry loop

- Status: open
- Origin: postmortem x action 1

## Problem

The retry loop spins.

## Acceptance criteria

Given a failing upstream
When the loop retries
Then it stops after three attempts and the log shows the third attempt

## Notes

Written by the ops-triage role.
"""


def record(verdict="rolled-back"):
    return {
        "version": VERSION, "stable": "0.3.9", "verdict": verdict, "held_at": None,
        "image": "registry.local/service@sha256:1111", "sbom": None, "dast": None, "reason": None,
        "stages": [{
            "pct": 25, "verdict": "NO-GO: slo — error_rate 0.500 > 0.010",
            "numbers": ["slo: error_rate canary(0.4.0-rc1)=0.500 stable(0.3.9)=0.000"],
            "started_at": "2026-11-24T04:01:05Z", "judged_at": "2026-11-24T04:02:05Z",
            "slo": [{"query": "error_rate", "canary": 0.5, "stable": 0.0, "delta": 0.5, "max_delta": 0.01}],
        }],
    }


class SelfHealingE2E(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.env = dict(os.environ, PYTHONPATH=str(ROOT))
        for key in ("LOGS", "RELEASE_TICKET", "TICKET", "STATE_STORE", "AUTO_POSTMORTEM_ON_ROLLBACK",
                    "AUTO_MITIGATE_ON_TRIAGE", "AUTO_INCIDENT_ON_ROLLBACK", "AUTO_PROMOTE_POSTMORTEM_TICKETS"):
            self.env.pop(key, None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str, mode: int | None = None) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        if mode:
            path.chmod(mode)
        return path

    def run_py(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], cwd=self.root, env=dict(self.env, **env),
                              capture_output=True, text=True, check=False, timeout=60)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=t",
                               "-c", "commit.gpgsign=false", *args],
                              cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    # --- 1. capture ---

    def test_rollback_with_a_credential_laden_canary_log(self) -> None:
        rec = self.write(".crew/release.json", json.dumps(record()))
        lines = [f"[canary] request {n} Authorization: Bearer tok{n}.abc" for n in range(300)]
        lines.append("SERVICE_TOKEN=do-not-leak")
        self.write(".crew/canary.log", "\n".join(lines) + "\n")
        proc = self.run_py(str(ROOT / "core" / "ops_loop.py"), "on-rollback", "--record", str(rec))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.root / proc.stdout.strip()
        text = path.read_text(encoding="utf-8")
        doc = json.loads(text)
        self.assertEqual(len(doc["logs"]), 200)
        self.assertEqual(doc["logs"][-1], "SERVICE_TOKEN=[REDACTED]")
        for leak in ("do-not-leak", "tok299.abc", "tok150.abc"):
            self.assertNotIn(leak, text)
        self.assertEqual(doc["exemplars"]["failing_stage_pct"], 25)
        self.assertEqual(doc["exemplars"]["breach"]["query"], "error_rate")
        valid = self.run_py(str(ROOT / "core" / "postmortem.py"), "validate", str(path))
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

    # --- 2. perimeters through a real merge ---

    def test_post_mortem_ticket_gets_a_perimeter_from_the_rolled_back_merge(self) -> None:
        self.git("init", "-q", "-b", "main")
        self.write("service/api.py", "v = 1\n")
        self.write("service/tests/test_api.py", "pass\n")
        self.write("service/side.py", "s = 1\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("service/api.py", "v = 2\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "change")
        self.git("checkout", "-q", "main")
        self.write("service/side.py", "s = 2\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "main moves")
        self.git("merge", "-q", "--no-ff", "-m", "merge", "feature")
        merge = self.git("rev-parse", "HEAD")
        self.write(".gitignore", ".crew/\n")

        self.write(".crew/release.json", json.dumps(record()))
        self.write("adapters/stub-state/get.sh", (
            '#!/bin/sh\n[ "$1 $2" = "tickets 0007" ] || exit 1\n'
            f'echo \'{{"release":{{"version":"{VERSION}"}},"pr":{{"number":3}},"merge":{{"sha":"{merge}"}}}}\'\n'))
        # core/postmortem.sh stand-in: files one local ticket and the post-mortem document
        self.write("core/postmortem.sh", (
            '#!/bin/sh\nset -eu\nname=$(basename "$INCIDENT" .json)\n'
            'mkdir -p ".crew/postmortem/$name" tickets\n'
            f"cat > tickets/0100.md <<'EOF'\n{TICKET}EOF\n"
            'printf \'{"actions":[{"n":1,"title":"t","ticket":"0100"}]}\' > ".crew/postmortem/$name/postmortem.json"\n'
            'echo "GO: 1 action item(s): tickets 0100"\n'))
        proc = self.run_py(
            str(ROOT / "core" / "ops_loop.py"), "on-rollback",
            RELEASE_TICKET="0007", STATE_STORE="stub-state", AUTO_POSTMORTEM_ON_ROLLBACK="on",
            AUTO_PROMOTE_POSTMORTEM_TICKETS="on")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = (self.root / "tickets" / "0100.md").read_text(encoding="utf-8")
        self.assertIn("## Perimeter\n\n- `service/api.py`\n- `service/tests/test_api.py`\n\n## Notes", text)
        self.assertNotIn("service/side.py", text, "the first-parent diff does not include main's own change")
        sys.path.insert(0, str(ROOT))
        try:
            from core import spec
        finally:
            sys.path.pop(0)
        self.assertIsNone(spec.check(text))

    # --- 3. mitigation through real and stub ports ---

    def test_mitigation_flips_the_real_kill_switch_and_rolls_back_through_a_stub_port(self) -> None:
        if shutil.which("jq") is None:
            self.fail("the flags-file adapter needs jq")
        (self.root / "adapters").mkdir()
        os.symlink(ROOT / "adapters" / "flags-file", self.root / "adapters" / "flags-file")
        self.write("adapters/stub-delivery/rollback.sh", '#!/bin/sh\necho rolled >> "$MARK"\n')
        flags = self.write("flags.json", json.dumps({"flags": {"feature-x": {
            "state": "ENABLED", "variants": {"on": True, "off": False}, "defaultVariant": "on"}}}))
        rec = self.write(".crew/release.json", json.dumps(record(verdict="held")))
        incident = self.write("incident.json", json.dumps({
            "alert": {"headline": "error budget burn", "version": VERSION}, "title": "t", "hypotheses": [],
            "proposal": {"action": "rollback", "version": VERSION, "reason": "burn"}, "run": {}}))
        mark = self.root / "mark.log"
        env = dict(DELIVERY="stub-delivery", FLAGS="flags-file", FLAGS_FILE=str(flags), MARK=str(mark),
                   WORKLOAD_KILL_SWITCH="feature-x", MITIGATE_ACTIONS="kill-switch,rollback")

        off = self.run_py(str(ROOT / "core" / "triage.py"), "mitigate", str(incident), str(rec), **env)
        self.assertEqual(off.returncode, 0, off.stdout + off.stderr)
        self.assertIn("disabled", off.stdout)
        self.assertFalse(mark.exists())
        self.assertEqual(json.loads(flags.read_text())["flags"]["feature-x"]["defaultVariant"], "on")

        on = self.run_py(str(ROOT / "core" / "triage.py"), "mitigate", str(incident), str(rec),
                         AUTO_MITIGATE_ON_TRIAGE="on", **env)
        self.assertEqual(on.returncode, 0, on.stdout + on.stderr)
        self.assertIn("applied", on.stdout)
        self.assertEqual(json.loads(flags.read_text())["flags"]["feature-x"]["defaultVariant"], "off")
        self.assertEqual(mark.read_text().splitlines(), ["rolled"])
        self.assertEqual(json.loads(rec.read_text())["verdict"], "rolled-back")
        audit = [json.loads(line) for line in (self.root / ".crew" / "mitigations.jsonl").read_text().splitlines()]
        self.assertEqual([(e["action"], e["verdict"], e["rc"]) for e in audit],
                         [("kill-switch", "applied", 0), ("rollback", "applied", 0)])

        again = self.run_py(str(ROOT / "core" / "triage.py"), "mitigate", str(incident), str(rec),
                            AUTO_MITIGATE_ON_TRIAGE="on", **env)
        self.assertEqual(again.returncode, 0)
        self.assertIn("already-rolled-back", again.stdout)
        self.assertEqual(mark.read_text().splitlines(), ["rolled"], "a re-delivered alert runs nothing twice")


if __name__ == "__main__":
    unittest.main()
