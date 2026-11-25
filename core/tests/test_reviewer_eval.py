# SPDX-License-Identifier: Apache-2.0
"""Unit tests for core/reviewer_eval.py, core/reviewer_eval.sh, fixtures, and ADR 0137.

Strictly vendor-free, app-free, and model-free: uses isolated synthetic git
repos, synthetic diff cases, and pluggable stub reviewers with zero network
calls or real secrets.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import adr_gov, reviewer_eval

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_git_diff(base_files: dict[str, str], head_files: dict[str, str], msg: str = "change") -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        git_base = [
            "git",
            "-c", "user.name=Tester",
            "-c", "user.email=tester@lights-out.invalid",
            "-c", "commit.gpgsign=false",
            "-c", "init.defaultBranch=main",
            "-C", str(repo),
        ]
        subprocess.run([*git_base, "init", "-q"], check=True, capture_output=True)
        for rel, body in base_files.items():
            _write(repo / rel, body)
        subprocess.run([*git_base, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git_base, "commit", "-q", "--allow-empty", "-m", "base"], check=True, capture_output=True)
        base_sha = subprocess.run([*git_base, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

        for p in list(repo.rglob("*")):
            if ".git" not in p.parts and p.is_file():
                p.unlink()
        for rel, body in head_files.items():
            _write(repo / rel, body)
        subprocess.run([*git_base, "add", "-A"], check=True, capture_output=True)
        subprocess.run([*git_base, "commit", "-q", "--allow-empty", "-m", msg], check=True, capture_output=True)
        patch = subprocess.run(
            [*git_base, "format-patch", "--stdout", f"{base_sha}..HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return patch.stdout


class DeterministicChecksTests(unittest.TestCase):
    def test_perimeter_test_weakening_migration_risk_and_secrets(self) -> None:
        ticket_ok = "# 0001 — Update calc\n\n## Perimeter\n\n- `src/calc.py`\n- `tests/test_calc.py`\n"
        diff_escape = _make_git_diff(
            {"src/calc.py": "x = 1\n", "src/extra.py": "y = 1\n"},
            {"src/calc.py": "x = 2\n", "src/extra.py": "y = 2\n"},
        )
        res_escape = reviewer_eval.run_deterministic_checks(
            ticket_ok, diff_escape, repo_root=ROOT, use_gitleaks=False
        )
        self.assertTrue(res_escape["defective"])
        self.assertIn("perimeter", res_escape["failed_gates"])

        diff_weak = _make_git_diff(
            {"src/calc.py": "x = 1\n", "tests/test_calc.py": "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n"},
            {"src/calc.py": "x = 2\n", "tests/test_calc.py": "def test_a():\n    assert 1 == 1\n"},
        )
        res_weak = reviewer_eval.run_deterministic_checks(
            ticket_ok, diff_weak, repo_root=ROOT, use_gitleaks=False
        )
        self.assertIn("test-weakening", res_weak["failed_gates"])

        mig_ticket = "# 0002 — Drop col\n\n## Perimeter\n\n- `migrations/0002_drop.sql`\n"
        diff_drop = _make_git_diff(
            {"migrations/0001_init.sql": "CREATE TABLE items (id INT, old_col TEXT);\n"},
            {
                "migrations/0001_init.sql": "CREATE TABLE items (id INT, old_col TEXT);\n",
                "migrations/0002_drop.sql": "ALTER TABLE items DROP COLUMN old_col;\n",
            },
        )
        res_drop = reviewer_eval.run_deterministic_checks(
            mig_ticket, diff_drop, repo_root=ROOT, policy={"RISK_HIGH_MIGRATIONS": "migrations/*"}, use_gitleaks=False
        )
        self.assertIn("migration-safety", res_drop["failed_gates"])
        self.assertEqual(res_drop["checks"]["risk"]["level"], "high")
        self.assertEqual(res_drop["checks"]["risk"]["decision"], "review")

        with tempfile.TemporaryDirectory() as tmp:
            base_dir = Path(tmp) / "base"
            _write(base_dir / "migrations" / "0001_init.sql", "CREATE TABLE items (id INT, old_col TEXT);\n")
            contract_ticket = mig_ticket + "\nContract-After: 0001_init.sql\n"
            res_contract = reviewer_eval.run_deterministic_checks(
                contract_ticket,
                diff_drop,
                repo_root=ROOT,
                policy={"RISK_HIGH_MIGRATIONS": "migrations/*"},
                base_dir=base_dir,
                use_gitleaks=False,
            )
            self.assertFalse(res_contract["defective"])
            self.assertTrue(res_contract["checks"]["migration-safety"]["go"])

        pem_marker = "-----BEGIN " + "PRIVATE KEY-----"
        diff_secret = _make_git_diff(
            {"src/calc.py": "x = 1\n"},
            {"src/calc.py": f'PEM = "{pem_marker}"\n'},
        )
        res_secret = reviewer_eval.run_deterministic_checks(
            ticket_ok, diff_secret, repo_root=ROOT, use_gitleaks=False
        )
        self.assertIn("secrets", res_secret["failed_gates"])


class ReplayAndMetricsTests(unittest.TestCase):
    def test_committed_12_cases_replay_without_keys_and_strict_sha(self) -> None:
        report = reviewer_eval.evaluate_suite(
            ROOT / "fixtures" / "reviewer-eval",
            mode="replay",
            repo_root=ROOT,
            strict_replay_sha=True,
            use_gitleaks=False,
            environ={},
        )
        self.assertEqual(report["schema"], reviewer_eval.SCHEMA)
        self.assertEqual(report["summary"]["defective_total"], 6)
        self.assertEqual(report["summary"]["clean_total"], 6)
        self.assertEqual(report["deterministic"]["caught"], 2)
        self.assertEqual(report["deterministic"]["false_positives"], 0)
        self.assertEqual(report["combined"]["caught"], 6)
        self.assertEqual(report["combined"]["false_positives"], 0)
        self.assertEqual(report["combined"]["precision"], 1.0)
        self.assertEqual(report["combined"]["recall"], 1.0)
        self.assertEqual(report["combined"]["f1"], 1.0)
        self.assertEqual(len(report["cases"]), 12)
        for case in report["cases"]:
            self.assertTrue(case["reviewer"]["sha_match"], f"{case['id']} sha mismatch")
            self.assertIn(case["defect_class"], report["combined"]["per_class"])

    def test_compute_metrics_precision_recall_f1_and_skipped(self) -> None:
        rows = [
            {"expected_defective": True, "defect_class": "a", "pred": True},
            {"expected_defective": True, "defect_class": "b", "pred": False},
            {"expected_defective": True, "defect_class": "c", "pred": None},
            {"expected_defective": False, "defect_class": "clean", "pred": True},
            {"expected_defective": False, "defect_class": "clean", "pred": False},
        ]
        m = reviewer_eval.compute_metrics(rows, pred_key="pred")
        self.assertEqual(m["cases_total"], 5)
        self.assertEqual(m["cases_evaluated"], 4)
        self.assertEqual(m["cases_skipped"], 1)
        self.assertEqual(m["caught"], 1)
        self.assertEqual(m["missed"], 1)
        self.assertEqual(m["false_positives"], 1)
        self.assertEqual(m["true_negatives"], 1)
        self.assertEqual(m["precision"], 0.5)
        self.assertEqual(m["recall"], 0.5)
        self.assertEqual(m["f1"], 0.5)
        self.assertEqual(m["false_positive_rate"], 0.5)
        self.assertEqual(m["per_class"]["c"]["catch_rate"], 0.0)


class LivePluggableAndQuotaTests(unittest.TestCase):
    def _build_synth_suite(self, root: Path) -> Path:
        _write(root / "roles" / "test-reviewer" / "prompt.md", "Review the diff.\n")
        _write(root / "roles" / "test-reviewer" / "role.env", "THINKING=high\n")
        _write(root / "policy" / "merge.env", "RISK_SIZE_MEDIUM_LINES=200\nRISK_AUTO_MERGE_LEVELS=low\n")
        _write(
            root / "profiles" / "providers.json",
            json.dumps(
                {
                    "providers": {
                        "alpha": {
                            "label": "Alpha",
                            "default_harness": "alpha-cli",
                            "env_var": "SYNTH_ALPHA_CRED",
                            "default_model": "alpha-1",
                        }
                    }
                }
            ),
        )
        _write(root / "profiles" / "l1.env", "MODEL_PROVIDER=alpha\nMODEL=alpha-1\n")

        fdir = root / "fixtures" / "reviewer-eval"
        cases = []
        for idx, exp in enumerate((True, True, False), 1):
            cid = f"0{idx}-case"
            cdir = fdir / "cases" / cid
            _write(cdir / "ticket.md", f"# 000{idx} — Case {idx}\n\n## Perimeter\n\n- `src/m{idx}.py`\n")
            diff = _make_git_diff({f"src/m{idx}.py": "v = 1\n"}, {f"src/m{idx}.py": "v = 2\n"})
            _write(cdir / "diff.patch", diff)
            cases.append(
                {
                    "id": cid,
                    "title": f"Case {idx}",
                    "path": f"cases/{cid}",
                    "expected_defective": exp,
                    "expected_verdict": "CHANGES-REQUESTED" if exp else "APPROVED",
                    "defect_class": "bug" if exp else "clean",
                    "reviewer_role": "test-reviewer",
                }
            )
        _write(fdir / "manifest.json", json.dumps({"schema": "1", "cases": cases}))
        return fdir

    def test_quota_exhaustion_stops_calling_immediately_and_records_unrun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fdir = self._build_synth_suite(root)
            calls: list[str] = []

            def fake_reviewer(**kw: object) -> dict[str, object]:
                cid = str(kw["case_id"])
                calls.append(cid)
                if len(calls) == 2:
                    raise reviewer_eval.QuotaExhaustedError("HTTP 429 RESOURCE_EXHAUSTED")
                return {
                    "raw_response": "CHANGES-REQUESTED\n\nFound defect.",
                    "model": "synth-1",
                    "tokens": {"input": 100, "output": 20, "thoughts": 10, "total": 130},
                    "latency_s": 0.25,
                }

            report = reviewer_eval.evaluate_suite(
                fdir,
                mode="live",
                repo_root=root,
                use_gitleaks=False,
                reviewer_fn=fake_reviewer,
                record_replay=True,
                environ={"SYNTH_ALPHA_CRED": "CANARY-SYNTH-CRED-9999"},
            )
            self.assertEqual(calls, ["01-case", "02-case"])
            self.assertIsNotNone(report["stopped_reason"])
            self.assertIn("quota_exhausted", report["stopped_reason"])
            self.assertEqual(report["reviewer"]["cases_evaluated"], 1)
            self.assertEqual(report["reviewer"]["cases_skipped"], 2)
            self.assertEqual(report["cases"][1]["reviewer"]["status"], "not_run_quota_exhausted")
            self.assertEqual(report["cases"][2]["reviewer"]["status"], "not_run_quota_exhausted")
            self.assertTrue((fdir / "cases" / "01-case" / "replay.json").is_file())
            self.assertFalse((fdir / "cases" / "02-case" / "replay.json").exists())
            self.assertNotIn("CANARY-SYNTH-CRED-9999", json.dumps(report))

    def test_live_runtime_refuses_missing_or_placeholder_cred_and_no_secret_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_synth_suite(root)
            canary = "CANARY-SECRET-MUST-NOT-LEAK-777"
            with self.assertRaises(reviewer_eval.EvalRefused):
                reviewer_eval.resolve_live_runtime(root, environ={"SYNTH_ALPHA_CRED": "replace-me"})
            rt = reviewer_eval.resolve_live_runtime(root, environ={"SYNTH_ALPHA_CRED": canary})
            self.assertEqual(rt["cred_env"], "SYNTH_ALPHA_CRED")
            self.assertNotIn(canary, json.dumps(rt))


class MapBriefCompareTests(unittest.TestCase):
    def test_compare_map_brief_off_vs_on_synthetic_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            git_base = [
                "git",
                "-c", "user.name=Tester",
                "-c", "user.email=tester@lights-out.invalid",
                "-c", "commit.gpgsign=false",
                "-c", "init.defaultBranch=main",
                "-C", str(root),
            ]
            subprocess.run([*git_base, "init", "-q"], check=True, capture_output=True)
            _write(root / "roles" / "architect" / "prompt.md", "Review structure.\n")
            _write(root / "roles" / "architect" / "role.env", "THINKING=high\n")
            _write(root / "src" / "calc.py", "def add(a, b):\n    return a + b\n")
            _write(root / "tests" / "test_calc.py", "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
            _write(root / "docs" / "adr" / "0001-calc.md", "# ADR 0001\n\n- Status: accepted\n\nCovers `src/calc.py`.\n")
            _write(root / "docs" / "guide.md", "# Guide\n\nSee `src/calc.py`.\n")
            _write(
                root / "tickets" / "0001.md",
                "# 0001 — Fix calc\n\n## Acceptance criteria\n\nGiven inputs When added Then sum.\n\n## Perimeter\n\n- `src/calc.py`\n",
            )
            subprocess.run([*git_base, "add", "-A"], check=True, capture_output=True)
            subprocess.run([*git_base, "commit", "-q", "-m", "seed"], check=True, capture_output=True)

            patch_text = _make_git_diff(
                {"src/calc.py": "def add(a, b):\n    return a + b\n"},
                {"src/calc.py": "def add(a: int, b: int) -> int:\n    return a + b\n"},
            )
            _write(root / "patches" / "0001.patch", patch_text)

            seen_prompts: dict[str, str] = {}

            def fake_arch(**kw: object) -> dict[str, object]:
                cid = str(kw["case_id"])
                prompt = str(kw["prompt"])
                seen_prompts[cid] = prompt
                if cid.endswith(":on"):
                    return {
                        "raw_response": "APPROVED\n\nAligned with `docs/adr/0001-calc.md` and `docs/guide.md`.",
                        "model": "synth-arch",
                        "tokens": {"input": 180, "output": 20, "thoughts": 30, "total": 230},
                        "latency_s": 0.4,
                    }
                return {
                    "raw_response": "APPROVED\n\nMinimal change inside perimeter.",
                    "model": "synth-arch",
                    "tokens": {"input": 100, "output": 15, "thoughts": 20, "total": 135},
                    "latency_s": 0.3,
                }

            cmp_rep = reviewer_eval.compare_map_brief(
                [{"id": "0001", "ticket_path": "tickets/0001.md", "patch_path": "patches/0001.patch", "expected_verdict": "APPROVED"}],
                repo_root=root,
                map_path=None,
                mode="live",
                reviewer_fn=fake_arch,
            )
            self.assertIn("## Project map", seen_prompts["0001:on"])
            self.assertNotIn("## Project map", seen_prompts["0001:off"])
            s = cmp_rep["summary"]
            self.assertEqual(s["off"]["correct"], 1)
            self.assertEqual(s["on"]["correct"], 1)
            self.assertGreater(s["delta"]["prompt_bytes"], 0)
            self.assertEqual(s["delta"]["matched_slice_adr_citations"], 1)
            self.assertEqual(s["delta"]["matched_slice_page_citations"], 1)


class WrapperContractAndGovernanceTests(unittest.TestCase):
    def test_wrapper_line_budget_cli_contract_and_adr_0137(self) -> None:
        sh_path = ROOT / "core" / "reviewer_eval.sh"
        code_lines = [
            ln for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25)

        proc = subprocess.run(
            ["sh", str(sh_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\x1b", lines[0])
        self.assertRegex(
            lines[0],
            r"^GO: reviewer-eval — \d+/\d+ defects caught, \d+/\d+ false positives \(replay\)$",
        )

        proc_json = subprocess.run(
            ["sh", str(sh_path)],
            cwd=str(ROOT),
            env={**os.environ, "LIGHTS_OUT_CLI_ARGS": "--json"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc_json.returncode, 0, proc_json.stderr)
        parsed = json.loads(proc_json.stdout)
        self.assertEqual(parsed["schema"], reviewer_eval.SCHEMA)
        self.assertEqual(parsed["combined"]["caught"], 6)

        adr_path = ROOT / "docs" / "adr" / "0137-labeled-diff-reviewer-and-catch-rate-eval.md"
        self.assertEqual(adr_gov.validate_adr(adr_path.read_text(encoding="utf-8")), [])

        scoreboard = json.loads((ROOT / "docs" / "scoreboard" / "reviewer-catch-rate.json").read_text(encoding="utf-8"))
        self.assertEqual(scoreboard["schema"], "lights-out/reviewer-catch-rate/1")
        statuses = {item["provider"]: item["status"] for item in scoreboard["blocked_or_not_run"]}
        self.assertEqual(statuses["anthropic"], "blocked_billing")
        self.assertEqual(statuses["openai"], "not_run")
        self.assertEqual(statuses["zai"], "not_run")


if __name__ == "__main__":
    unittest.main()
