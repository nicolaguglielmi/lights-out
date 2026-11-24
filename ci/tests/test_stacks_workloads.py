"""Integration and contract tests for M71 stacks and example workloads (ADR 0102)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import init

ROOT = Path(__file__).resolve().parents[2]


class StackRunnersAndWorkloadsTest(unittest.TestCase):
    def test_go_and_ts_stack_runners_exist(self) -> None:
        for rel in (
            "stacks/go/runners/sast.sh",
            "stacks/go/runners/sast.go",
            "stacks/go/runners/mutation.sh",
            "stacks/go/runners/mutate.go",
            "stacks/typescript/runners/sast.sh",
            "stacks/typescript/runners/mutation.sh",
            "stacks/typescript/runners/score.mjs",
            "stacks/typescript/runners/eslint.security.mjs",
            "stacks/typescript/runners/package.json",
            "stacks/typescript/runners/package-lock.json",
        ):
            self.assertTrue((ROOT / rel).is_file(), f"missing {rel}")

    def test_init_write_draft_copies_runners_and_uses_contract_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            go_repo = base / "go-repo"
            go_repo.mkdir()
            (go_repo / "go.mod").write_text("module example.com/g\n\ngo 1.24\n")
            (go_repo / "go.sum").write_text("")
            (go_repo / "pkg" / "calc").mkdir(parents=True)
            (go_repo / "pkg" / "calc" / "calc.go").write_text("package calc\n")
            go_out = base / "go-out"
            self.assertEqual(init.write_draft(str(go_repo), str(go_out), str(ROOT / "stacks"), "2026-11-24", "abc"), "go")
            wenv = (go_out / "workload.env").read_text()
            self.assertIn("SAST_CMD=sh .lights-out/runners/sast.sh pkg\n", wenv)
            self.assertIn('MUTATION_CMD=sh .lights-out/runners/mutation.sh pkg "go test ./..."\n', wenv)
            self.assertTrue((go_out / "runners" / "mutate.go").is_file())

            ts_repo = base / "ts-repo"
            ts_repo.mkdir()
            (ts_repo / "package.json").write_text('{"name":"t"}')
            (ts_repo / "tsconfig.json").write_text("{}")
            (ts_repo / "package-lock.json").write_text('{"name":"t","lockfileVersion":3,"packages":{}}')
            (ts_repo / "src").mkdir()
            (ts_repo / "src" / "a.ts").write_text("export const x: number = 1;\n")
            ts_out = base / "ts-out"
            self.assertEqual(
                init.write_draft(str(ts_repo), str(ts_out), str(ROOT / "stacks"), "2026-11-24", "abc"),
                "typescript",
            )
            ts_wenv = (ts_out / "workload.env").read_text()
            self.assertIn("SAST_CMD=sh .lights-out/runners/sast.sh src\n", ts_wenv)
            self.assertIn('MUTATION_CMD=npm ci && sh .lights-out/runners/mutation.sh src "node --test"\n', ts_wenv)
            self.assertTrue((ts_out / "runners" / "eslint.security.mjs").is_file())

    def test_minimal_ts_workload_contract_and_node24_tests(self) -> None:
        wl = ROOT / "workloads/minimal-ts"
        for rel in (
            "package.json",
            "package-lock.json",
            "tsconfig.json",
            "src/calc.ts",
            "test/calc.test.ts",
            ".lights-out/workload.env",
            ".lights-out/worker.md",
            ".lights-out/policy.env",
        ):
            self.assertTrue((wl / rel).is_file(), f"missing workloads/minimal-ts/{rel}")

        # Runners match stacks/typescript/runners byte for byte.
        diff = subprocess.run(
            ["diff", "-r", str(ROOT / "stacks/typescript/runners"), str(wl / ".lights-out/runners")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)

        # ci/workloads.sh accepts workloads/minimal-ts.
        w = subprocess.run(
            ["sh", "ci/workloads.sh", "workloads/minimal-ts"],
            cwd=ROOT,
            env={**os.environ, "WORKLOAD_CONTRACT": str(wl / ".lights-out"), "WORKBENCH_IMAGE": "x", "HARNESS_BASE_IMAGE": "x"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        self.assertEqual(w.stdout.strip(), "GO: workloads")

        # Node 24 executes TypeScript tests natively inside workbench.
        node_test = subprocess.run(
            ["node", "--test"],
            cwd=wl,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(node_test.returncode, 0, node_test.stdout + node_test.stderr)

    def test_minimal_go_workload_contract_and_runners(self) -> None:
        wl = ROOT / "workloads/minimal-go"
        for rel in (
            "Containerfile",
            "go.mod",
            "go.sum",
            ".lights-out/workload.env",
            ".lights-out/worker.md",
            ".lights-out/policy.env",
        ):
            self.assertTrue((wl / rel).is_file(), f"missing workloads/minimal-go/{rel}")
        self.assertTrue(any((wl / "pkg/calc").glob("*.go")))
        self.assertTrue(any((wl / "cmd/server").glob("*.go")))

        diff = subprocess.run(
            ["diff", "-r", str(ROOT / "stacks/go/runners"), str(wl / ".lights-out/runners")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)

        w = subprocess.run(
            ["sh", "ci/workloads.sh", "workloads/minimal-go"],
            cwd=ROOT,
            env={**os.environ, "WORKLOAD_CONTRACT": str(wl / ".lights-out"), "WORKBENCH_IMAGE": "x", "HARNESS_BASE_IMAGE": "x"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        self.assertEqual(w.stdout.strip(), "GO: workloads")

    def test_typescript_score_mjs_handles_ts_report(self) -> None:
        score_mjs = ROOT / "stacks/typescript/runners/score.mjs"
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "mutation.json"
            report.write_text(
                json.dumps(
                    {
                        "files": {
                            "src/calc.ts": {
                                "mutants": [
                                    {"status": "Killed", "mutatorName": "ArithmeticOperator", "location": {"start": {"line": 2}}},
                                    {"status": "Survived", "mutatorName": "EqualityOperator", "location": {"start": {"line": 5}}},
                                    {"status": "CompileError", "mutatorName": "BlockStatement", "location": {"start": {"line": 9}}},
                                ]
                            }
                        }
                    }
                )
            )
            p = subprocess.run(["node", str(score_mjs), str(report)], capture_output=True, text=True, check=False)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout.strip()), {"total": 2, "killed": 1, "survived": 1, "score": 0.5})
            self.assertIn("SURVIVOR src/calc.ts:5 EqualityOperator", p.stderr)


if __name__ == "__main__":
    unittest.main()
