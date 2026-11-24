"""Tests for core/adr_gov.py, the RISK_REQUIRE_ADR key in core/policy.py and
`adr_required` in core/risk.py, written from docs/adr/0115, never from the
implementation.
"""

import os
import shutil
import tempfile
import unittest

from core import adr_gov, policy, risk
from core.tests.gitrepo import Repo

EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

VALID = """# ADR 0001 — A choice

- Status: proposed
- Date: 2026-11-24

## Context

Why the choice is needed.

## Decision

What was chosen.

## Alternatives rejected

- Something else, and why not.

## Consequences

What follows.
"""

# The policy the range checks use: one workload path list, the platform's
# own surface, and the size rule (docs/adr/0016), plus the mode under test.
BASE_POLICY = {
    "RISK_HIGH_MIGRATIONS": "db/migrations/*",
    "RISK_HIGH_AUTH": "svc/auth.py",
    "RISK_HIGH_PAYMENTS": "",
    "RISK_HIGH_PLATFORM": "platform/*",
    "RISK_SIZE_MEDIUM_LINES": "200",
    "RISK_SIZE_EXCLUDE": "*.lock",
    "RISK_AUTO_MERGE_LEVELS": "low",
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def mode(value):
    return {**BASE_POLICY, "RISK_REQUIRE_ADR": value}


class NextNumberTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def touch(self, name):
        with open(os.path.join(self.dir, name), "w") as fh:
            fh.write("x\n")

    def test_empty_directory_is_0001(self):
        self.assertEqual(adr_gov.next_number(self.dir), "0001")

    def test_missing_directory_is_0001(self):
        self.assertEqual(adr_gov.next_number(os.path.join(self.dir, "nope")), "0001")

    def test_one_past_the_largest_zero_padded(self):
        for name in ("0001-a.md", "0007-b.md", "0003-c.md"):
            self.touch(name)
        self.assertEqual(adr_gov.next_number(self.dir), "0008")

    def test_gaps_are_not_reused(self):
        self.touch("0001-a.md")
        self.touch("0115-b.md")
        self.assertEqual(adr_gov.next_number(self.dir), "0116")

    def test_only_nnnn_dash_md_files_count(self):
        for name in ("README.md", "0099.md", "999-short.md", "0500-x.txt", "template.md"):
            self.touch(name)
        self.touch("0002-real.md")
        self.assertEqual(adr_gov.next_number(self.dir), "0003")


class ScaffoldTests(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.join(tempfile.mkdtemp(), "docs", "adr")

    def make(self, **kw):
        args = dict(title="Use a queue", context="The tower polls.", decision="Use a queue.",
                    alternatives=["Keep polling: it wastes calls."], consequences="One more process.",
                    date="2026-11-24")
        args.update(kw)
        return adr_gov.scaffold(self.dir, **args)

    def test_writes_numbered_slugged_file_with_every_section(self):
        path = self.make()
        self.assertEqual(os.path.basename(path), "0001-use-a-queue.md")
        with open(path) as fh:
            text = fh.read()
        self.assertTrue(text.startswith("# ADR 0001 — Use a queue\n"), text)
        for want in ("Status: proposed", "Date: 2026-11-24", "## Context", "The tower polls.",
                     "## Decision", "## Alternatives rejected", "- Keep polling: it wastes calls.",
                     "## Consequences", "## Provenance"):
            self.assertIn(want, text)
        self.assertEqual(adr_gov.validate_adr(text), [])

    def test_numbers_follow_existing_records(self):
        self.make()
        second = self.make(title="Second: a choice!")
        self.assertEqual(os.path.basename(second), "0002-second-a-choice.md")

    def test_status_and_provenance_are_written(self):
        path = self.make(status="accepted", provenance=["The cap — decision: one owner call."])
        text = read(path)
        self.assertIn("Status: accepted", text)
        self.assertIn("- The cap — decision: one owner call.", text)

    def test_default_date_is_an_iso_date(self):
        text = read(self.make(date=None))
        self.assertRegex(text, r"Date: \d{4}-\d{2}-\d{2}\n")

    def test_refuses_an_invalid_record_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            self.make(alternatives="")
        with self.assertRaises(ValueError):
            self.make(decision="Retry at most 5 times.")
        self.assertEqual(adr_gov.next_number(self.dir), "0001")

    def test_threshold_with_provenance_is_accepted(self):
        path = self.make(decision="Retry at most 5 times.",
                         provenance="5 retries — measurement: the p99 recovery in ten runs.")
        self.assertEqual(adr_gov.validate_adr(read(path)), [])


class ValidateTests(unittest.TestCase):
    def test_complete_record_is_valid(self):
        self.assertEqual(adr_gov.validate_adr(VALID), [])

    def test_each_required_section_missing_is_named(self):
        for section in ("Context", "Decision", "Alternatives"):
            with self.subTest(section=section):
                text = VALID.replace(f"## {section}", "## Something")
                defects = adr_gov.validate_adr(text)
                self.assertTrue(any(section in d for d in defects), defects)

    def test_empty_section_is_a_defect(self):
        text = VALID.replace("What was chosen.\n", "")
        defects = adr_gov.validate_adr(text)
        self.assertTrue(any("Decision" in d for d in defects), defects)

    def test_subsections_are_part_of_their_section(self):
        text = VALID.replace("What was chosen.\n", "### 1. The first part\n\nWhat was chosen.\n")
        self.assertEqual(adr_gov.validate_adr(text), [])
        only_heading = VALID.replace("What was chosen.\n", "### 1. The first part\n")
        self.assertTrue(any("Decision" in d for d in adr_gov.validate_adr(only_heading)))

    def test_alternatives_heading_may_be_considered(self):
        self.assertEqual(adr_gov.validate_adr(VALID.replace("rejected", "considered")), [])

    def test_threshold_without_provenance_is_a_defect(self):
        for line in ("The cap is 40 requests a second.",
                     "Retry at most 3 times.",
                     "BUDGET_USD=12",
                     "A timeout of 2.5 seconds."):
            with self.subTest(line=line):
                text = VALID.replace("What was chosen.", f"What was chosen.\n\n{line}")
                defects = adr_gov.validate_adr(text)
                self.assertEqual(len(defects), 1, defects)
                self.assertIn("threshold", defects[0])
                self.assertIn(line, defects[0])

    def test_threshold_with_provenance_in_its_paragraph_is_valid(self):
        for line in ("The cap is 40 requests a second, measured over ten runs.",
                     "Retry at most 3 times because the upstream says so.",
                     "Provenance, decision: the owner's call.\nBUDGET_USD=12",
                     "A timeout of 2.5 seconds (ADR 0016 §4)."):
            with self.subTest(line=line):
                text = VALID.replace("What was chosen.", f"What was chosen.\n\n{line}")
                self.assertEqual(adr_gov.validate_adr(text), [])

    def test_threshold_whose_number_is_in_the_provenance_section_is_valid(self):
        text = VALID.replace("What was chosen.", "The cap is 40 requests a second.")
        text += "\n## Provenance\n\n- 40 — external: the provider's documented limit.\n"
        self.assertEqual(adr_gov.validate_adr(text), [])

    def test_numbers_that_are_not_thresholds_are_ignored(self):
        lines = ("See docs/adr/0016-risk-model-and-merge-policy.md and ADR-0037 §5.",
                 "Decided on 2026-11-24 at milestone M75.",
                 "1. A numbered step with a limit of words.",
                 "The pin is v2.6.0, the maximum we tried.",
                 "Six roles and 3 screens.")
        for line in lines:
            with self.subTest(line=line):
                text = VALID.replace("What was chosen.", f"What was chosen.\n\n{line}")
                self.assertEqual(adr_gov.validate_adr(text), [])

    def test_fenced_code_is_ignored(self):
        text = VALID.replace("What was chosen.", "What was chosen.\n\n```\nLIMIT=99\n```")
        self.assertEqual(adr_gov.validate_adr(text), [])


class PolicyKeyTests(unittest.TestCase):
    def test_the_three_values_are_accepted(self):
        for value in ("off", "high", "structural"):
            with self.subTest(value=value):
                policy.check_value("RISK_REQUIRE_ADR", value)

    def test_anything_else_is_refused_by_name(self):
        for value in ("", "on", "HIGH", "low", "high:structural", " off"):
            with self.subTest(value=value):
                with self.assertRaises(policy.PolicyError) as got:
                    policy.check_value("RISK_REQUIRE_ADR", value)
                self.assertIn("RISK_REQUIRE_ADR", str(got.exception))

    def platform(self, extra=""):
        return "".join(f"{k}={v}\n" for k, v in BASE_POLICY.items()) + \
            "MUTATION_MARGIN=0.10\nERROR_RATE_MAX_DELTA=0.01\nLATENCY_P95_MAX_DELTA=0.25\n" + extra

    def test_absent_platform_default_is_not_refused_and_not_invented(self):
        got, reports = policy.effective(self.platform(), "", "")
        self.assertNotIn("RISK_REQUIRE_ADR", got)
        self.assertEqual(reports, [])

    def test_stricter_override_is_silent_looser_is_reported(self):
        block = "# Provenance, decision: the owner.\nRISK_REQUIRE_ADR={}\n"
        got, reports = policy.effective(self.platform("RISK_REQUIRE_ADR=off\n"), "", block.format("structural"))
        self.assertEqual((got["RISK_REQUIRE_ADR"], reports), ("structural", []))
        got, reports = policy.effective(self.platform("RISK_REQUIRE_ADR=structural\n"), "", block.format("high"))
        self.assertEqual(got["RISK_REQUIRE_ADR"], "high")
        self.assertEqual(len(reports), 1)
        self.assertIn("RISK_REQUIRE_ADR", reports[0])

    def test_override_without_provenance_is_refused(self):
        with self.assertRaises(policy.PolicyError):
            policy.effective(self.platform(), "", "RISK_REQUIRE_ADR=high\n")

    def test_malformed_platform_value_is_refused(self):
        with self.assertRaises(policy.PolicyError):
            policy.effective(self.platform("RISK_REQUIRE_ADR=maybe\n"), "", "")

    def test_the_real_platform_default_is_off_with_provenance(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(root, "policy", "merge.env")) as fh:
            lines = fh.read().splitlines()
        index = lines.index("RISK_REQUIRE_ADR=off")
        above = []
        i = index - 1
        while i >= 0 and lines[i].startswith("#"):
            above.append(lines[i])
            i -= 1
        self.assertTrue(any(line.startswith("# Provenance, decision:") for line in above), above)


class ClassifyAdrRequiredTests(unittest.TestCase):
    MIGRATION = ["3\t0\tdb/migrations/001.sql"]
    SMALL = ["1\t1\tsvc/view.py"]

    def test_key_is_false_when_off_or_absent(self):
        for pol in (BASE_POLICY, mode("off")):
            got = risk.classify(self.MIGRATION, pol)
            self.assertIs(got["adr_required"], False)
            self.assertEqual(got["level"], "high")

    def test_high_requires_on_high_level_only(self):
        self.assertIs(risk.classify(self.MIGRATION, mode("high"))["adr_required"], True)
        self.assertIs(risk.classify(self.SMALL, mode("high"))["adr_required"], False)
        self.assertIs(risk.classify(self.SMALL, mode("high"), added_paths=["package.json"])["adr_required"], False)

    def test_structural_also_requires_on_a_new_manifest_or_lockfile(self):
        self.assertIs(risk.classify(self.MIGRATION, mode("structural"))["adr_required"], True)
        self.assertIs(risk.classify(self.SMALL, mode("structural"))["adr_required"], False)
        for added in ("package.json", "svc/go.mod", "Cargo.lock", "web/pnpm-lock.yaml", "requirements.txt",
                      "Containerfile"):
            with self.subTest(added=added):
                got = risk.classify(self.SMALL + [f"5\t0\t{added}"], mode("structural"), added_paths=[added])
                self.assertIs(got["adr_required"], True)
        got = risk.classify(self.SMALL, mode("structural"), added_paths=["svc/new_view.py"])
        self.assertIs(got["adr_required"], False)

    def test_existing_keys_are_unchanged(self):
        before = risk.classify(self.MIGRATION, BASE_POLICY)
        after = risk.classify(self.MIGRATION, mode("structural"), added_paths=["x.lock"])
        for key in ("level", "decision", "signals", "size"):
            self.assertEqual(before[key], after[key])

    def test_unknown_mode_is_a_value_error(self):
        with self.assertRaises(ValueError):
            risk.classify(self.SMALL, mode("sometimes"))


class CheckRangeTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.base = self.repo.commit({"svc/view.py": "x = 1\n", "docs/adr/0001-first.md": VALID})

    def check(self, head, value, base=None):
        return adr_gov.check_range_adr(self.repo.dir, base or self.base, head, mode(value))

    def test_off_is_go_without_looking(self):
        got = self.check("not-a-commit", "off")
        self.assertTrue(got.go)
        self.assertIn("off", got.reason)

    def test_absent_key_is_off(self):
        got = adr_gov.check_range_adr(self.repo.dir, self.base, "nope", BASE_POLICY)
        self.assertTrue(got.go)

    def test_unknown_mode_is_no_go(self):
        head = self.repo.commit({"svc/view.py": "x = 2\n"})
        self.assertFalse(self.check(head, "always").go)

    def test_low_risk_needs_no_record(self):
        head = self.repo.commit({"svc/view.py": "x = 2\n"})
        for value in ("high", "structural"):
            with self.subTest(value=value):
                self.assertTrue(self.check(head, value).go)

    def test_high_risk_without_record_is_no_go_naming_the_path(self):
        head = self.repo.commit({"db/migrations/002.sql": "create table t;\n"}, message="add table")
        for value in ("high", "structural"):
            with self.subTest(value=value):
                got = self.check(head, value)
                self.assertFalse(got.go)
                self.assertTrue(any("db/migrations/002.sql" in d for d in got.details), got.details)

    def test_new_valid_record_satisfies_both_modes(self):
        head = self.repo.commit({"db/migrations/002.sql": "create table t;\n",
                                 "docs/adr/0002-a-table.md": VALID.replace("0001", "0002")})
        for value in ("high", "structural"):
            with self.subTest(value=value):
                self.assertTrue(self.check(head, value).go)

    def test_updated_record_under_root_adr_dir_satisfies(self):
        self.repo.commit({"adr/0001-x.md": VALID})
        base = self.repo.git("rev-parse", "HEAD")
        head = self.repo.commit({"platform/run.sh": "true\n", "adr/0001-x.md": VALID + "\nAmended.\n"})
        self.assertTrue(self.check(head, "structural", base=base).go)

    def test_invalid_record_is_no_go_with_its_defects(self):
        bad = VALID.replace("## Alternatives rejected\n\n- Something else, and why not.\n", "")
        head = self.repo.commit({"db/migrations/002.sql": "x\n", "docs/adr/0002-bad.md": bad})
        got = self.check(head, "high")
        self.assertFalse(got.go)
        self.assertTrue(any("docs/adr/0002-bad.md" in d and "Alternatives" in d for d in got.details), got.details)

    def test_commit_message_reference_satisfies_high_only(self):
        for message in ("Add table (ADR-0001)", "Add table, see ADR 0001"):
            with self.subTest(message=message):
                head = self.repo.commit({f"db/migrations/{len(message)}.sql": "x\n"}, message=message)
                self.assertTrue(self.check(head, "high").go)
                self.assertFalse(self.check(head, "structural").go)

    def test_structural_new_manifest_needs_a_record(self):
        head = self.repo.commit({"svc/package.json": "{}\n"})
        self.assertTrue(self.check(head, "high").go)
        got = self.check(head, "structural")
        self.assertFalse(got.go)
        self.assertTrue(any("svc/package.json" in d for d in got.details), got.details)

    def test_modified_manifest_is_not_new(self):
        self.repo.commit({"svc/package.json": "{}\n"})
        base = self.repo.git("rev-parse", "HEAD")
        head = self.repo.commit({"svc/package.json": "{\"a\": 1}\n"})
        self.assertTrue(self.check(head, "structural", base=base).go)

    def test_empty_tree_base(self):
        # From the empty tree the range adds the valid record too, and the
        # commit messages are read without a base commit.
        head = self.repo.commit({"db/migrations/002.sql": "x\n"}, message="no reference")
        self.assertTrue(self.check(head, "structural", base=EMPTY_TREE).go)
        bare = Repo(self)
        first = bare.commit({"db/migrations/001.sql": "x\n"}, message="Start (ADR-0001)")
        got = adr_gov.check_range_adr(bare.dir, EMPTY_TREE, first, mode("high"))
        self.assertFalse(got.go, got)  # the message was read; the record it names is not there
        self.assertTrue(any("ADR-0001" in d for d in got.details), got.details)
        self.assertFalse(adr_gov.check_range_adr(bare.dir, EMPTY_TREE, first, mode("structural")).go)

    def test_reference_to_a_record_that_does_not_exist_is_no_go(self):
        head = self.repo.commit({"db/migrations/002.sql": "x\n"}, message="Add table (ADR-9999)")
        got = self.check(head, "high")
        self.assertFalse(got.go)
        self.assertTrue(any("ADR-9999" in d for d in got.details), got.details)

    def test_reference_to_an_incomplete_record_is_no_go(self):
        bad = VALID.replace("## Context\n\nWhy the choice is needed.\n", "")
        self.repo.commit({"adr/0003-thin.md": bad})
        base = self.repo.git("rev-parse", "HEAD")
        head = self.repo.commit({"db/migrations/002.sql": "x\n"}, message="Add table, see ADR 0003")
        got = self.check(head, "high", base=base)
        self.assertFalse(got.go)
        self.assertTrue(any("adr/0003-thin.md" in d and "Context" in d for d in got.details), got.details)

    def test_every_referenced_record_must_resolve(self):
        head = self.repo.commit({"db/migrations/002.sql": "x\n"}, message="ADR-0001 and ADR-0042")
        got = self.check(head, "high")
        self.assertFalse(got.go)
        self.assertTrue(any("ADR-0042" in d for d in got.details), got.details)

    def test_missing_record_names_the_next_free_number(self):
        head = self.repo.commit({"db/migrations/002.sql": "x\n"})
        got = self.check(head, "structural")
        self.assertTrue(any("0002" in d for d in got.details), got.details)

    def test_unreadable_range_raises_value_error(self):
        with self.assertRaises(ValueError):
            self.check("nope", "high")


if __name__ == "__main__":
    unittest.main()
