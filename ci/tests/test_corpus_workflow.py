"""Contract and workflow integration tests for M70 corpus check (ADR 0106).

Written from ADR 0106 before implementation: covers ci/corpus.sh contract
compliance, ci/workloads.sh integration across positive drafted shapes,
planted-defect failure detection, Makefile/go wiring, and corpus.yml pins
and actionlint validation.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from ci.checks import corpus as ci_corpus
from core import corpus as core_corpus

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CHECKOUT_PIN = "3d3c42e5aac5ba805825da76410c181273ba90b1"
EXPECTED_UPLOAD_PIN = "cf430e030ddbb5b0abf93d22962f4752f3646cd9"


class CorpusCheckContractTest(unittest.TestCase):
    def test_ci_corpus_sh_honors_one_line_contract_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            env = {
                **os.environ,
                "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:test"),
                "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE", "localhost/lights-out-workbench:test"),
                "CORPUS_REPORT": str(report_path),
            }
            proc = subprocess.run(
                ["sh", "ci/corpus.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            lines = proc.stdout.splitlines()
            self.assertEqual(len(lines), 1, f"expected 1 stdout line, got: {proc.stdout!r}")
            self.assertNotIn("\x1b", proc.stdout)
            self.assertRegex(lines[0], r"^GO: corpus — \d+/\d+ repository shapes verified$")

            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "lights-out.corpus/1")
            self.assertEqual(report["contract_rel"], ".lights-out")
            self.assertEqual(report["passed"], report["total"])
            self.assertGreaterEqual(report["total"], 22)
            self.assertLess(report["duration_ms"], 10000)
            by_name = {item["name"]: item for item in report["shapes"]}
            for required in (
                "rust-crate-lib",
                "java-maven-lib",
                "java-gradle-lib",
                "javascript-pnpm-lib",
                "typescript-yarn-lib",
                "monorepo-npm-service",
                "stateful-python-service",
                "stateless-comment-only-service",
                "neg-rust-no-lock",
                "neg-gradle-no-lock",
                "neg-bun-binary-lockb",
                "neg-monorepo-ambiguous",
            ):
                self.assertIn(required, by_name)
                self.assertEqual(by_name[required]["status"], "PASS")

    def test_planted_broken_contract_fails_workloads_and_nogo(self) -> None:
        base = next(s for s in core_corpus.default_shapes() if s.name == "python-uv-lib")

        def plant_bad_contract(shape, repo_dir: Path, contract_dir: Path, _info: dict) -> str | None:
            env_path = contract_dir / "workload.env"
            env_path.write_text(env_path.read_text(encoding="utf-8") + "EXTRA_BAD_KEY=1\n", encoding="utf-8")
            return ci_corpus.verify_workloads(shape, repo_dir, contract_dir, _info)

        passed, total, details = core_corpus.run(
            contract_rel=".lights-out",
            stacks_dir=ROOT / "stacks",
            report_path=None,
            verify_light=False,
            on_draft=plant_bad_contract,
            shapes=[base],
        )
        self.assertEqual((passed, total), (0, 1))
        self.assertEqual(details[0]["status"], "FAIL")
        self.assertTrue(any("EXTRA_BAD_KEY" in e or "workloads" in e for e in details[0]["errors"]))

    def test_self_test_rejects_broken_case(self) -> None:
        self.assertIsNone(ci_corpus.self_test(ROOT))


class CorpusWiringTest(unittest.TestCase):
    def test_makefile_wires_corpus_target_and_ci_checks_order(self) -> None:
        text = (ROOT / "Makefile").read_text(encoding="utf-8")
        m = re.search(r"^CI_CHECKS\s*:=\s*(.+)$", text, re.MULTILINE)
        self.assertIsNotNone(m)
        checks = m.group(1).split()
        self.assertIn("corpus", checks)
        self.assertEqual(checks.index("corpus"), checks.index("contract") - 1)
        self.assertRegex(text, r"(?m)^corpus:\s*##\s+\S+")

    def test_go_lists_corpus_help_line(self) -> None:
        text = (ROOT / "go").read_text(encoding="utf-8")
        self.assertIn("./go corpus", text)


class CorpusWorkflowTest(unittest.TestCase):
    def test_corpus_yml_structure_pins_and_guards(self) -> None:
        wf_path = ROOT / ".github/workflows/corpus.yml"
        self.assertTrue(wf_path.is_file(), f"missing {wf_path}")
        doc = yaml.safe_load(wf_path.read_text(encoding="utf-8"))

        # PyYAML parses bare `on:` as boolean True if unquoted.
        triggers = doc.get("on") if "on" in doc else doc.get(True)
        self.assertIsInstance(triggers, dict)
        self.assertIn("schedule", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertTrue(any("cron" in item for item in triggers["schedule"]))

        perms = doc.get("permissions") or {}
        self.assertEqual(perms, {"contents": "read"})

        jobs = doc.get("jobs") or {}
        self.assertTrue(jobs)
        all_runs = []
        uses_refs = []
        for job_name, job in jobs.items():
            cond = str(job.get("if", ""))
            self.assertIn("github.server_url == 'https://github.com'", cond, job_name)
            for step in job.get("steps") or []:
                if "uses" in step:
                    uses_refs.append(str(step["uses"]))
                if "run" in step:
                    all_runs.append(str(step["run"]))

        self.assertTrue(any(f"actions/checkout@{EXPECTED_CHECKOUT_PIN}" in u for u in uses_refs), uses_refs)
        self.assertTrue(any(f"actions/upload-artifact@{EXPECTED_UPLOAD_PIN}" in u for u in uses_refs), uses_refs)

        run_blob = "\n".join(all_runs)
        self.assertIn("./go ci-corpus", run_blob)
        self.assertIn("CORPUS_FULL=1", run_blob)

        # Report artifact path uploaded.
        upload_steps = [
            step
            for job in jobs.values()
            for step in (job.get("steps") or [])
            if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        self.assertTrue(upload_steps)
        self.assertIn(".crew/corpus/report.json", json.dumps(upload_steps))

    def test_corpus_yml_passes_actionlint(self) -> None:
        wf_path = ROOT / ".github/workflows/corpus.yml"
        if not shutil.which("actionlint"):
            self.skipTest("actionlint not installed outside workbench")
        proc = subprocess.run(
            ["actionlint", "-no-color", str(wf_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
