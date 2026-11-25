# SPDX-License-Identifier: Apache-2.0
"""Unit and contract tests for M94 ci/checks/* modules and activated ci/ shell-boundary ratchet."""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci.checks import (
    authors,
    board,
    docs_drift,
    evidence_check,
    map_check,
    plan_check,
    prompts,
    roles,
    secrets_check,
    secrets_history,
    shell_boundary,
)
from core import adr_gov

ROOT = Path(__file__).resolve().parents[2]

MIGRATED = (
    ("ci/roles.sh", "roles", "ci.checks.roles"),
    ("ci/authors.sh", "authors", "ci.checks.authors"),
    ("ci/map.sh", "map", "ci.checks.map_check"),
    ("ci/plan.sh", "plan", "ci.checks.plan_check"),
    ("ci/board.sh", "board", "ci.checks.board"),
    ("ci/prompts.sh", "prompts", "ci.checks.prompts"),
    ("ci/secrets.sh", "secrets", "ci.checks.secrets_check"),
    ("ci/secrets-history.sh", "secrets-history", "ci.checks.secrets_history"),
    ("ci/docs-drift.sh", "docs-drift", "ci.checks.docs_drift"),
    ("ci/evidence.sh", "evidence", "ci.checks.evidence_check"),
    ("ci/shell-boundary.sh", "shell-boundary", "ci.checks.shell_boundary"),
)


def _code_lines(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


class WrapperAndAdrTests(unittest.TestCase):
    """Verify thin wrapper budgets, verdict wiring, shellcheck, and ADR 0136."""

    def test_wrappers_honor_budget_and_verdict_contract(self) -> None:
        for rel, expected_name, expected_mod in MIGRATED:
            with self.subTest(script=rel):
                path = ROOT / rel
                text = path.read_text(encoding="utf-8")
                lines = text.splitlines()
                self.assertGreaterEqual(len(lines), 2)
                self.assertEqual(lines[0], "#!/bin/sh")
                self.assertEqual(lines[1], "# SPDX-License-Identifier: Apache-2.0")
                self.assertRegex(text, r"(?m)^set -eu$")
                self.assertRegex(text, rf"(?m)^name={re.escape(expected_name)}$")
                self.assertRegex(text, r"(?m)^\. ci/lib\.sh$")
                self.assertIn(f"verdict {expected_mod}", text)
                code = _code_lines(path)
                cap = 20 if rel == "ci/shell-boundary.sh" else 25
                self.assertLessEqual(len(code), cap, f"{rel} has {len(code)} code lines > {cap}")

        sb_text = (ROOT / "ci" / "shell-boundary.sh").read_text(encoding="utf-8")
        self.assertRegex(sb_text, r"(?m)^debt=''\s*$")

        if shutil.which("shellcheck"):
            proc = subprocess.run(
                ["shellcheck", "-s", "sh", "ci/lib.sh", *[rel for rel, _, _ in MIGRATED]],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_adr_0136_validates_cleanly(self) -> None:
        adr = ROOT / "docs" / "adr" / "0136-activate-ci-shell-boundary-ratchet.md"
        self.assertTrue(adr.is_file(), f"missing {adr}")
        text = adr.read_text(encoding="utf-8")
        self.assertIn("- Status: accepted", text)
        self.assertIn("- Date: 2026-11-25", text)
        self.assertEqual(adr_gov.validate_adr(text), [])


class ShellBoundaryRatchetTests(unittest.TestCase):
    """Verify activated ci/ ratchet alongside core/ and gates/ zero-debt rules."""

    def test_constants_and_real_repo_compliance(self) -> None:
        self.assertEqual(shell_boundary.LIMIT, 30)
        self.assertEqual(
            shell_boundary.CORE_GATES_HOST,
            ("core/doctor.sh", "core/attach.sh", "core/preflight.sh", "core/progress.sh"),
        )
        self.assertEqual(shell_boundary.CORE_GATES_DEBT, {})
        self.assertEqual(shell_boundary.CI_HOST, ("ci/lib.sh", "ci/engine-compat.sh"))
        self.assertIsNone(shell_boundary.self_test())
        self.assertEqual(shell_boundary.judge(ROOT), ("", True))
        for rel, ceiling in shell_boundary.CI_DEBT.items():
            p = ROOT / rel
            self.assertTrue(p.is_file(), rel)
            self.assertGreater(ceiling, 30, rel)
            self.assertEqual(shell_boundary.code_lines(p), ceiling, rel)

    def test_ci_ratchet_catches_all_four_clauses_plus_missing_host(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for subdir in ("core", "gates", "ci"):
                (root / subdir).mkdir()
            (root / "ci" / "lib.sh").write_text("".join(f": {i}\n" for i in range(112)), encoding="utf-8")
            (root / "ci" / "ok.sh").write_text("".join(f": {i}\n" for i in range(30)), encoding="utf-8")
            (root / "ci" / "unlisted.sh").write_text("".join(f": {i}\n" for i in range(31)), encoding="utf-8")
            (root / "ci" / "grown.sh").write_text("".join(f": {i}\n" for i in range(41)), encoding="utf-8")
            (root / "ci" / "shrunk.sh").write_text("".join(f": {i}\n" for i in range(39)), encoding="utf-8")
            (root / "ci" / "paid.sh").write_text("".join(f": {i}\n" for i in range(20)), encoding="utf-8")

            found = shell_boundary.flaws(
                root,
                core_gates_host=(),
                core_gates_debt={},
                ci_host=("ci/lib.sh", "ci/missing-host.sh"),
                ci_debt={"ci/grown.sh": 40, "ci/shrunk.sh": 40, "ci/paid.sh": 40, "ci/deleted.sh": 50},
            )
            self.assertEqual(
                found,
                sorted(
                    [
                        "ci/deleted.sh is listed and gone: remove its entry",
                        "ci/grown.sh grew to 41 code lines past its debt of 40: move logic to Python",
                        "ci/missing-host.sh is listed and gone: remove its entry",
                        "ci/paid.sh has 20 code lines, at most 30: remove its debt entry",
                        "ci/shrunk.sh is down to 39 code lines: lower its debt entry from 40",
                        "ci/unlisted.sh has 31 code lines, over 30: move its logic to Python",
                    ]
                ),
            )


class CleanBoundaryModulesTests(unittest.TestCase):
    """Behavior and planted-failure proofs for the 10 clean-boundary check modules."""

    def test_roles_accepts_matching_count_and_rejects_drift(self) -> None:
        self.assertIsNone(roles.self_test())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("alpha", "beta"):
                (root / "roles" / name).mkdir(parents=True)
                (root / "roles" / name / "role.env").write_text("TIMEOUT_S=60\n", encoding="utf-8")
                (root / "roles" / name / "prompt.md").write_text("Two roles collaborate.\n", encoding="utf-8")
            (root / "README.md").write_text("Uses 2 agent roles.\n", encoding="utf-8")
            self.assertEqual(roles.judge(root), ("", True))

            (root / "README.md").write_text("Uses six agent roles.\n", encoding="utf-8")
            msg, ok = roles.judge(root)
            self.assertFalse(ok)
            self.assertIn("a stated role count disagrees with roles/", msg)
            self.assertIn("README.md:1: says 'six agent roles'", msg)

    def test_authors_self_test_and_email_rules(self) -> None:
        self.assertEqual(authors.bad_email_reason("45848139+nicolaguglielmi@users.noreply.github.com"), "")
        self.assertEqual(authors.bad_email_reason("dev@example.org"), "")
        self.assertEqual(authors.bad_email_reason("49699333+dependabot[bot]@users.noreply.github.com"), "")
        self.assertIn("Anthropic", authors.bad_email_reason("Noreply@Anthropic.com"))
        self.assertIn("no account id", authors.bad_email_reason("nicola@users.noreply.github.com"))
        self.assertIsNone(authors.self_test())

    def test_map_check_extract_perimeter(self) -> None:
        ticket = "# Title\n\n## Perimeter\n\n- `.lights-out/worker.md`\n\n## Notes\n\n- ignored\n"
        self.assertEqual(map_check._extract_perimeter(ticket), ["- `.lights-out/worker.md`"])

    def test_plan_check_self_test_and_real_workflows(self) -> None:
        self.assertIsNone(plan_check.self_test(ROOT))
        self.assertEqual(plan_check.judge(ROOT), ("", True))
        self.assertEqual(plan_check.classify_paths(ROOT), "code")
        self.assertEqual(plan_check.classify_paths(ROOT, "docs/PLAN.md", "site/render.py"), "docs")

    def test_board_checks_and_verb_guard(self) -> None:
        self.assertIsNone(board.check_tower_table(ROOT))
        self.assertIsNone(board.check_gates_order(ROOT))
        self.assertIsNone(board.check_read_only_verbs(ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "board").mkdir()
            (root / "board" / "serve.py").write_text('adapter("DELIVERY", "rollback")\n', encoding="utf-8")
            err = board.check_read_only_verbs(root)
            self.assertIsNotNone(err)
            assert err is not None
            self.assertIn("rollback", err)

    def test_prompts_drift_and_planted_self_test(self) -> None:
        self.assertEqual(prompts.find_tool_drift(ROOT), [])
        rules = prompts.build_rules(["risk", "sast", "mutation"], ["ci", "help"])
        self.assertIsNone(prompts.self_test(rules))
        self.assertIsNone(prompts.check_text("Update files under gates/ only.\n", rules))
        self.assertEqual(prompts.check_text("Run ./go ci first.\n", rules), "./go ci")
        self.assertEqual(prompts.judge(ROOT), ("", True))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "gates").mkdir()
            (root / "gates" / "deps.sh").write_text("osv-scanner\n", encoding="utf-8")
            drift = prompts.find_tool_drift(root, tools=("osv-scanner", "gitleaks"))
            self.assertEqual(drift, ["gitleaks"])

    def test_secrets_check_unignored_exempt_and_history_suite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            env = {
                **os.environ,
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "ci",
                "GIT_AUTHOR_EMAIL": "ci@localhost",
                "GIT_COMMITTER_NAME": "ci",
                "GIT_COMMITTER_EMAIL": "ci@localhost",
            }
            subprocess.run(["git", "-C", str(repo), "init", "-q"], env=env, check=True)
            (repo / ".gitignore").write_text("", encoding="utf-8")
            msg, ok = secrets_check.judge(repo, env={})
            self.assertFalse(ok)
            self.assertIn(".gitignore does not ignore .crew/cosign.key", msg)

        if shutil.which("gitleaks"):
            self.assertEqual(secrets_history.judge(ROOT), ("", True))
            # Verify attached-mode StringIO stderr compatibility when run in-process.
            with tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp) / "att"
                repo.mkdir()
                env = {
                    **os.environ,
                    "GIT_CONFIG_GLOBAL": "/dev/null",
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_AUTHOR_NAME": "ci",
                    "GIT_AUTHOR_EMAIL": "ci@localhost",
                    "GIT_COMMITTER_NAME": "ci",
                    "GIT_COMMITTER_EMAIL": "ci@localhost",
                }
                subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env, check=True)
                (repo / "ok.py").write_text("x = 1\n", encoding="utf-8")
                subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env, check=True)
                subprocess.run(
                    ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base"],
                    env=env,
                    check=True,
                )
                base_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
                (repo / "ok.py").write_text("x = 2\n", encoding="utf-8")
                subprocess.run(
                    ["git", "-C", str(repo), "-c", "commit.gpgsign=false", "commit", "-q", "-am", "head"],
                    env=env,
                    check=True,
                )
                head_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
                err_buf = io.StringIO()
                with contextlib.redirect_stderr(err_buf):
                    msg, ok = secrets_check.judge(
                        ROOT,
                        env={"WORKLOAD_ATTACHED": "1", "WORKLOAD": str(repo), "BASE": base_sha, "HEAD": head_sha},
                    )
                self.assertTrue(ok, msg)

    def test_docs_drift_requires_map_exclude_and_catches_site_drift(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            msg, ok = docs_drift.judge(ROOT)
            self.assertFalse(ok)
            self.assertEqual(msg, "MAP_EXCLUDE not set; run this via ./go ci")

        proc = subprocess.run(
            ["sh", "ci/docs-drift.sh"],
            cwd=ROOT,
            env={k: v for k, v in os.environ.items() if k != "MAP_EXCLUDE"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "NO-GO: docs-drift — MAP_EXCLUDE not set; run this via ./go ci")

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp)
            (sp / "site").mkdir()
            (sp / "docs").mkdir()
            (sp / "lights-out").write_text("#!/bin/sh\nversion=0.9.0\n", encoding="utf-8")
            (sp / "site" / "index.html").write_text(
                '<p>v0.8.0 ghcr.io/nicolaguglielmi/lights-out-gates:0.8.0</p>\n'
                '<a href="https://github.com/nicolaguglielmi/lights-out/blob/main/docs/missing.md">x</a>\n'
                '<code>./go nope</code>\n',
                encoding="utf-8",
            )
            findings = docs_drift.site_drift(sp, {"help", "ci"})
            self.assertEqual(
                findings,
                [
                    "site/index.html:1: version v0.8.0 is not the wrapper's 0.9.0",
                    "site/index.html:1: image tag 0.8.0 is not the wrapper's 0.9.0",
                    "site/index.html:2: link to docs/missing.md names nothing",
                    "site/index.html:3: command ./go nope names nothing",
                ],
            )

    def test_evidence_check_helpers_and_preconditions(self) -> None:
        sample = {
            "a": {"source": "s1", "value": 1},
            "b": {"source": "s2", "missing": "none"},
            "nested": [{"source": "s3", "missing": "absent"}],
        }
        self.assertEqual(evidence_check._count_missing_leaves(sample), 2)
        with tempfile.TemporaryDirectory() as tmp:
            msg, ok = evidence_check.judge(Path(tmp))
            self.assertFalse(ok)
            self.assertEqual(msg, "core/evidence.sh not found")
        if shutil.which("cosign"):
            text, ok = evidence_check.judge(ROOT)
            self.assertTrue(ok, text)
            self.assertIn("signed, verified, tamper rejected", text)


if __name__ == "__main__":
    unittest.main()
