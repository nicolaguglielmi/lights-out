"""gates/migration-safety.sh end to end (docs/adr/0123): the wrapper, the policy and the output contract.

The migrations are the bundled workload's real expand (0002) and contract
(0003) steps, copied into a scratch repository at the path its policy.env
names. This file sits outside ci/app-free.sh, which keeps core/ and gates/
free of that path.
"""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.tests.gitrepo import Repo

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = "app/backend/migrations"
ANSI = re.compile(r"\x1b\[")


def real(name):
    return (ROOT / MIGRATIONS / name).read_text()


def path(name):
    return f"{MIGRATIONS}/{name}"


class MigrationGateE2ETest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.init = self.repo.commit({path("0001_init.sql"): real("0001_init.sql")}, "init")

    def gate(self, head, ticket=None):
        env = {k: v for k, v in os.environ.items() if k not in ("TICKET_FILE", "NOGO_DETAILS", "CI_RECORDS")}
        env.update(BASE=self.init, HEAD=head, WORKLOAD=self.repo.dir, WORKLOAD_CONTRACT=str(ROOT / "app"),
                   GATES_OUT=self.out)
        if ticket is not None:
            tf = os.path.join(self.out, "ticket.md")
            with open(tf, "w") as fh:
                fh.write(ticket)
            env["TICKET_FILE"] = tf
        got = subprocess.run(["sh", "gates/migration-safety.sh"], cwd=ROOT, env=env, capture_output=True,
                             text=True, check=False)
        self.assertEqual(len(got.stdout.splitlines()), 1, got.stdout)
        self.assertIsNone(ANSI.search(got.stdout + got.stderr))
        return got

    def test_expand_step_is_go(self):
        head = self.repo.commit({path("0002_expand_starts_at.sql"): real("0002_expand_starts_at.sql")})
        got = self.gate(head)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertTrue(got.stdout.startswith("GO: migration-safety"), got.stdout)

    def test_no_migration_is_go(self):
        head = self.repo.commit({"docs/note.txt": "x\n"})
        got = self.gate(head)
        self.assertEqual((got.returncode, got.stdout.split(" — ")[0]), (0, "GO: migration-safety"))

    def test_contract_step_in_the_expands_pr_is_nogo(self):
        head = self.repo.commit({path("0002_expand_starts_at.sql"): real("0002_expand_starts_at.sql"),
                                 path("0003_contract_starts_at.sql"): real("0003_contract_starts_at.sql")})
        got = self.gate(head)
        self.assertEqual(got.returncode, 1)
        self.assertTrue(got.stdout.startswith("NO-GO: migration-safety — "), got.stdout)
        self.assertIn(f"{path('0003_contract_starts_at.sql')}:2", got.stdout)
        self.assertIn("drop-column", got.stdout)
        for rule in ("drop-column", "rename", "set-not-null"):
            self.assertIn(f": {rule}:", got.stderr)

    def expanded(self):
        """init + the expand step, shipped: the base of the contract PR."""
        self.init = self.repo.commit({path("0002_expand_starts_at.sql"): real("0002_expand_starts_at.sql")},
                                     "expand")
        return self.init

    def test_contract_step_after_the_shipped_expand_needs_a_declaration(self):
        self.expanded()
        head = self.repo.commit({path("0003_contract_starts_at.sql"): real("0003_contract_starts_at.sql")})
        got = self.gate(head)
        self.assertEqual(got.returncode, 1)
        self.assertIn("Contract-After", got.stderr)

    def test_contract_declared_by_commit_trailer_is_go(self):
        self.expanded()
        head = self.repo.commit({path("0003_contract_starts_at.sql"): real("0003_contract_starts_at.sql")},
                                "contract\n\nContract-After: 0002_expand_starts_at.sql\n")
        got = self.gate(head)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("contract cleanup allowed", got.stdout)
        self.assertIn("allowed (contract after 0002_expand_starts_at.sql)", got.stderr)

    def test_contract_declared_by_ticket_section_with_the_expand_commit(self):
        expand = self.expanded()
        head = self.repo.commit({path("0003_contract_starts_at.sql"): real("0003_contract_starts_at.sql")})
        got = self.gate(head, ticket=f"# 0042\n\n## Contract migration\n\nDrops the text column after {expand}.\n")
        self.assertEqual(got.returncode, 0, got.stderr)

    def test_contract_after_a_step_that_never_shipped_is_nogo(self):
        head = self.repo.commit({path("0003_contract_starts_at.sql"): real("0003_contract_starts_at.sql")},
                                "contract\n\nContract-After: 0002_expand_starts_at.sql\n")
        got = self.gate(head)
        self.assertEqual(got.returncode, 1)
        self.assertIn("reference", got.stdout)

    def test_policy_path_alone_selects_a_file_without_structural_name(self):
        head = self.repo.commit({path("seed.txt"): "DROP TABLE users;\n"})
        got = self.gate(head)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("not analyzed", got.stderr)

    def test_missing_range_is_nogo_one_line(self):
        env = {k: v for k, v in os.environ.items() if k not in ("BASE", "HEAD")}
        got = subprocess.run(["sh", "gates/migration-safety.sh"], cwd=ROOT, env=env, capture_output=True,
                             text=True, check=False)
        self.assertEqual((got.returncode, got.stdout), (1, "NO-GO: migration-safety — BASE and HEAD must both be set\n"))


if __name__ == "__main__":
    unittest.main()
