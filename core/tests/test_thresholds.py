"""Unit tests for core/thresholds.py and core/thresholds.sh (docs/adr/0135).

Strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import thresholds

REPO = Path(__file__).resolve().parents[2]
CONTRACT_DIRNAME = "." + "lights" + "-out"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Tester",
        "GIT_AUTHOR_EMAIL": "tester@example.invalid",
        "GIT_COMMITTER_NAME": "Tester",
        "GIT_COMMITTER_EMAIL": "tester@example.invalid",
    }
    proc = subprocess.run(
        ["git", "-C", str(cwd), *args],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _write_policy_tree(root: Path, *, with_override: bool = True) -> None:
    (root / "policy").mkdir(parents=True, exist_ok=True)
    (root / "policy" / "merge.env").write_text(
        "# Platform merge defaults\n"
        "# Provenance, external: review size limit.\n"
        "RISK_SIZE_MEDIUM_LINES=200\n"
        "# Provenance, decision: one score quantum.\n"
        "MUTATION_MARGIN=0.10\n"
        "# Provenance, decision: verify bound.\n"
        "ERROR_RATE_MAX_DELTA=0.01\n"
        "RISK_REQUIRE_ADR=off\n",
        encoding="utf-8",
    )
    (root / "policy" / "rework.env").write_text(
        "# Provenance, decision: cap attempts.\n"
        "REWORK_MAX_ATTEMPTS=4\n"
        "# Provenance, decision: empty by default.\n"
        "SPEND_BUDGET_TICKET_USD=\n"
        "SPEND_BUDGET_DAY_USD=\n",
        encoding="utf-8",
    )
    (root / "policy" / "mainline.env").write_text(
        "# Provenance: the owner's decision matching daily merges.\n"
        "MAINLINE_GROUP_MAX=4\n",
        encoding="utf-8",
    )
    dev = root / "roles" / "dev-worker"
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "role.env").write_text(
        "THINKING=high\n"
        "# Provenance, measurement over decision: M9 measured headroom.\n"
        "TOKEN_BUDGET=2600000\n",
        encoding="utf-8",
    )
    contract = root / CONTRACT_DIRNAME
    contract.mkdir(parents=True, exist_ok=True)
    (contract / "workload.env").write_text(
        "# Provenance, measurement: 100 mutants, 80 killed.\n"
        "MUTATION_BASELINE=0.80\n"
        "WORKLOAD_IMAGE=example-runner:latest\n"
        "TEST_CMD=pytest -q\n",
        encoding="utf-8",
    )
    if with_override:
        (contract / "policy.env").write_text(
            "# Provenance, decision: tighten mutation margin for core module.\n"
            "MUTATION_MARGIN=0.05\n"
            "# Provenance, decision: keep same lines threshold explicitly.\n"
            "RISK_SIZE_MEDIUM_LINES=200\n",
            encoding="utf-8",
        )


class ThresholdsCollectionTests(unittest.TestCase):
    def test_parses_defaults_provenance_overrides_and_env_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_policy_tree(root, with_override=True)
            crew = root / ".crew"
            crew.mkdir()
            (crew / "start-plan.env").write_text(
                "export SPEND_BUDGET_TICKET_USD='4.50'\n"
                "UNRELATED_PLAN_KEY=ignored\n",
                encoding="utf-8",
            )
            env = {
                "SPEND_BUDGET_DAY_USD": "18.00",
                "REWORK_MAX_ATTEMPTS": "6",
            }
            report = thresholds.collect(root, env=env, include_git=False)

        by_id = {e["id"]: e for e in report["entries"]}
        self.assertNotIn("TEST_CMD", by_id)
        self.assertNotIn("WORKLOAD_IMAGE", by_id)
        self.assertNotIn("UNRELATED_PLAN_KEY", by_id)

        margin = by_id["MUTATION_MARGIN"]
        self.assertEqual(margin["default"], "0.10")
        self.assertEqual(margin["active"], "0.05")
        self.assertTrue(margin["changed"])
        self.assertTrue(margin["overridden"])
        self.assertEqual(margin["source"], f"{CONTRACT_DIRNAME}/policy.env")
        self.assertEqual(margin["provenance_kind"], "decision")
        self.assertIn("tighten mutation margin", margin["provenance"])

        same_override = by_id["RISK_SIZE_MEDIUM_LINES"]
        self.assertEqual(same_override["default"], "200")
        self.assertEqual(same_override["active"], "200")
        self.assertFalse(same_override["changed"])
        self.assertTrue(same_override["overridden"])

        ticket_budget = by_id["SPEND_BUDGET_TICKET_USD"]
        self.assertEqual(ticket_budget["default"], "")
        self.assertEqual(ticket_budget["active"], "4.50")
        self.assertTrue(ticket_budget["changed"])
        self.assertEqual(ticket_budget["source"], ".crew/start-plan.env")
        self.assertEqual(ticket_budget["provenance_kind"], "decision")

        day_budget = by_id["SPEND_BUDGET_DAY_USD"]
        self.assertEqual(day_budget["active"], "18.00")
        self.assertEqual(day_budget["source"], "env")
        self.assertEqual(day_budget["provenance_kind"], "decision")

        rework = by_id["REWORK_MAX_ATTEMPTS"]
        self.assertEqual(rework["default"], "4")
        self.assertEqual(rework["active"], "6")
        self.assertEqual(rework["source"], "env")

        mainline = by_id["MAINLINE_GROUP_MAX"]
        self.assertEqual(mainline["provenance_kind"], "decision")
        self.assertFalse(mainline["overridden"])

        role_budget = by_id["roles/dev-worker/TOKEN_BUDGET"]
        self.assertEqual(role_budget["role"], "dev-worker")
        self.assertEqual(role_budget["active"], "2600000")
        self.assertEqual(role_budget["provenance_kind"], "measurement")
        self.assertEqual(report["summary"]["overridden"], 5)
        self.assertEqual(report["summary"]["changed"], 4)

    def test_git_history_captures_up_to_five_commits_per_policy_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_policy_tree(root, with_override=False)
            _git(root, "init", "-q")
            _git(root, "add", "policy")
            _git(root, "commit", "-q", "-m", "initial policy defaults")
            for idx in range(2, 7):
                (root / "policy" / "rework.env").write_text(
                    f"# Provenance, decision: iteration {idx}.\nREWORK_MAX_ATTEMPTS={idx}\n",
                    encoding="utf-8",
                )
                _git(root, "add", "policy/rework.env")
                _git(root, "commit", "-q", "-m", f"bump rework attempt {idx}")

            report = thresholds.collect(root, env={}, include_git=True)
            rework = next(e for e in report["entries"] if e["id"] == "REWORK_MAX_ATTEMPTS")
            self.assertEqual(len(rework["history"]), 5)
            self.assertEqual(rework["last_commit"]["subject"], "bump rework attempt 6")
            self.assertEqual(len(rework["last_commit"]["short"]), 12)
            self.assertIn("policy/rework.env", report["history"])

    def test_missing_defaults_raises_thresholds_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(thresholds.ThresholdsError):
                thresholds.collect(td, env={}, include_git=False)


class ThresholdsCliAndShellContractTests(unittest.TestCase):
    def test_shell_script_line_count_under_twenty_five_and_output_contract(self) -> None:
        sh_path = REPO / "core" / "thresholds.sh"
        code_lines = [
            ln
            for ln in sh_path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write_policy_tree(root, with_override=True)
            proc = subprocess.run(
                ["sh", "core/thresholds.sh", "--root", str(root), "--contract-dir", str(root / CONTRACT_DIRNAME), "--no-git"],
                cwd=REPO,
                env={k: v for k, v in os.environ.items() if k != "WORKLOAD_CONTRACT"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertRegex(proc.stdout.strip(), r"^GO: thresholds — \d+ keys \(\d+ overridden\)$")
            self.assertIn("MUTATION_MARGIN", proc.stderr)
            self.assertIn("KEY", proc.stderr)

            json_proc = subprocess.run(
                [sys.executable, "core/thresholds.py", "--json", "--root", str(root), "--no-git"],
                cwd=REPO,
                env={},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(json_proc.returncode, 0, json_proc.stderr)
            payload = json.loads(json_proc.stdout)
            self.assertEqual(payload["summary"]["total"], 11)
            self.assertEqual(payload["summary"]["overridden"], 2)


if __name__ == "__main__":
    unittest.main()
