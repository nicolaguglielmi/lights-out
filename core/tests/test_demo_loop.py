"""Unit and integration tests for core/demo_loop.py and ADR 0132 (M38 / K4).

Strictly stdlib, vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import adr_gov, demo_loop, provenance

ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = ROOT / "docs" / "adr" / "0132-closed-self-healing-demo-loop.md"


class DemoLoopContractTests(unittest.TestCase):
    def test_adr_0132_exists_and_passes_governance(self) -> None:
        self.assertTrue(ADR_PATH.is_file(), f"missing {ADR_PATH}")
        text = ADR_PATH.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        self.assertIn("- Status: accepted", text)
        self.assertIn("DEMO_LOOP_BUDGET_SECONDS", text)
        self.assertIn("300", text)

    def test_shell_wrapper_stays_under_twenty_code_lines(self) -> None:
        sh_path = ROOT / "core" / "demo_loop.sh"
        self.assertTrue(sh_path.is_file())
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 20)

    def test_constants_and_input_validation(self) -> None:
        self.assertEqual(demo_loop.DEMO_LOOP_BUDGET_SECONDS, 300)
        self.assertEqual(demo_loop.INJECT_STAGES, ("triage", "spec", "gates", "canary"))
        with self.assertRaises(ValueError):
            demo_loop.run_demo_loop(inject_break="nonexistent")
        with self.assertRaises(ValueError):
            demo_loop.run_demo_loop(budget_s=0)

    def test_metrics_from_probes_summarizes_traffic_errors_and_latency(self) -> None:
        got = demo_loop._metrics_from_probes(
            "v0.1.0",
            [("/health", 200, 0.010), ("/quote", 200, 0.020)],
            "v0.2.0-rc1",
            [("/checkout", 500, 0.030), ("/health", 200, 0.010)],
        )
        self.assertEqual(got["v0.1.0"]["traffic"], 2.0)
        self.assertEqual(got["v0.1.0"]["error_rate"], 0.0)
        self.assertEqual(got["v0.2.0-rc1"]["traffic"], 2.0)
        self.assertEqual(got["v0.2.0-rc1"]["error_rate"], 0.5)

    def test_main_rejects_invalid_budget_with_one_nogo_line(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = demo_loop.main(["run", "--budget", "0"])
        self.assertEqual(rc, 1)
        self.assertTrue(out.getvalue().strip().startswith("NO-GO: demo-loop — "))

    def test_budget_exceeded_returns_nogo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "out"
            with mock.patch.object(demo_loop, "_phase_initial_held_canary", return_value={"verdict": "GO"}), \
                 mock.patch.object(demo_loop, "_phase_alert_triage_rollback", return_value={"verdict": "GO"}), \
                 mock.patch.object(demo_loop, "_phase_postmortem_and_ticket", return_value={"verdict": "GO"}), \
                 mock.patch.object(demo_loop, "_phase_fix_gates_merge_mainline", return_value={"verdict": "GO"}), \
                 mock.patch.object(demo_loop, "_phase_canary_promote_provenance_freeze", return_value={"verdict": "GO"}), \
                 mock.patch.object(demo_loop.time, "monotonic", side_effect=[100.0, 450.0, 450.0]):
                report = demo_loop.run_demo_loop(out_dir=out_dir, budget_s=300)
            self.assertEqual(report["verdict"], "NO-GO")
            self.assertIn("exceeded budget 300s", report["reason"])
            self.assertTrue(report["line"].startswith("NO-GO: demo-loop — "))
            self.assertTrue((out_dir / "summary.json").is_file())


class DemoLoopFullIntegrationTests(unittest.TestCase):
    def test_full_closed_loop_produces_go_provenance_and_frozen_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "demo-out"
            with contextlib.redirect_stderr(io.StringIO()):
                report = demo_loop.run_demo_loop(out_dir=out_dir, budget_s=300)

            self.assertEqual(report["verdict"], "GO", report.get("reason"))
            self.assertRegex(
                report["line"],
                r"^GO: demo-loop — closed loop \(alert -> rollback -> postmortem -> ticket -> gates -> canary -> 100%\) in \d+s \(budget 300s\)$",
            )
            self.assertLessEqual(report["elapsed_s"], 300)

            phases = report["phases"]
            self.assertEqual(phases["initial_canary"]["held_at"], 25)
            self.assertEqual(phases["triage_rollback"]["mitigation_status"], "applied")
            self.assertEqual(phases["triage_rollback"]["delivery_weight_after"], 0)
            self.assertEqual(phases["triage_rollback"]["release_verdict_after"], "rolled-back")
            self.assertEqual(phases["postmortem"]["readiness"], "spec-ready")
            self.assertEqual(phases["postmortem"]["spec_line"], "GO: spec")
            self.assertEqual(phases["fix_gates"]["gates_count"], 8)
            self.assertEqual(phases["fix_gates"]["mainline_state"], "main-green")
            self.assertEqual(phases["canary_promote"]["delivery_weight"], 100)
            self.assertEqual(phases["canary_promote"]["provenance_missing"], [])

            for rel in (
                "alerts/alert.json",
                "incident.json",
                "postmortem/postmortem.md",
                "postmortem/postmortem.json",
                "tickets/0001.md",
                "tickets/0002.md",
                "mitigations.jsonl",
                "release.json",
                "releases.jsonl",
                "v0.2.1.intoto.json",
                "logbook.json",
                "console-frozen.html",
                "summary.json",
                "summary.md",
            ):
                self.assertTrue((out_dir / rel).is_file(), f"missing artifact {rel}")

            inc_text = (out_dir / "incident.json").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", inc_text)
            self.assertNotIn("demo-canary-tok-999", inc_text)
            self.assertNotIn("demo-bearer-val-999", inc_text)
            inc_doc = json.loads(inc_text)
            self.assertEqual(inc_doc["chain"]["ticket"], "0001")
            self.assertEqual(inc_doc["exemplars"]["failing_stage_pct"], 25)

            stmt_path = out_dir / "v0.2.1.intoto.json"
            stmt = provenance.verify_statement(str(stmt_path))
            self.assertEqual(stmt["predicate"]["missing"], [])
            self.assertEqual(stmt["predicate"]["human_gate"]["answer"], "GO")
            self.assertEqual(stmt["predicate"]["gates"]["verdict"], "GO")
            self.assertTrue(any(a["path"] == "docs/adr/0001-service-contract.md" for a in stmt["predicate"]["adrs"]))

            book = json.loads((out_dir / "logbook.json").read_text(encoding="utf-8"))
            self.assertEqual(book["schema"], "lights-out/logbook/1.8")
            states = {t["id"]: t["state"] for t in book["tickets"]}
            self.assertEqual(states, {"0001": "rolled-back", "0002": "held"})

            frozen = (out_dir / "console-frozen.html").read_text(encoding="utf-8")
            self.assertIn("frozen-logbook", frozen)
            self.assertIn("Content-Security-Policy", frozen)
            self.assertIn("demo-loop v0.2.1", frozen)


if __name__ == "__main__":
    unittest.main()
