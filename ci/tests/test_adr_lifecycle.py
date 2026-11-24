"""End-to-end tests for gates/adr-check.sh, written from docs/adr/0115 §3-§6
and the gate output contract (ci/lib.sh), never from the implementation.

The gate runs as `make gates` would run it: from the repository root, with
BASE and HEAD naming a range of the workload repository WORKLOAD, and the
workload's contract directory WORKLOAD_CONTRACT holding workload.env and an
optional policy.env over policy/merge.env (docs/adr/0037 §5).
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

from ci.checks import contract
from core import adr_gov
from core.tests.gitrepo import ENV, Repo

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GATE = "gates/adr-check.sh"
ADR = "docs/adr/0115-autonomous-adr-lifecycle-and-policy.md"

RECORD = """# ADR 0002 — A table

- Status: proposed
- Date: 2026-11-24

## Context

The service needs a table.

## Decision

Add it with a migration.

## Alternatives rejected

- A file on disk: no transactions.

## Consequences

One more migration to keep.
"""


class GateEndToEnd(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.contract = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        for d in (self.contract, self.out):
            self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(self.contract, "workload.env"), "w") as fh:
            fh.write("MUTATION_BASELINE=0.5\n")
        self.base = self.repo.commit({"svc/view.py": "x = 1\n"})

    def require(self, value):
        """The workload's own policy.env, as an owner opting in writes it."""
        with open(os.path.join(self.contract, "policy.env"), "w") as fh:
            fh.write(f"# Provenance, decision: the owner opts in (docs/adr/0115 §3).\nRISK_REQUIRE_ADR={value}\n"
                     "# Provenance, decision: this workload's migrations.\nRISK_HIGH_MIGRATIONS=db/migrations/*\n")

    def gate(self, head, base=None, **extra):
        env = dict(ENV, BASE=base or self.base, HEAD=head, WORKLOAD=self.repo.dir,
                   WORKLOAD_CONTRACT=self.contract, GATES_OUT=self.out, PYTHONPATH=ROOT, **extra)
        got = subprocess.run(["sh", GATE], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        self.assertIsNone(contract.shape("adr-check", got.returncode, got.stdout), got.stdout + got.stderr)
        return got

    def test_default_policy_is_off_and_go(self):
        head = self.repo.commit({"db/migrations/001.sql": "create table t;\n"})
        got = self.gate(head)
        self.assertEqual((got.returncode, got.stdout), (0, "GO: adr-check\n"), got.stderr)

    def test_high_without_record_is_one_no_go_line_details_on_stderr(self):
        self.require("high")
        head = self.repo.commit({"db/migrations/001.sql": "create table t;\n"})
        got = self.gate(head)
        self.assertEqual(got.returncode, 1, got.stdout + got.stderr)
        self.assertTrue(got.stdout.startswith("NO-GO: adr-check — "), got.stdout)
        self.assertNotIn("\x1b", got.stdout)
        self.assertIn("db/migrations/001.sql", got.stderr)

    def test_high_with_record_is_go(self):
        self.require("high")
        head = self.repo.commit({"db/migrations/001.sql": "x\n", "docs/adr/0002-a-table.md": RECORD})
        self.assertEqual(self.gate(head).stdout, "GO: adr-check\n")

    def test_high_with_commit_reference_is_go_structural_is_not(self):
        self.base = self.repo.commit({"docs/adr/0002-a-table.md": RECORD})
        head = self.repo.commit({"db/migrations/001.sql": "x\n"}, message="Add a table (ADR-0002)")
        self.require("high")
        self.assertEqual(self.gate(head).returncode, 0)
        self.require("structural")
        self.assertEqual(self.gate(head).returncode, 1)

    def test_high_with_reference_to_a_missing_record_is_no_go(self):
        self.require("high")
        head = self.repo.commit({"db/migrations/001.sql": "x\n"}, message="Add a table (ADR-9999)")
        got = self.gate(head)
        self.assertEqual(got.returncode, 1, got.stdout)
        self.assertIn("ADR-9999", got.stderr)

    def test_structural_new_lockfile_needs_a_record(self):
        self.require("structural")
        head = self.repo.commit({"web/package-lock.json": "{}\n"})
        got = self.gate(head)
        self.assertEqual(got.returncode, 1)
        self.assertIn("web/package-lock.json", got.stderr)

    def test_incomplete_record_is_no_go_naming_the_section(self):
        self.require("structural")
        bad = RECORD.replace("## Alternatives rejected\n\n- A file on disk: no transactions.\n", "")
        head = self.repo.commit({"db/migrations/001.sql": "x\n", "docs/adr/0002-a-table.md": bad})
        got = self.gate(head)
        self.assertEqual(got.returncode, 1)
        self.assertIn("Alternatives", got.stderr)

    def test_bad_override_is_no_go_naming_the_key(self):
        with open(os.path.join(self.contract, "policy.env"), "w") as fh:
            fh.write("# Provenance, decision: a typo.\nRISK_REQUIRE_ADR=always\n")
        got = self.gate(self.repo.commit({"svc/view.py": "x = 2\n"}))
        self.assertEqual(got.returncode, 1)
        self.assertIn("RISK_REQUIRE_ADR", got.stdout)

    def test_cold_run_without_a_range_is_one_no_go_line(self):
        env = {k: v for k, v in ENV.items() if k not in ("BASE", "HEAD")}
        got = subprocess.run(["sh", GATE], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(got.returncode, 1)
        self.assertIsNone(contract.shape("adr-check", got.returncode, got.stdout), got.stdout)

    def test_risk_gate_reports_adr_required(self):
        self.require("high")
        head = self.repo.commit({"db/migrations/001.sql": "x\n"})
        env = dict(ENV, BASE=self.base, HEAD=head, WORKLOAD=self.repo.dir, WORKLOAD_CONTRACT=self.contract,
                   GATES_OUT=self.out, PYTHONPATH=ROOT)
        got = subprocess.run(["sh", "gates/risk.sh"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        self.assertEqual(got.stdout, "GO: risk\n", got.stderr)
        self.assertIn("adr_required=true", got.stderr)


class Boundaries(unittest.TestCase):
    def read(self, rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_gate_is_under_the_shell_boundary(self):
        # docs/adr/0052 §4 and 0084 §5: at most 30 code lines.
        code = [l for l in self.read(GATE).splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertLessEqual(len(code), 30, code)
        self.assertIn("verdict core.gates.adr_check", self.read(GATE))

    def test_this_adr_passes_its_own_rule(self):
        self.assertEqual(adr_gov.validate_adr(self.read(ADR)), [])

    def test_core_names_no_vendor_workload_or_model(self):
        words = re.compile(r"envoy|cloud[ _-]?deploy|(^|[^a-z])argo|rollouts|flagger|flagd|unleash|gitea|github|gitlab"
                           r"|\bapp/|meetup|waitlist|geocoder|workloads/|\.attached|\.lights-out|_api_key", re.I)
        for rel in ("core/adr_gov.py", "core/gates/adr_check.py", GATE):
            with self.subTest(rel=rel):
                self.assertIsNone(words.search(self.read(rel)))

    def test_prompts_ask_for_decision_records_without_naming_the_check(self):
        for rel in ("roles/architect/prompt.md", "roles/dev-worker/prompt.md"):
            with self.subTest(rel=rel):
                text = self.read(rel)
                self.assertIn("decision record", text)
                self.assertNotRegex(text, r"(?i)(?<![\w-])adr-check(?![\w-])")


if __name__ == "__main__":
    unittest.main()
