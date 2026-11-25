# SPDX-License-Identifier: Apache-2.0
"""Unit and contract tests for ci/checks/contract_v1.py and ci/contract-v1.sh (docs/adr/0138)."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from ci.checks import contract_v1
from core import contract_spec

ROOT = Path(__file__).resolve().parents[2]


def _code_lines(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


class ContractV1WrapperAndSelfTestTests(unittest.TestCase):
    """Verify ci/contract-v1.sh budget, verdict contract, shellcheck, and self-test."""

    def test_wrapper_budget_verdict_and_shellcheck(self) -> None:
        script = ROOT / "ci" / "contract-v1.sh"
        self.assertTrue(script.is_file(), f"missing {script}")
        text = script.read_text(encoding="utf-8")
        lines = text.splitlines()
        self.assertGreaterEqual(len(lines), 2)
        self.assertEqual(lines[0], "#!/bin/sh")
        self.assertEqual(lines[1], "# SPDX-License-Identifier: Apache-2.0")
        self.assertRegex(text, r"(?m)^set -eu$")
        self.assertRegex(text, r"(?m)^name=contract-v1$")
        self.assertRegex(text, r"(?m)^\. ci/lib\.sh$")
        self.assertIn('verdict ci.checks.contract_v1 "$@"', text)

        code = _code_lines(script)
        self.assertLessEqual(len(code), 20, f"ci/contract-v1.sh has {len(code)} code lines > 20")

        if shutil.which("shellcheck"):
            proc = subprocess.run(
                ["shellcheck", "-s", "sh", "ci/lib.sh", "ci/contract-v1.sh"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_self_test_and_real_repo_contracts_pass(self) -> None:
        self.assertIsNone(contract_v1.self_test())
        contracts = contract_v1.discover_repo_contracts(ROOT)
        self.assertGreaterEqual(len(contracts), 9)
        msg, ok = contract_v1.judge(ROOT)
        self.assertTrue(ok, msg)
        self.assertIn("v1.0 frozen", msg)

    def test_shell_gate_emits_one_line_go_on_real_repo(self) -> None:
        proc = subprocess.run(
            ["sh", "ci/contract-v1.sh"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIsNone(contract_spec.validate_gate_line("contract-v1", proc.returncode, proc.stdout))
        self.assertRegex(proc.stdout, r"^GO: contract-v1 — v1\.0 frozen, \d+/\d+ contracts valid\n$")

    def test_judge_rejects_broken_repo_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp)
            contract_dir = probe / ".lights-out"
            contract_dir.mkdir(parents=True)
            valid_text = contract_v1._render_contract({"CONTRACT_VERSION": "1.0"})
            (contract_dir / "workload.env").write_text(valid_text, encoding="utf-8")

            ok_msg, ok = contract_v1.judge(probe)
            self.assertTrue(ok, ok_msg)

            # Break CONTRACT_VERSION to 2.0 -> must fail loudly.
            (contract_dir / "workload.env").write_text(
                contract_v1._render_contract({"CONTRACT_VERSION": "2.0"}),
                encoding="utf-8",
            )
            bad_msg, bad_ok = contract_v1.judge(probe)
            self.assertFalse(bad_ok)
            self.assertIn("unsupported major version 2", bad_msg)

            # Break required key and TOOLCHAIN_IMAGE -> both defects reported.
            (contract_dir / "workload.env").write_text(
                contract_v1._render_contract({"TEST_CMD": None, "TOOLCHAIN_IMAGE": "Not An Image"}),
                encoding="utf-8",
            )
            multi_msg, multi_ok = contract_v1.judge(probe)
            self.assertFalse(multi_ok)
            self.assertIn("TEST_CMD", multi_msg)
            self.assertIn("TOOLCHAIN_IMAGE", multi_msg)


if __name__ == "__main__":
    unittest.main()
