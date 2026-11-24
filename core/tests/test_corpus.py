"""Specification tests for core/corpus.py (ADR 0106).

Strictly vendor-free, app-free, and model-free: exercises synthetic temporary
git repository builders, init drafting, light-tier gates, callback hooks,
structured JSON reporting, and deterministic failure detection.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import corpus

ROOT = Path(__file__).resolve().parents[2]
STACKS_DIR = ROOT / "stacks"


class CorpusCatalogTest(unittest.TestCase):
    def test_catalog_covers_at_least_eight_distinct_shapes_and_three_negatives(self) -> None:
        shapes = corpus.default_shapes()
        self.assertGreaterEqual(len(shapes), 8)
        names = [s.name for s in shapes]
        self.assertEqual(len(names), len(set(names)), "shape names must be unique")

        positives = {s.name: s for s in shapes if s.kind == "positive"}
        negatives = {s.name: s for s in shapes if s.kind == "negative"}
        self.assertGreaterEqual(len(positives), 7)
        self.assertGreaterEqual(len(negatives), 3)

        # Required positive coverage per ADR 0106.
        for req in (
            "python-uv-http",
            "python-uv-lib",
            "python-requirements",
            "javascript-npm-lib",
            "typescript-npm-lib",
            "go-http",
            "python-plain-http",
        ):
            self.assertIn(req, positives)

        self.assertTrue(positives["python-uv-http"].expected_has_plane)
        self.assertEqual(positives["python-uv-http"].expected_port, 8000)
        self.assertFalse(positives["python-uv-lib"].expected_has_plane)
        self.assertEqual(positives["python-requirements"].expected_lockfiles, "requirements.txt")
        self.assertEqual(positives["javascript-npm-lib"].expected_stack, "javascript")
        self.assertEqual(positives["typescript-npm-lib"].expected_stack, "typescript")
        self.assertTrue(positives["go-http"].expected_has_plane)
        self.assertEqual(positives["go-http"].expected_port, 8080)
        self.assertTrue(positives["python-plain-http"].expected_has_plane)
        self.assertEqual(positives["python-plain-http"].expected_port, 5000)
        self.assertIn("unittest", positives["python-plain-http"].expected_test_cmd_substr or "")

        for req_neg in (
            "neg-unpinned-requirements",
            "neg-pyproject-no-uv-lock",
            "neg-unknown-language",
        ):
            self.assertIn(req_neg, negatives)
            self.assertTrue(negatives[req_neg].expected_error_tokens)

    def test_catalog_covers_extended_stacks_monorepo_and_stateful_plane(self) -> None:
        shapes = corpus.default_shapes()
        self.assertGreaterEqual(len(shapes), 22)
        by_name = {s.name: s for s in shapes}

        for req_pos in (
            "rust-crate-lib",
            "java-maven-lib",
            "java-gradle-lib",
            "javascript-pnpm-lib",
            "typescript-yarn-lib",
            "monorepo-npm-service",
            "stateful-python-service",
            "stateless-comment-only-service",
        ):
            self.assertIn(req_pos, by_name)
            self.assertEqual(by_name[req_pos].kind, "positive")

        self.assertEqual(by_name["rust-crate-lib"].expected_stack, "rust")
        self.assertEqual(by_name["rust-crate-lib"].expected_lockfiles, "Cargo.lock")
        self.assertEqual(by_name["java-maven-lib"].expected_stack, "java")
        self.assertEqual(by_name["java-maven-lib"].expected_lockfiles, "pom.xml")
        self.assertEqual(by_name["java-gradle-lib"].expected_stack, "java")
        self.assertEqual(by_name["java-gradle-lib"].expected_lockfiles, "gradle.lockfile")
        self.assertEqual(by_name["javascript-pnpm-lib"].expected_lockfiles, "pnpm-lock.yaml")
        self.assertEqual(by_name["typescript-yarn-lib"].expected_lockfiles, "yarn.lock")
        self.assertEqual(by_name["monorepo-npm-service"].init_service, "services/api")
        self.assertEqual(by_name["monorepo-npm-service"].expected_test_root, "services/api/")
        self.assertIn("crew-postgres", by_name["stateful-python-service"].expected_compose_substrings)
        self.assertIn("crew-postgres", by_name["stateless-comment-only-service"].forbidden_compose_substrings)

        for req_neg in (
            "neg-rust-no-lock",
            "neg-gradle-no-lock",
            "neg-bun-binary-lockb",
            "neg-monorepo-ambiguous",
        ):
            self.assertIn(req_neg, by_name)
            self.assertEqual(by_name[req_neg].kind, "negative")
            self.assertTrue(by_name[req_neg].expected_error_tokens)


class CorpusRunTest(unittest.TestCase):
    def test_run_with_contract_subdir_and_callback_writes_report(self) -> None:
        callback_calls: list[tuple[str, bool]] = []

        def on_draft(shape, repo_dir: Path, contract_dir: Path, draft_info: dict) -> str | None:
            callback_calls.append((shape.name, (contract_dir / "workload.env").is_file()))
            self.assertTrue((repo_dir / ".git").exists())
            self.assertEqual(draft_info["stack"], shape.expected_stack)
            return None

        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "out" / "report.json"
            passed, total, details = corpus.run(
                contract_rel=".contract",
                stacks_dir=STACKS_DIR,
                report_path=report_path,
                verify_light=False,
                on_draft=on_draft,
            )

            self.assertEqual(passed, total)
            self.assertGreaterEqual(total, 10)
            self.assertEqual(len(details), total)
            self.assertTrue(report_path.is_file())

            payload = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "lights-out.corpus/1")
            self.assertEqual(payload["mode"], "fast")
            self.assertEqual(payload["contract_rel"], ".contract")
            self.assertEqual(payload["passed"], total)
            self.assertEqual(payload["failed"], 0)
            self.assertEqual(payload["total"], total)
            self.assertIsInstance(payload["duration_ms"], int)
            self.assertEqual(len(payload["shapes"]), total)

            positives = [s for s in payload["shapes"] if s["kind"] == "positive"]
            self.assertEqual(len(callback_calls), len(positives))
            self.assertTrue(all(ok for _, ok in callback_calls))

            by_name = {item["name"]: item for item in payload["shapes"]}
            self.assertEqual(by_name["python-uv-http"]["actual_port"], 8000)
            self.assertEqual(by_name["go-http"]["actual_port"], 8080)
            self.assertEqual(by_name["python-plain-http"]["actual_port"], 5000)
            self.assertIn("unittest", by_name["python-plain-http"]["test_cmd"])
            self.assertEqual(by_name["python-requirements"]["lockfiles"], "requirements.txt")

            for item in payload["shapes"]:
                self.assertEqual(item["status"], "PASS", item)
                self.assertEqual(item["errors"], [], item)

    def test_run_with_root_contract_exercises_light_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            passed, total, details = corpus.run(
                contract_rel=".",
                stacks_dir=STACKS_DIR,
                report_path=report_path,
                verify_light=True,
            )
            self.assertEqual(passed, total, details)
            for item in details:
                if item["kind"] == "positive":
                    self.assertTrue(
                        str(item.get("light_verdict", "")).startswith("GO: gates-light"),
                        item,
                    )

    def test_cli_writes_report_and_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "cli-report.json"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "core.corpus",
                    "--contract-rel",
                    ".",
                    "--stacks",
                    str(STACKS_DIR),
                    "--report",
                    str(report_path),
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONSAFEPATH": "1", "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(report_path.is_file())
            data = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(data["passed"], data["total"])


class CorpusFailureDetectionTest(unittest.TestCase):
    def test_detects_mismatched_port_expectation(self) -> None:
        base = next(s for s in corpus.default_shapes() if s.name == "go-http")
        broken = corpus.Shape(
            name="broken-port-expectation",
            kind="positive",
            expected_stack=base.expected_stack,
            expected_has_plane=True,
            expected_port=9999,
            expected_probe=base.expected_probe,
            expected_lockfiles=base.expected_lockfiles,
            expected_test_cmd_substr=base.expected_test_cmd_substr,
            expected_toolchain=base.expected_toolchain,
            files=base.files,
            change_files=base.change_files,
        )
        passed, total, details = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS_DIR,
            report_path=None,
            verify_light=False,
            shapes=[broken],
        )
        self.assertEqual((passed, total), (0, 1))
        self.assertEqual(details[0]["status"], "FAIL")
        self.assertTrue(any("9999" in err or "port" in err.lower() for err in details[0]["errors"]))

    def test_detects_negative_shape_that_unexpectedly_succeeds(self) -> None:
        valid_req = next(s for s in corpus.default_shapes() if s.name == "python-requirements")
        broken_neg = corpus.Shape(
            name="broken-negative-succeeds",
            kind="negative",
            expected_error_tokens=("unpinned",),
            files=valid_req.files,
        )
        passed, total, details = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS_DIR,
            report_path=None,
            verify_light=False,
            shapes=[broken_neg],
        )
        self.assertEqual((passed, total), (0, 1))
        self.assertEqual(details[0]["status"], "FAIL")
        self.assertTrue(details[0]["errors"])

    def test_detects_callback_failure(self) -> None:
        lib = next(s for s in corpus.default_shapes() if s.name == "python-uv-lib")

        def failing_cb(_shape, _repo: Path, _contract: Path, _info: dict) -> str:
            return "planted contract violation"

        passed, total, details = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS_DIR,
            report_path=None,
            verify_light=False,
            on_draft=failing_cb,
            shapes=[lib],
        )
        self.assertEqual((passed, total), (0, 1))
        self.assertEqual(details[0]["status"], "FAIL")
        self.assertTrue(any("planted contract violation" in err for err in details[0]["errors"]))

    def test_detects_missing_or_forbidden_stateful_compose_hints(self) -> None:
        base = next(s for s in corpus.default_shapes() if s.name == "stateless-comment-only-service")
        wrong_expect = corpus.Shape(
            name="broken-expects-postgres",
            kind="positive",
            expected_stack=base.expected_stack,
            expected_has_plane=True,
            expected_port=base.expected_port,
            expected_compose_substrings=("crew-postgres",),
            files=base.files,
        )
        passed, total, details = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS_DIR,
            report_path=None,
            verify_light=False,
            shapes=[wrong_expect],
        )
        self.assertEqual((passed, total), (0, 1))
        self.assertIn("crew-postgres", "; ".join(details[0]["errors"]))

        stateful = next(s for s in corpus.default_shapes() if s.name == "stateful-python-service")
        wrong_forbid = corpus.Shape(
            name="broken-forbids-postgres",
            kind="positive",
            expected_stack=stateful.expected_stack,
            expected_has_plane=True,
            expected_port=stateful.expected_port,
            forbidden_compose_substrings=("crew-postgres",),
            files=stateful.files,
        )
        passed2, total2, details2 = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS_DIR,
            report_path=None,
            verify_light=False,
            shapes=[wrong_forbid],
        )
        self.assertEqual((passed2, total2), (0, 1))
        self.assertIn("crew-postgres", "; ".join(details2[0]["errors"]))


if __name__ == "__main__":
    unittest.main()
