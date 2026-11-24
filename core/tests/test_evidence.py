"""Tests for core/evidence.py, written from docs/adr/0081 §1-§6, never from
the implementation.

Each test runs the builder in a temp root whose state store stub prints
state/<id>.json (a missing file is the verb's exit 1) and whose release log
is written by hand in the shape core/releases.py record() and answer()
append. `pack` is called in-process; `build` runs as the ADR's interface
says, `python3 core/evidence.py build`, in the temp root. Signing is
core/evidence.sh's job and ci/evidence.sh proves it: nothing here signs.

The ADR fixes the entries, the sources' content and the reasons it quotes;
the key names inside a section follow the ADR's own field names (`pr.number`,
`merge.sha`, `gates.lines` for ci.lines, ...).
"""

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import evidence

REPO = Path(__file__).resolve().parents[2]
VERSION = "0.2.0"
SCHEMA = "lights-out.evidence/1"

LINES = ["GO: secrets", "GO: lint — ran in the workbench", "NO-GO: gates — tests: 1 failed"]
DOC = {
    "id": "0001", "state": "rolled-back",
    "spec": {"sha256": "5pec", "title": "Add a reminder"},
    "pr": {"number": 7, "url": "http://host/pr/7", "head_sha": "h2", "base_sha": "b1"},
    "ci": {"verdict": "GO: gates", "failed": None, "risk_level": "low", "decision": "auto-merge",
           "head_sha": "h2", "line": "GO: gates", "details": ["none"], "lines": LINES},
    "rework": [{"attempt": 1, "judge": "gates", "head_sha": "h1", "line": "NO-GO: gates — tests",
                "details": ["x"], "lines": LINES, "at": "2026-11-12T10:00:00Z"}],
    "review": {"role": "reviewer", "verdict": "approved", "reason": "fine"},
    "merge": {"sha": "m1", "head_sha": "h2"},
    "release": {"version": VERSION, "verdict": "rolled-back", "image": "img@sha256:1"},
}


def release_line(version=VERSION, verdict="rolled-back", ticket="0001", **record):
    rec = {"version": version, "stable": "0.1.0",
           "stages": [{"pct": 5, "verdict": "NO-GO", "numbers": ["slo: error_rate 0.3"]}],
           "verdict": verdict, "held_at": 25 if verdict == "held" else None,
           "image": f"repo@sha256:{version}", "sbom": None, "dast": None}
    rec.update(record)
    return {"kind": "release", "at": "2026-11-12T11:00:00Z", "ticket": ticket, "record": rec}


def answer_line(version=VERSION, said="GO", **extra):
    line = {"kind": "answer", "at": "2026-11-12T12:00:00Z", "version": version, "answer": said,
            "by": "nicola", "via": "console"}
    line.update(extra)
    return line


def leaves(node):
    """Every entry under NODE, in document order."""
    if isinstance(node, dict):
        if "source" in node:
            yield node
            return
        for child in node.values():
            yield from leaves(child)
    elif isinstance(node, list):
        for child in node:
            yield from leaves(child)


def sections(pack):
    return [pack[k] for k in ("release", "stages", "artefacts", "answer", "prs") if k in pack]


def val(entry):
    return entry["value"]


def plain(field):
    """A top-level field, whether the pack wraps it in an entry or not."""
    return field["value"] if isinstance(field, dict) and "value" in field else field


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        (self.root / "adapters" / "stub-state" / "get.sh").write_text(
            '[ "$1" = tickets ] || exit 1\nf=state/"$2".json\n[ -f "$f" ] || exit 1\ncat "$f"\n')
        (self.root / "state").mkdir()
        (self.root / ".crew").mkdir()
        self.log = ".crew/releases.jsonl"
        self.out = ".crew/evidence"
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def env(self, **extra):
        env = {"VERSION": VERSION, "RELEASES_LOG": self.log, "STATE_STORE": "stub-state",
               "EVIDENCE_DIR": self.out}
        env.update(extra)
        return {k: v for k, v in env.items() if v is not None}

    def write_log(self, *lines):
        (self.root / self.log).write_text(
            "".join((l if isinstance(l, str) else json.dumps(l)) + "\n" for l in lines))

    def write_doc(self, doc, ticket="0001"):
        text = doc if isinstance(doc, str) else json.dumps(doc)
        (self.root / "state" / f"{ticket}.json").write_text(text)

    def pack(self, **extra):
        return evidence.pack(self.env(**extra))

    def build(self, **extra):
        env = {k: v for k, v in os.environ.items() if k not in self.env()}
        env.update(PYTHONPATH=str(REPO), **self.env(**extra))
        proc = subprocess.run([sys.executable, str(REPO / "core" / "evidence.py"), "build"],
                              cwd=self.root, env=env, capture_output=True, text=True)
        return proc.returncode, proc.stdout, proc.stderr

    def one_pr(self, pack):
        prs = pack["prs"]
        self.assertIsInstance(prs, list, prs)
        self.assertEqual(len(prs), 1)
        return prs[0]

    def assertMissingPrs(self, pack, *words):
        """`prs` is one missing entry (alone, or the only item of the list)."""
        prs = pack["prs"]
        if isinstance(prs, list):
            self.assertEqual(len(prs), 1, prs)
            prs = prs[0]
        self.assertEqual(set(prs), {"source", "missing"})
        for w in words:
            self.assertIn(w.lower(), prs["missing"].lower())


class Shape(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.write_log(release_line(), release_line("0.3.0", "held", ticket=None), answer_line("0.3.0"))
        self.write_doc(DOC)

    def test_schema_and_version(self):
        pack = self.pack()
        self.assertEqual(pack["schema"], SCHEMA)
        self.assertEqual(plain(pack["version"]), VERSION)
        self.assertIn("built_at", pack)

    def test_every_leaf_is_one_of_the_two_shapes(self):
        found = list(leaves(sections(self.pack())))
        self.assertGreater(len(found), 10)
        for e in found:
            self.assertIsInstance(e["source"], str)
            self.assertTrue(e["source"])
            self.assertIn(set(e), ({"source", "value"}, {"source", "missing"}), e)
            if "missing" in e:
                self.assertIsInstance(e["missing"], str)
                self.assertTrue(e["missing"])

    def test_top_level_missing_lists_every_missing_entry_and_nothing_else(self):
        pack = self.pack()
        gaps = [e for e in leaves(sections(pack)) if "missing" in e]
        self.assertGreater(len(gaps), 0)
        self.assertEqual(len(pack["missing"]), len(gaps))
        self.assertEqual(len(set(pack["missing"])), len(pack["missing"]))
        self.assertTrue(all(isinstance(p, str) for p in pack["missing"]))
        # Rolled back, null sbom and dast, no answer, no stage times.
        joined = "\n".join(pack["missing"])
        for word in ("sbom", "dast", "answer", "started_at", "judged_at"):
            self.assertIn(word, joined)
        self.assertNotIn("image", joined)

    def test_a_pack_with_no_gaps_lists_none(self):
        self.write_log(
            release_line("0.3.0", "held", sbom="sha256:sbom", dast="staging: 0 high",
                         stages=[{"pct": 5, "verdict": "GO", "numbers": [], "started_at": "a", "judged_at": "b"}]),
            answer_line("0.3.0", reason="r", brief_sha256="s", brief_author="me"))
        # ADR 0081 §4: the review is evidence only where the gates asked for one.
        self.write_doc(dict(DOC, release=dict(DOC["release"], version="0.3.0"),
                            ci=dict(DOC["ci"], decision="review")))
        pack = self.pack(VERSION="0.3.0")
        self.assertEqual(pack["missing"], [])
        self.assertFalse([e for e in leaves(sections(pack)) if "missing" in e])


class WhichRelease(EvidenceCase):
    def test_last_release_line_of_the_version_is_packed(self):
        self.write_log(release_line(image="repo@sha256:old"), release_line("0.9.0"),
                       release_line(image="repo@sha256:new"))
        self.write_doc(DOC)
        image = self.pack()["artefacts"]["image"]
        self.assertEqual(val(image), "repo@sha256:new")
        self.assertIn(f"{self.log}:3", image["source"])

    def test_earlier_releases_are_listed_by_line_number(self):
        self.write_log(release_line(), release_line("0.9.0"), release_line(), release_line())
        self.write_doc(DOC)
        self.assertEqual(plain(self.pack()["earlier_releases"]), [1, 3])

    def test_no_earlier_release_is_an_empty_list(self):
        self.write_log(release_line())
        self.write_doc(DOC)
        self.assertEqual(plain(self.pack()["earlier_releases"]), [])

    def test_the_answer_is_the_first_one_after_the_release_and_before_the_next(self):
        self.write_log(
            answer_line(said="NO-GO", by="before"),          # before the release line: not taken
            release_line("0.3.0", "held", ticket=None),
            answer_line("0.4.0", by="other"),                # another version: not taken
            answer_line("0.3.0", said="GO", by="first"),
            answer_line("0.3.0", said="NO-GO", by="second"))
        pack = self.pack(VERSION="0.3.0")
        self.assertEqual(val(pack["answer"]["by"]), "first")
        self.assertEqual(val(pack["answer"]["answer"]), "GO")

    def test_an_answer_before_the_release_line_is_not_taken(self):
        self.write_log(answer_line("0.3.0", by="before"), release_line("0.3.0", "held", ticket=None))
        self.assertIn("missing", self.pack(VERSION="0.3.0")["answer"])

    def test_an_answer_for_another_version_only_is_no_answer(self):
        self.write_log(release_line("0.3.0", "held", ticket=None), answer_line("0.4.0"))
        self.assertIn("missing", self.pack(VERSION="0.3.0")["answer"])

    def test_an_answer_after_the_last_release_line_belongs_to_it(self):
        self.write_log(release_line("0.3.0", "held", ticket=None),
                       release_line("0.3.0", "held", ticket=None), answer_line("0.3.0", by="late"))
        pack = self.pack(VERSION="0.3.0")
        self.assertEqual(val(pack["answer"]["by"]), "late")
        self.assertEqual(plain(pack["earlier_releases"]), [1])

    def test_the_answer_between_two_release_lines_is_not_the_last_releases(self):
        self.write_log(release_line("0.3.0", "held", ticket=None), answer_line("0.3.0", by="mid"),
                       release_line("0.3.0", "held", ticket=None))
        pack = self.pack(VERSION="0.3.0")
        self.assertIn("missing", pack["answer"])
        self.assertEqual(plain(pack["earlier_releases"]), [1])

    def test_line_numbers_count_every_line_of_the_file(self):
        self.write_log("", release_line())
        self.write_doc(DOC)
        self.assertIn(f"{self.log}:2", self.pack()["artefacts"]["image"]["source"])

    def test_a_line_that_is_not_json_is_refused_and_named(self):
        # ADR 0081 §2: the pack never skips a line it cannot read.
        self.write_log(release_line(), "not json at all")
        self.write_doc(DOC)
        with self.assertRaises(evidence.Refused) as refused:
            self.pack()
        self.assertIn("line 2", str(refused.exception))


class Refusals(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.write_log(release_line())
        self.write_doc(DOC)

    def test_unset_version(self):
        with self.assertRaises(evidence.Refused):
            self.pack(VERSION=None)

    def test_malformed_versions(self):
        for bad in ("../x", ".hidden", "a/b", "a b", "", "x\n"):
            with self.subTest(version=bad), self.assertRaises(evidence.Refused):
                self.pack(VERSION=bad)

    def test_version_with_no_release_line(self):
        self.write_log(release_line("0.9.0"), answer_line("0.5.0"))
        with self.assertRaises(evidence.Refused):
            self.pack(VERSION="0.5.0")

    def test_refusal_through_build_is_one_line_on_stdout_exit_1_and_writes_nothing(self):
        for extra in ({"VERSION": None}, {"VERSION": "../x"}, {"VERSION": "9.9.9"}):
            with self.subTest(**extra):
                code, out, _ = self.build(**extra)
                self.assertEqual(code, 1)
                self.assertEqual(len(out.splitlines()), 1, out)
                self.assertFalse((self.root / self.out).exists()
                                 and any((self.root / self.out).iterdir()))
        self.assertFalse((self.root / ".crew" / "x.json").exists())

    def test_a_version_with_dots_dashes_and_underscores_is_accepted(self):
        self.write_log(release_line("1.2.3-rc.1_b"))
        self.write_doc(dict(DOC, release=dict(DOC["release"], version="1.2.3-rc.1_b")))
        self.assertEqual(self.pack(VERSION="1.2.3-rc.1_b")["schema"], SCHEMA)


class Stages(EvidenceCase):
    def test_pct_verdict_numbers_are_values_and_the_times_are_missing(self):
        self.write_log(release_line(stages=[
            {"pct": 5, "verdict": "GO", "numbers": ["a", "b"]},
            {"pct": 25, "verdict": "NO-GO", "numbers": []}]))
        self.write_doc(DOC)
        stages = self.pack()["stages"]
        self.assertEqual(len(stages), 2)
        self.assertEqual(val(stages[0]["pct"]), 5)
        self.assertEqual(val(stages[0]["verdict"]), "GO")
        self.assertEqual(val(stages[0]["numbers"]), ["a", "b"])
        self.assertEqual(val(stages[1]["pct"]), 25)
        self.assertEqual(val(stages[1]["numbers"]), [])
        for s in stages:
            for field in ("started_at", "judged_at"):
                self.assertEqual(set(s[field]), {"source", "missing"})
                self.assertIn(f"the record of this stage carries no {field}", s[field]["missing"])

    def test_the_times_are_values_when_the_record_carries_them(self):
        self.write_log(release_line(stages=[{"pct": 5, "verdict": "GO", "numbers": [],
                                             "started_at": "2026-11-12T10:00:00Z",
                                             "judged_at": "2026-11-12T10:05:00Z"}]))
        self.write_doc(DOC)
        s = self.pack()["stages"][0]
        self.assertEqual(val(s["started_at"]), "2026-11-12T10:00:00Z")
        self.assertEqual(val(s["judged_at"]), "2026-11-12T10:05:00Z")
        self.assertIn(f"{self.log}:1", s["started_at"]["source"])

    def test_an_extra_stage_field_is_packed_as_found(self):
        self.write_log(release_line(stages=[{"pct": 5, "verdict": "GO", "numbers": [], "p95_ms": 42}]))
        self.write_doc(DOC)
        self.assertEqual(val(self.pack()["stages"][0]["p95_ms"]), 42)

    def test_no_stages_is_an_empty_list(self):
        self.write_log(release_line(stages=[]))
        self.write_doc(DOC)
        self.assertEqual(self.pack()["stages"], [])


class Artefacts(EvidenceCase):
    def test_null_sbom_and_dast_are_missing_with_a_reason_each(self):
        self.write_log(release_line())
        self.write_doc(DOC)
        a = self.pack()["artefacts"]
        self.assertEqual(val(a["image"]), f"repo@sha256:{VERSION}")
        for field in ("sbom", "dast"):
            self.assertEqual(set(a[field]), {"source", "missing"})
        self.assertIn("registry", a["sbom"]["missing"])
        self.assertIn("off", a["dast"]["missing"].lower())

    def test_sbom_and_dast_are_values_when_present(self):
        self.write_log(release_line(sbom="sha256:abc", dast="staging: 0 raised"))
        self.write_doc(DOC)
        a = self.pack()["artefacts"]
        self.assertEqual(val(a["sbom"]), "sha256:abc")
        self.assertEqual(val(a["dast"]), "staging: 0 raised")
        self.assertIn(".record.sbom", a["sbom"]["source"])


class Answer(EvidenceCase):
    def test_answer_fields_are_values_and_the_stream_a_ones_are_missing(self):
        self.write_log(release_line("0.3.0", "held", ticket=None), answer_line("0.3.0"))
        a = self.pack(VERSION="0.3.0")["answer"]
        self.assertEqual(val(a["answer"]), "GO")
        self.assertEqual(val(a["by"]), "nicola")
        self.assertEqual(val(a["via"]), "console")
        self.assertEqual(val(a["at"]), "2026-11-12T12:00:00Z")
        for field in ("reason", "brief_sha256", "brief_author"):
            self.assertEqual(set(a[field]), {"source", "missing"})
        self.assertIn(f"{self.log}:2", a["answer"]["source"])

    def test_the_stream_a_fields_are_values_when_the_line_carries_them(self):
        self.write_log(release_line("0.3.0", "held", ticket=None),
                       answer_line("0.3.0", reason="looks fine", brief_sha256="abc", brief_author="release"))
        a = self.pack(VERSION="0.3.0")["answer"]
        self.assertEqual(val(a["reason"]), "looks fine")
        self.assertEqual(val(a["brief_sha256"]), "abc")
        self.assertEqual(val(a["brief_author"]), "release")

    def test_a_no_go_answer_is_packed_as_said(self):
        self.write_log(release_line("0.3.0", "held", ticket=None), answer_line("0.3.0", said="NO-GO"))
        self.assertEqual(val(self.pack(VERSION="0.3.0")["answer"]["answer"]), "NO-GO")

    def test_no_answer_on_a_rolled_back_release_is_one_missing_entry_saying_none_was_asked(self):
        self.write_log(release_line())
        self.write_doc(DOC)
        pack = self.pack()
        self.assertEqual(set(pack["answer"]), {"source", "missing"})
        self.assertIn("rolled back", pack["answer"]["missing"])
        self.assertEqual(len([p for p in pack["missing"] if "answer" in p]), 1)

    def test_no_answer_on_a_held_release_says_it_still_waits(self):
        self.write_log(release_line("0.3.0", "held", ticket=None))
        a = self.pack(VERSION="0.3.0")["answer"]
        self.assertEqual(set(a), {"source", "missing"})
        self.assertIn("waits", a["missing"])
        self.assertNotIn("rolled back", a["missing"])

    def test_the_two_no_answer_reasons_differ(self):
        self.write_log(release_line(), release_line("0.3.0", "held", ticket=None))
        self.write_doc(DOC)
        self.assertNotEqual(self.pack()["answer"]["missing"], self.pack(VERSION="0.3.0")["answer"]["missing"])


class Prs(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.write_log(release_line())
        self.write_doc(DOC)

    def test_one_pr_with_its_fields(self):
        pr = self.one_pr(self.pack())
        for field, want in (("number", 7), ("url", "http://host/pr/7"), ("head_sha", "h2"), ("base_sha", "b1")):
            self.assertEqual(val(pr["pr"][field]), want)
        self.assertEqual(val(pr["merge"]["sha"]), "m1")
        self.assertEqual(val(pr["spec"]["sha256"]), "5pec")
        self.assertEqual(val(pr["spec"]["title"]), "Add a reminder")

    def test_the_gates_of_the_last_attempt(self):
        g = self.one_pr(self.pack())["gates"]
        self.assertEqual(val(g["verdict"]), "GO: gates")
        self.assertEqual(val(g["line"]), "GO: gates")
        self.assertEqual(val(g["lines"]), LINES)
        self.assertEqual(val(g["details"]), ["none"])
        self.assertEqual(val(g["risk_level"]), "low")
        self.assertEqual(val(g["decision"]), "auto-merge")
        self.assertEqual(val(g["head_sha"]), "h2")

    def test_rework_is_kept_as_the_document_has_it(self):
        self.assertEqual(val(self.one_pr(self.pack())["rework"]), DOC["rework"])

    def test_no_rework_is_a_value_not_a_gap(self):
        self.write_doc(dict(DOC, rework=[]))
        pack = self.pack()
        self.assertEqual(val(self.one_pr(pack)["rework"]), [])
        self.assertFalse([p for p in pack["missing"] if "rework" in p])

    def test_review_is_a_value_when_the_gates_asked_for_one(self):
        self.write_doc(dict(DOC, ci=dict(DOC["ci"], decision="review")))
        r = self.one_pr(self.pack())["review"]
        self.assertEqual(val(r["role"]), "reviewer")
        self.assertEqual(val(r["verdict"]), "approved")
        self.assertEqual(val(r["reason"]), "fine")

    def test_review_is_missing_when_the_gates_decided_otherwise_and_the_reason_names_it(self):
        r = self.one_pr(self.pack())["review"]
        self.assertEqual(set(r), {"source", "missing"})
        self.assertIn("auto-merge", r["missing"])
        self.write_doc(dict(DOC, ci=dict(DOC["ci"], decision="human")))
        self.assertIn("human", self.one_pr(self.pack())["review"]["missing"])

    def test_no_ticket_on_the_log_line_makes_prs_one_missing_entry(self):
        self.write_log(release_line(ticket=None))
        pack = self.pack()
        self.assertMissingPrs(pack, "ticket")
        self.assertTrue(any("prs" in p for p in pack["missing"]))

    def test_an_unreadable_document_makes_prs_one_missing_entry(self):
        (self.root / "state" / "0001.json").unlink()
        self.assertMissingPrs(self.pack())

    def test_a_document_that_is_not_json_makes_prs_one_missing_entry(self):
        self.write_doc("{not json")
        self.assertMissingPrs(self.pack())

    def test_a_document_of_another_release_is_not_passed_off_as_this_ones(self):
        self.write_doc(dict(DOC, release=dict(DOC["release"], version="0.9.0")))
        self.assertMissingPrs(self.pack())

    def test_a_document_with_no_release_is_not_this_ones_either(self):
        doc = copy.deepcopy(DOC)
        del doc["release"]
        self.write_doc(doc)
        self.assertMissingPrs(self.pack())

    def test_a_document_missing_one_field_gives_a_missing_entry_for_that_field_only(self):
        doc = copy.deepcopy(DOC)
        del doc["spec"]["title"]
        self.write_doc(doc)
        pr = self.one_pr(self.pack())
        self.assertEqual(set(pr["spec"]["title"]), {"source", "missing"})
        self.assertEqual(val(pr["spec"]["sha256"]), "5pec")


class Sources(EvidenceCase):
    def test_release_entries_name_the_log_and_the_line(self):
        self.write_log(release_line("0.9.0"), release_line())
        self.write_doc(DOC)
        pack = self.pack()
        for e in (pack["artefacts"]["image"], pack["stages"][0]["pct"], pack["stages"][0]["started_at"],
                  pack["artefacts"]["sbom"]):
            self.assertIn(f"{self.log}:2", e["source"])
        self.assertIn("image", pack["artefacts"]["image"]["source"])

    def test_answer_entries_name_the_answer_line(self):
        self.write_log(release_line("0.3.0", "held", ticket=None), answer_line("0.4.0"), answer_line("0.3.0"))
        a = self.pack(VERSION="0.3.0")["answer"]
        self.assertIn(f"{self.log}:3", a["by"]["source"])

    def test_pr_entries_name_the_state_store_and_the_document(self):
        self.write_log(release_line())
        self.write_doc(DOC)
        pr = self.one_pr(self.pack())
        for e in (pr["pr"]["number"], pr["gates"]["lines"], pr["merge"]["sha"], pr["spec"]["sha256"],
                  pr["review"], pr["rework"]):
            self.assertIn("stub-state", e["source"])
            self.assertIn("tickets/0001", e["source"])
        self.assertIn(".ci.lines", pr["gates"]["lines"]["source"])


class Build(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.write_log(release_line(), release_line("0.3.0", "held", ticket=None), answer_line("0.3.0"))
        self.write_doc(DOC)

    def test_writes_the_pack_and_prints_path_and_count(self):
        code, out, err = self.build()
        self.assertEqual(code, 0, err)
        self.assertEqual(out.count("\n"), 1)
        path, count = out.rstrip("\n").split("\t")
        self.assertEqual(Path(path).name, f"{VERSION}.json")
        self.assertEqual((self.root / path).resolve(), (self.root / self.out / f"{VERSION}.json").resolve())
        written = json.loads((self.root / path).read_text())
        self.assertEqual(int(count), len(written["missing"]))
        self.assertGreater(int(count), 0)
        self.assertEqual(written["schema"], SCHEMA)

    def test_the_file_is_the_pack_pack_returns(self):
        _, out, _ = self.build()
        written = json.loads((self.root / out.split("\t")[0]).read_text())
        built = self.pack()
        written.pop("built_at", None)
        built.pop("built_at", None)
        self.assertEqual(written, built)

    def test_each_missing_path_is_on_stderr_and_not_on_stdout(self):
        _, out, err = self.build()
        written = json.loads((self.root / out.split("\t")[0]).read_text())
        for path in written["missing"]:
            self.assertIn(path, err)
            self.assertNotIn(path, out)

    def test_the_directory_is_created_and_the_pack_is_overwritten_by_the_next_build(self):
        self.assertFalse((self.root / self.out).exists())
        self.build()
        target = self.root / self.out / f"{VERSION}.json"
        target.write_text("stale")
        self.assertEqual(self.build()[0], 0)
        self.assertEqual(json.loads(target.read_text())["schema"], SCHEMA)

    def test_a_count_of_zero_when_nothing_is_missing(self):
        self.write_log(
            release_line("0.3.0", "held", sbom="s", dast="d",
                         stages=[{"pct": 5, "verdict": "GO", "numbers": [], "started_at": "a", "judged_at": "b"}]),
            answer_line("0.3.0", reason="r", brief_sha256="s", brief_author="a"))
        self.write_doc(dict(DOC, release=dict(DOC["release"], version="0.3.0"),
                            ci=dict(DOC["ci"], decision="review")))
        code, out, _ = self.build(VERSION="0.3.0")
        self.assertEqual(code, 0)
        self.assertEqual(out.rstrip("\n").split("\t")[1], "0")

    def test_the_defaults_are_under_crew(self):
        code, _, err = self.build(EVIDENCE_DIR=None, RELEASES_LOG=None)
        self.assertEqual(code, 0, err)
        self.assertTrue((self.root / ".crew" / "evidence" / f"{VERSION}.json").is_file())

    def test_it_writes_nothing_to_the_log_or_the_state_store(self):
        def snapshot():
            return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(self.root.rglob("*")) if p.is_file() and self.out not in str(p)}
        before = snapshot()
        self.assertEqual(self.build()[0], 0)
        self.pack()
        self.assertEqual(snapshot(), before)


class ProvenanceArtefact(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.write_log(release_line(), release_line("0.3.0", "held", ticket=None), answer_line("0.3.0"))
        self.write_doc(DOC)
        self.prov_dir = self.root / ".crew" / "provenance"

    def _write_provenance(self, subject_id, signed=True, directory=None):
        target_dir = directory or self.prov_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        stmt = {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": f"repo@sha256:{'a' * 64}", "digest": {"sha256": "a" * 64}}],
            "predicateType": "https://lights-out.dev/provenance/v1",
            "predicate": {"ticket": {"id": "0001"}, "missing": []},
        }
        att = target_dir / f"{subject_id}.intoto.json"
        att.write_text(json.dumps(stmt, indent=2) + "\n", encoding="utf-8")
        if signed:
            bundle = target_dir / f"{subject_id}.intoto.json.bundle"
            bundle.write_text(json.dumps({"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json"}) + "\n",
                              encoding="utf-8")
        return att

    def test_absent_provenance_keeps_artefacts_and_missing_count_unchanged(self):
        pack = self.pack()
        self.assertEqual(set(pack["artefacts"]), {"image", "sbom", "dast"})
        self.assertFalse(any("provenance" in p for p in pack["missing"]))

    def test_unsigned_statement_without_bundle_is_not_surfaced(self):
        self._write_provenance(VERSION, signed=False)
        pack = self.pack()
        self.assertNotIn("provenance", pack["artefacts"])

    def test_signed_version_provenance_is_surfaced_as_found_entry(self):
        att = self._write_provenance(VERSION, signed=True)
        pack = self.pack()
        self.assertIn("provenance", pack["artefacts"])
        prov = pack["artefacts"]["provenance"]
        self.assertEqual(set(prov), {"source", "value"})
        self.assertEqual(prov["value"]["sha256"], hashlib.sha256(att.read_bytes()).hexdigest())
        self.assertEqual(prov["value"]["predicate_type"], "https://lights-out.dev/provenance/v1")
        self.assertTrue(prov["value"]["statement"].endswith(f"{VERSION}.intoto.json"))
        self.assertTrue(prov["value"]["bundle"].endswith(f"{VERSION}.intoto.json.bundle"))
        # Leaf shape invariant holds across the entire pack.
        for e in leaves(sections(pack)):
            self.assertIn(set(e), ({"source", "value"}, {"source", "missing"}))

    def test_signed_ticket_provenance_fallback_and_custom_provenance_dir(self):
        custom = self.root / "custom-prov"
        att = self._write_provenance("0001", signed=True, directory=custom)
        pack = self.pack(PROVENANCE_DIR="custom-prov")
        prov = pack["artefacts"]["provenance"]
        self.assertEqual(set(prov), {"source", "value"})
        self.assertEqual(prov["value"]["sha256"], hashlib.sha256(att.read_bytes()).hexdigest())
        self.assertTrue(prov["value"]["statement"].endswith("0001.intoto.json"))


if __name__ == "__main__":
    unittest.main()
