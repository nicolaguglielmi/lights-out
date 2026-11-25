"""End-to-end tests for M89 (ADR 0129): polyglot stacks, 36-shape corpus, outside EventBus, and CNCF hygiene."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import adr_gov, corpus as core_corpus

ROOT = Path(__file__).resolve().parents[2]


class PolyglotHygieneAndRunnersContractTest(unittest.TestCase):
    def test_adr_0129_validates_with_provenance_table(self) -> None:
        adr_path = ROOT / "docs/adr/0129-polyglot-enterprise-stacks-and-hygiene.md"
        self.assertTrue(adr_path.is_file(), f"missing {adr_path}")
        text = adr_path.read_text(encoding="utf-8")
        self.assertIn("## Provenance", text)
        self.assertEqual(adr_gov.validate_adr(text), [])

    def test_hygiene_docs_exist_and_pass_docs_drift_and_roles(self) -> None:
        for doc_name in ("CODE_OF_CONDUCT.md", "GOVERNANCE.md"):
            doc_path = ROOT / doc_name
            self.assertTrue(doc_path.is_file(), f"missing {doc_name}")
            content = doc_path.read_text(encoding="utf-8")
            self.assertTrue(content.startswith("# "))

        roles_proc = subprocess.run(
            ["sh", "ci/roles.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(roles_proc.returncode, 0, roles_proc.stdout + roles_proc.stderr)
        self.assertEqual(roles_proc.stdout.strip(), "GO: roles")

        drift_proc = subprocess.run(
            ["make", "--no-print-directory", "ci-docs-drift"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(drift_proc.returncode, 0, drift_proc.stdout + drift_proc.stderr)
        self.assertEqual(drift_proc.stdout.strip(), "GO: docs-drift")

    def test_executable_bits_and_shellcheck_on_all_m89_scripts(self) -> None:
        scripts: list[Path] = []
        for stack_name in ("dotnet", "kotlin", "swift", "php", "ruby"):
            scripts.append(ROOT / "stacks" / stack_name / "runners" / "sast.sh")
            scripts.append(ROOT / "stacks" / stack_name / "runners" / "mutation.sh")
        for verb in ("env.sh", "ready.sh", "publish.sh", "pull.sh", "ack.sh"):
            scripts.append(ROOT / "fixtures/outside-adapters/file-eventbus" / verb)

        for script in scripts:
            self.assertTrue(script.is_file(), f"missing {script}")
            self.assertTrue(os.access(script, os.X_OK), f"not executable (+x): {script}")

        if shutil.which("shellcheck"):
            sc = subprocess.run(
                ["shellcheck", "-s", "sh", *map(str, scripts)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)


class PolyglotThirtySixCorpusE2ETest(unittest.TestCase):
    def test_ci_corpus_verifies_all_36_shapes_cold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "corpus-36.json"
            env = {
                **os.environ,
                "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE") or "localhost/lights-out-workbench:test",
                "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE") or "localhost/lights-out-workbench:test",
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
            self.assertEqual(proc.stdout.strip(), "GO: corpus — 36/36 repository shapes verified")

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["total"], 36)
            self.assertEqual(report["passed"], 36)
            self.assertEqual(report["failed"], 0)
            by_name = {item["name"]: item for item in report["shapes"]}
            for name in (
                "dotnet-sdk-http",
                "dotnet-cpm-sln-lib",
                "kotlin-gradle-lib",
                "kotlin-maven-http",
                "swift-spm-lib",
                "php-composer-lib",
                "ruby-bundler-rspec",
                "monorepo-dotnet-ruby-service",
                "neg-dotnet-no-lock",
                "neg-swift-no-resolved",
                "neg-php-no-lock",
                "neg-ruby-no-lock",
            ):
                self.assertIn(name, by_name)
                self.assertEqual(by_name[name]["status"], "PASS", by_name[name])

    def test_polyglot_positive_shapes_pass_full_osv_and_sast_checks(self) -> None:
        if not shutil.which("osv-scanner"):
            self.fail("osv-scanner not installed outside workbench")
        polyglot_names = {
            "dotnet-sdk-http",
            "dotnet-cpm-sln-lib",
            "kotlin-gradle-lib",
            "kotlin-maven-http",
            "swift-spm-lib",
            "php-composer-lib",
            "ruby-bundler-rspec",
            "monorepo-dotnet-ruby-service",
        }
        selected = [s for s in core_corpus.default_shapes() if s.name in polyglot_names]
        self.assertEqual(len(selected), 8)
        passed, total, details = core_corpus.run(
            contract_rel=".lights-out",
            stacks_dir=ROOT / "stacks",
            report_path=None,
            verify_light=True,
            full=True,
            shapes=selected,
        )
        self.assertEqual((passed, total), (8, 8), details)
        for item in details:
            self.assertEqual(item["callbacks"].get("osv"), "PASS", item)
            self.assertEqual(item["callbacks"].get("sast"), "PASS", item)


class OutsideFileEventBusE2ETest(unittest.TestCase):
    def test_file_eventbus_preserves_bytes_idempotent_ack_and_passes_6_case_conformance(self) -> None:
        fixture = ROOT / "fixtures/outside-adapters/file-eventbus"
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            bus_dir = tpath / "bus-state"
            env = {
                **os.environ,
                "FILE_EVENTBUS_DIR": str(bus_dir),
                "CONFORMANCE_ACK_DEADLINE": "1",
            }
            ready = subprocess.run(["sh", str(fixture / "ready.sh")], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertEqual(ready.stdout, "")

            empty_pull = subprocess.run(["sh", str(fixture / "pull.sh")], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(empty_pull.returncode, 0)
            self.assertEqual(empty_pull.stdout, "")

            # Multi-line or empty payloads must fail closed.
            for bad in ("", "line1\nline2"):
                bad_pub = subprocess.run(
                    ["sh", str(fixture / "publish.sh"), bad],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(bad_pub.returncode, 0)
                self.assertEqual(bad_pub.stdout, "")

            tricky = '{"msg":"hello world","q":"a\\"b\\\\c","utf8":"über café 🚀"}'
            pub = subprocess.run(["sh", str(fixture / "publish.sh"), tricky], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(pub.returncode, 0, pub.stderr)

            pulled = subprocess.run(["sh", str(fixture / "pull.sh")], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(pulled.returncode, 0, pulled.stderr)
            ack_id, sep, data = pulled.stdout.rstrip("\n").partition(" ")
            self.assertEqual(sep, " ")
            self.assertTrue(ack_id)
            self.assertEqual(data, tricky)

            # Leased message is hidden from concurrent pull.
            second_pull = subprocess.run(["sh", str(fixture / "pull.sh")], env=env, capture_output=True, text=True, check=False)
            self.assertEqual(second_pull.returncode, 0)
            self.assertEqual(second_pull.stdout, "")

            # Ack is idempotent when called multiple times.
            for _ in range(2):
                ack = subprocess.run(["sh", str(fixture / "ack.sh"), ack_id], env=env, capture_output=True, text=True, check=False)
                self.assertEqual(ack.returncode, 0, ack.stderr)
                self.assertEqual(ack.stdout, "")

    def test_ci_outside_sh_passes_with_eventbus_included(self) -> None:
        if not shutil.which("jq"):
            self.fail("jq not installed outside workbench")
        proc = subprocess.run(
            ["sh", "ci/outside.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        line = proc.stdout.strip()
        self.assertTrue(line.startswith("GO: outside — "), line)
        self.assertIn("EventBus", line)


if __name__ == "__main__":
    unittest.main()
