"""Tests for core/incident.py, written from docs/adr/0044 §1 and §2, never
from the implementation.

Each test runs the builder in-process in a temp root with a release record,
an optional log excerpt and a StateStore stub that prints state/doc.json
(a missing file is get.sh's exit 1). ci/postmortem.sh part B proves the
same through core/incident.sh against the spec's hand-written document.
"""

import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import incident

RECORD = {
    "version": "0.2.0-canary", "stable": "0.1.0", "image": "img@sha256:1",
    "verdict": "rolled-back", "held_at": None,
    "stages": [
        {"pct": 5, "verdict": "GO: slo", "numbers": ["slo: error_rate 0.00"]},
        {"pct": 25, "verdict": "NO-GO: slo — error_rate 0.31 > 0.05", "numbers": ["slo: error_rate 0.31"]},
    ],
}
WINDOW = {"NAME": "pm-demo", "FROM": "2026-11-04T10:00:00Z", "TO": "2026-11-04T10:05:00Z"}
TOWER_DOC = {"id": "0001", "state": "rolled-back", "pr": {"number": 1}, "merge": {"sha": "m1"},
             "release": {"version": "0.2.0-canary"}}


class IncidentCase(unittest.TestCase):
    def setUp(self):
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        (self.root / "adapters" / "stub-state" / "get.sh").write_text(
            '[ "$1 $2" = "tickets 0001" ] || exit 1\n[ -f state/doc.json ] || exit 1\ncat state/doc.json\n')
        (self.root / "state").mkdir()
        os.chdir(self.root)
        self.record(RECORD)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def record(self, value):
        Path("release.json").write_text(value if isinstance(value, str) else json.dumps(value))

    def run_incident(self, **env):
        env = {**WINDOW, "RELEASE_RECORD": "release.json", "INCIDENT_OUT": "out", **env}
        env = {k: v for k, v in env.items() if v is not None}
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = incident.main(env)
        return code, out.getvalue().strip(), err.getvalue()

    def written(self):
        return sorted(str(p) for p in Path("out").rglob("*")) if Path("out").exists() else []

    def assertRefused(self, pattern, **env):
        code, out, _ = self.run_incident(**env)
        self.assertEqual(code, 1, out)
        self.assertIn(pattern, out)
        self.assertEqual(self.written(), [], "a NO-GO left a document behind")


class TheDocument(IncidentCase):
    def test_the_shape_of_section_1(self):
        code, out, err = self.run_incident()
        self.assertEqual(code, 0)
        self.assertEqual(out, os.path.join("out", "pm-demo.json"))
        doc = json.loads(Path(out).read_text())
        self.assertEqual(list(doc), ["kind", "name", "window", "release", "alerts", "logs", "chain"])
        self.assertEqual(doc["kind"], "incident")
        self.assertEqual(doc["name"], "pm-demo")
        self.assertEqual(doc["window"], {"from": "2026-11-04T10:00:00Z", "to": "2026-11-04T10:05:00Z"})
        self.assertEqual(doc["release"], RECORD)
        self.assertEqual(doc["logs"], [])
        self.assertIsNone(doc["chain"])
        self.assertIn("[incident] pm-demo: 1 alert(s), 0 log line(s), chain null", err)

    def test_one_alert_per_stage_that_is_not_go(self):
        _, out, _ = self.run_incident()
        alerts = json.loads(Path(out).read_text())["alerts"]
        self.assertEqual(alerts, [{"headline": "NO-GO: slo — error_rate 0.31 > 0.05",
                                   "version": "0.2.0-canary", "stable": "0.1.0",
                                   "stage": RECORD["stages"][1]}])

    def test_a_record_with_no_stages_has_no_alerts(self):
        record = dict(RECORD, verdict="held")
        record.pop("stages")
        self.record(record)
        _, out, _ = self.run_incident()
        self.assertEqual(json.loads(Path(out).read_text())["alerts"], [])

    def test_held_is_a_verdict(self):
        self.record(dict(RECORD, verdict="held"))
        self.assertEqual(self.run_incident()[0], 0)

    def test_log_lines_are_copied_verbatim(self):
        Path("logs.txt").write_text("first\n\n  third — “quoted”\r\nlast")
        _, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(json.loads(Path(out).read_text())["logs"],
                         ["first", "", "  third — “quoted”\r", "last"])

    def test_a_final_newline_adds_no_line(self):
        Path("logs.txt").write_text("one\ntwo\n")
        _, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(json.loads(Path(out).read_text())["logs"], ["one", "two"])
        Path("logs.txt").write_text("")
        _, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(json.loads(Path(out).read_text())["logs"], [])

    def test_the_same_inputs_give_the_same_bytes(self):
        Path("logs.txt").write_text("a\nb\n")
        _, out, _ = self.run_incident(LOGS="logs.txt")
        first = Path(out).read_bytes()
        _, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(Path(out).read_bytes(), first)
        self.assertTrue(first.endswith(b"}\n"))
        self.assertIn("—".encode(), first)

    def test_ticket_anchors_the_chain_through_the_store(self):
        Path("state/doc.json").write_text(json.dumps(TOWER_DOC))
        code, out, err = self.run_incident(TICKET="0001", STATE_STORE="stub-state")
        self.assertEqual(code, 0, out)
        self.assertEqual(json.loads(Path(out).read_text())["chain"],
                         {"ticket": "0001", "pr": 1, "merge": "m1"})
        self.assertIn('chain {"ticket":"0001","pr":1,"merge":"m1"}', err)

    def test_the_default_output_directory(self):
        code, out, _ = self.run_incident(INCIDENT_OUT=None)
        self.assertEqual((code, out), (0, os.path.join(".crew", "incidents", "pm-demo.json")))


class Refusals(IncidentCase):
    def test_name_from_and_to_are_required(self):
        for var in WINDOW:
            with self.subTest(var=var):
                self.assertRefused(f"{var} not set", **{var: None})

    def test_name_is_a_slug(self):
        for name in ("-lead", "Upper", "with space", "dot.ted", "../x", "x/y", "under_score"):
            with self.subTest(name=name):
                self.assertRefused("is not a slug", NAME=name)

    def test_times_are_utc_stamps(self):
        for stamp in ("2026-11-04 10:00:00Z", "2026-11-04T10:00:00", "2026-11-04T10:00:00+00:00",
                      "2026-11-4T10:00:00Z", "x2026-11-04T10:00:00Z"):
            with self.subTest(stamp=stamp):
                self.assertRefused("is not a UTC time", FROM=stamp)

    def test_the_window_does_not_end_before_it_starts(self):
        self.assertRefused("the window ends (2026-11-04T09:00:00Z) before it starts",
                           TO="2026-11-04T09:00:00Z")
        self.assertEqual(self.run_incident(TO=WINDOW["FROM"])[0], 0)

    def test_no_record(self):
        self.assertRefused("no release record at nowhere.json", RELEASE_RECORD="nowhere.json")

    def test_a_record_that_is_not_json(self):
        self.record("{oops")
        self.assertRefused("is not JSON")

    def test_in_progress_is_not_a_verdict(self):
        self.record(dict(RECORD, verdict="in-progress"))
        self.assertRefused("release 0.2.0-canary is in-progress")

    def test_any_other_verdict_is_refused(self):
        for verdict in ("promoted", None, ""):
            with self.subTest(verdict=verdict):
                self.record(dict(RECORD, verdict=verdict))
                self.assertRefused("has no verdict of held or rolled-back")

    def test_a_record_without_version(self):
        record = copy.deepcopy(RECORD)
        record.pop("version")
        self.record(record)
        self.assertRefused("names no version")

    def test_a_missing_log_excerpt(self):
        self.assertRefused("no log excerpt at gone.txt", LOGS="gone.txt")

    def test_ticket_without_a_document(self):
        self.assertRefused("no document for ticket 0001 in stub-state", TICKET="0001",
                           STATE_STORE="stub-state")

    def test_ticket_needs_a_store(self):
        self.assertRefused("STATE_STORE not set", TICKET="0001")

    def test_ticket_about_another_release(self):
        Path("state/doc.json").write_text(json.dumps(dict(TOWER_DOC, release={"version": "0.3.0"})))
        self.assertRefused('records release "0.3.0", the release record is "0.2.0-canary"',
                           TICKET="0001", STATE_STORE="stub-state")

    def test_ticket_with_nothing_to_anchor_to(self):
        for missing in ("pr", "merge"):
            with self.subTest(missing=missing):
                doc = copy.deepcopy(TOWER_DOC)
                doc.pop(missing)
                Path("state/doc.json").write_text(json.dumps(doc))
                self.assertRefused("has no PR or merge to anchor to", TICKET="0001", STATE_STORE="stub-state")


class TraceExemplarsAndAnchors(IncidentCase):
    def test_traces_extracted_from_logs_and_alerts_only_when_present(self):
        t_log = "4bf92f3577b34da6a3ce929d0e0e4736"
        s_log = "00f067aa0ba902b7"
        t_alert = "aaaa1111bbbb2222cccc3333dddd4444"
        rec = copy.deepcopy(RECORD)
        rec["stages"][1]["numbers"].append(f"trace_id={t_alert}")
        self.record(rec)
        Path("logs.txt").write_text(f"2026-11-04T10:01:00Z traceparent=00-{t_log}-{s_log}-01 err=500\n")

        code, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(code, 0, out)
        doc = json.loads(Path(out).read_text())
        self.assertEqual(
            doc["traces"],
            [
                {"trace_id": t_alert, "span_id": None},
                {"trace_id": t_log, "span_id": s_log},
            ],
        )

    def test_postmortem_accepts_real_trace_anchors_and_refuses_fabricated_ones(self):
        from core import postmortem

        t1 = "4bf92f3577b34da6a3ce929d0e0e4736"
        s1 = "00f067aa0ba902b7"
        t2 = "abcdef0123456789abcdef0123456789"
        Path("logs.txt").write_text(f"err traceparent=00-{t1}-{s1}-01\nwarn trace_id={t2}\n")
        code, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(code, 0)
        doc = postmortem.validate(out)

        ev_idx = postmortem.anchor_evidence("trace:1", doc)
        self.assertIn(t1, "\n".join(ev_idx))
        self.assertIn(s1, "\n".join(ev_idx))
        ev_hex = postmortem.anchor_evidence(f"trace:{t2}", doc)
        self.assertIn(t2, "\n".join(ev_hex))

        for bad in (
            "trace:0",
            "trace:3",
            "trace:ffffffffffffffffffffffffffffffff",
            "trace:not-a-trace",
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(postmortem.Fail):
                    postmortem.anchor_evidence(bad, doc)

        # Document without traces refuses any trace anchor cleanly.
        Path("logs.txt").write_text("plain log line\n")
        code2, out2, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(code2, 0)
        doc_no_traces = postmortem.validate(out2)
        self.assertNotIn("traces", doc_no_traces)
        with self.assertRaises(postmortem.Fail):
            postmortem.anchor_evidence("trace:1", doc_no_traces)

    def test_postmortem_check_validates_trace_timeline_anchor(self):
        from core import postmortem

        t1 = "4bf92f3577b34da6a3ce929d0e0e4736"
        s1 = "00f067aa0ba902b7"
        Path("logs.txt").write_text(f"err traceparent=00-{t1}-{s1}-01\n")
        code, out, _ = self.run_incident(LOGS="logs.txt")
        self.assertEqual(code, 0)
        calls = [
            {
                "name": "file_postmortem",
                "args": {
                    "title": "Traced regression",
                    "timeline": [
                        {"event": "Canary rolled back", "anchor": "release:0.2.0-canary"},
                        {"event": "Failing span traced", "anchor": f"trace:{t1}"},
                    ],
                    "causes": [{"cause": "5xx burst", "evidence": f"trace:{t1}"}],
                    "actions": [{"title": "Fix bug", "problem": "5xx", "acceptance": "Given X When Y Then Z"}],
                },
            }
        ]
        Path("calls.json").write_text(json.dumps(calls))
        checked = postmortem.check(out, "calls.json")
        self.assertEqual(checked["timeline"][1]["anchor"], f"trace:{t1}")
        self.assertIn(t1, checked["timeline"][1]["evidence"][0])


if __name__ == "__main__":
    unittest.main()
