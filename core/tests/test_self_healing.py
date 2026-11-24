"""Specification tests for the closed self-healing loop (docs/adr/0121).

Strictly vendor-free, app-free, and model-free. Covers, in this order:
bounded log capture with secret redaction and stage exemplars
(core/ops_loop.py), post-mortem action-ticket perimeters (core/ops_loop.py),
and the policy-gated triage mitigation (core/triage.py, core/webhook.py).
Each test is derived from a sentence of the ADR, not from the code.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import ops_loop, spec, triage, webhook

VERSION = "0.4.0-rc1"
STABLE = "0.3.9"


def stage(pct, verdict, slo=None, numbers=None):
    entry = {"pct": pct, "verdict": verdict, "numbers": numbers or [],
             "started_at": "2026-11-24T04:00:00Z", "judged_at": "2026-11-24T04:01:00Z"}
    if slo is not None:
        entry["slo"] = slo
    return entry


def record(verdict="rolled-back", stages=None, version=VERSION, stable=STABLE):
    return {"version": version, "stable": stable, "stages": stages or [], "verdict": verdict,
            "held_at": None, "image": "registry.local/service@sha256:1111", "sbom": None,
            "dast": None, "reason": None}


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


# --- 1. redaction, bounded collection, exemplars ---


class RedactionTests(unittest.TestCase):
    def test_assignment_shapes_keep_the_name_and_drop_the_value(self) -> None:
        self.assertEqual(ops_loop.redact_line("SERVICE_KEY=abc123 started"), "SERVICE_KEY=[REDACTED] started")
        self.assertEqual(ops_loop.redact_line("export DEPLOY_TOKEN=\"a b c\" ok"), "export DEPLOY_TOKEN=[REDACTED] ok")
        self.assertEqual(ops_loop.redact_line("SIGNING_SECRET='x y'"), "SIGNING_SECRET=[REDACTED]")
        self.assertEqual(ops_loop.redact_line("deploy_token=lowercase"), "deploy_token=[REDACTED]")

    def test_json_member_and_authorization_header(self) -> None:
        self.assertEqual(ops_loop.redact_line('{"auth_token": "zzz", "n": 1}'), '{"auth_token": "[REDACTED]", "n": 1}')
        self.assertEqual(ops_loop.redact_line('{"api_token": "ab\\"cd", "n": 1}'), '{"api_token": "[REDACTED]", "n": 1}')
        self.assertEqual(ops_loop.redact_line("Authorization: Bearer abc.def-123"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(ops_loop.redact_line("authorization: bearer abc"), "Authorization: Bearer [REDACTED]")
        self.assertEqual(ops_loop.redact_line("Authorization: Basic dXNlcjpwdw=="), "Authorization: Basic [REDACTED]")

    def test_ordinary_lines_are_untouched(self) -> None:
        for line in ("", "slo: error_rate canary(0.4.0-rc1)=0.500 stable(0.3.9)=0.000", "key=value", "TOKENS are fine"):
            self.assertEqual(ops_loop.redact_line(line), line)


class CollectTests(TempCase):
    def test_bound_constants_match_the_adr(self) -> None:
        self.assertEqual(ops_loop.MAX_LOG_LINES, 200)
        self.assertEqual(ops_loop.MAX_LOG_LINE_CHARS, 320)

    def test_tail_keeps_the_last_lines_in_order(self) -> None:
        path = self.write("canary.log", "".join(f"line {n}\n" for n in range(1, 251)))
        lines = ops_loop.collect_log_tail(str(path))
        self.assertEqual(len(lines), 200)
        self.assertEqual(lines[0], "line 51")
        self.assertEqual(lines[-1], "line 250")

    def test_long_line_is_cut_inside_the_cap_and_marked(self) -> None:
        path = self.write("canary.log", "x" * 1000 + "\nshort\n")
        lines = ops_loop.collect_log_tail(str(path))
        self.assertEqual(len(lines[0]), 320)
        self.assertTrue(lines[0].endswith("..."))
        self.assertEqual(lines[1], "short")

    def test_redaction_precedes_the_cut_so_a_secret_never_straddles_it(self) -> None:
        line = "y" * 300 + " SERVICE_KEY=supersecretvalue"
        path = self.write("canary.log", line + "\n")
        out = ops_loop.collect_log_tail(str(path))
        self.assertNotIn("supersecret", out[0])

    def test_missing_file_is_empty_not_an_error(self) -> None:
        self.assertEqual(ops_loop.collect_log_tail("nope.log"), [])


class ExemplarTests(unittest.TestCase):
    def test_structured_slo_entries_give_numbers_and_a_breach(self) -> None:
        rec = record(stages=[
            stage(5, "GO", slo=[{"query": "traffic", "canary": 1.2, "stable": 4.1, "delta": -2.9, "max_delta": None},
                                {"query": "error_rate", "canary": 0.0, "stable": 0.0, "delta": 0.0, "max_delta": 0.01}]),
            stage(25, "NO-GO: slo — error_rate 0.500 > 0.010", slo=[
                {"query": "error_rate", "canary": 0.5, "stable": 0.0, "delta": 0.5, "max_delta": 0.01},
                {"query": "latency_p95", "canary": 0.9, "stable": 0.5, "delta": 0.4, "max_delta": 0.25}]),
        ])
        ex = ops_loop.exemplars_of(rec)
        self.assertEqual(ex["failing_stage_pct"], 25)
        self.assertEqual([s["pct"] for s in ex["stages"]], [5, 25])
        self.assertEqual(ex["stages"][1]["error_rate"],
                         {"canary": 0.5, "stable": 0.0, "delta": 0.5, "max_delta": 0.01})
        self.assertEqual(ex["stages"][1]["p95"], {"canary": 0.9, "stable": 0.5, "delta": 0.4, "max_delta": 0.25})
        self.assertIsNone(ex["stages"][0]["p95"])
        self.assertEqual(ex["stages"][0]["traffic"]["canary"], 1.2)
        self.assertEqual(ex["breach"], {"pct": 25, "query": "error_rate", "delta": 0.5, "max_delta": 0.01})

    def test_numbers_text_is_parsed_when_a_stage_has_no_slo_list(self) -> None:
        rec = record(stages=[
            stage(25, "NO-GO: slo", numbers=["slo: error_rate canary(0.4.0-rc1)=0.500 stable(0.3.9)=0.000"])])
        ex = ops_loop.exemplars_of(rec)
        self.assertEqual(ex["stages"][0]["error_rate"],
                         {"canary": 0.5, "stable": 0.0, "delta": 0.5, "max_delta": None})
        self.assertIsNone(ex["breach"], "no threshold in the record, none invented")

    def test_all_go_has_no_failing_stage_and_no_stages_is_empty(self) -> None:
        ex = ops_loop.exemplars_of(record(stages=[stage(5, "GO")]))
        self.assertIsNone(ex["failing_stage_pct"])
        self.assertIsNone(ex["breach"])
        self.assertEqual(ops_loop.exemplars_of(record(stages=[])),
                         {"failing_stage_pct": None, "stages": [], "breach": None})

    def test_deterministic(self) -> None:
        rec = record(stages=[stage(25, "NO-GO: slo", numbers=["slo: error_rate canary(a)=0.500 stable(b)=0.000"])])
        self.assertEqual(json.dumps(ops_loop.exemplars_of(rec)), json.dumps(ops_loop.exemplars_of(rec)))


class OnRollbackCaptureTests(TempCase):
    def _record(self) -> str:
        rec = record(stages=[stage(25, "NO-GO: slo — error_rate 0.500 > 0.010",
                                   numbers=["slo: error_rate canary(0.4.0-rc1)=0.500 stable(0.3.9)=0.000"])])
        return str(self.write(".crew/release.json", json.dumps(rec)))

    def _run(self, env=None):
        with contextlib.redirect_stderr(io.StringIO()):
            res = ops_loop.on_rollback(self._record(), dict({"INCIDENT_OUT": ".crew/incidents"}, **(env or {})))
        return res, Path(res["incident_path"]).read_text(encoding="utf-8")

    def test_canary_log_fallback_is_tailed_and_redacted(self) -> None:
        body = "".join(f"line {n} SERVICE_KEY=leak{n}\n" for n in range(1, 251))
        self.write(".crew/canary.log", body)
        res, text = self._run()
        self.assertEqual(len(res["incident"]["logs"]), 200)
        self.assertEqual(res["incident"]["logs"][-1], "line 250 SERVICE_KEY=[REDACTED]")
        self.assertNotIn("leak", text)

    def test_rollback_log_source_wins_over_the_canary_log(self) -> None:
        self.write(".crew/canary.log", "from canary log\n")
        self.write("elsewhere.log", "from the named source\nDEPLOY_TOKEN=abc\n")
        res, text = self._run({"ROLLBACK_LOG_SOURCE": "elsewhere.log"})
        self.assertEqual(res["incident"]["logs"], ["from the named source", "DEPLOY_TOKEN=[REDACTED]"])
        self.assertNotIn("abc", text)

    def test_explicit_logs_win_are_not_truncated_but_are_redacted(self) -> None:
        self.write(".crew/canary.log", "from canary log\n")
        self.write("given.log", "".join(f"g{n}\n" for n in range(300)) + "Authorization: Bearer tok.en\n")
        res, text = self._run({"LOGS": "given.log"})
        self.assertEqual(len(res["incident"]["logs"]), 301)
        self.assertEqual(res["incident"]["logs"][-1], "Authorization: Bearer [REDACTED]")
        self.assertNotIn("tok.en", text)

    def test_no_source_leaves_logs_empty(self) -> None:
        res, _ = self._run()
        self.assertEqual(res["incident"]["logs"], [])

    def test_incident_keeps_its_keys_and_adds_exemplars(self) -> None:
        res, _ = self._run()
        doc = res["incident"]
        for key in ("kind", "name", "window", "release", "alerts", "logs", "chain"):
            self.assertIn(key, doc)
        self.assertEqual(doc["exemplars"]["failing_stage_pct"], 25)
        # postmortem.py ignores unknown keys: the document still validates.
        from core import postmortem
        postmortem.validate(res["incident_path"])


# --- 2. perimeters ---


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@example.test", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


TICKET = """# 0100 — Fix the retry loop

- Status: open
- Origin: postmortem rollback-x action 1

## Problem

The retry loop spins.

## Acceptance criteria

Given a failing upstream
When the loop retries
Then it stops after three attempts and the log shows the third attempt

## Notes

Written by the ops-triage role.
"""


class PerimeterTests(TempCase):
    def make_repo(self) -> str:
        git(self.root, "init", "-q")
        self.write("service/api.py", "v = 1\n")
        self.write("service/tests/test_api.py", "pass\n")
        self.write("service/other.py", "o = 1\n")
        self.write("tickets/0001.md", "# one\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "checkout", "-q", "-b", "feature")
        self.write("service/api.py", "v = 2\n")
        self.write("service/util.py", "u = 1\n")
        self.write("tickets/0002.md", "# two\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "change")
        git(self.root, "checkout", "-q", "main")
        # an unrelated commit on main makes the merge a real two-parent commit
        self.write("service/other.py", "o = 2\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "main moves")
        git(self.root, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
        return git(self.root, "rev-parse", "HEAD")

    def chain(self, sha):
        return {"ticket": "0007", "pr": 3, "merge": sha}

    def test_changed_files_of_a_merge_are_its_first_parent_diff(self) -> None:
        sha = self.make_repo()
        self.assertEqual(ops_loop.changed_files(str(self.root), sha),
                         ["service/api.py", "service/util.py"])

    def test_changed_files_refuses_a_non_hex_revision(self) -> None:
        self.make_repo()
        self.assertEqual(ops_loop.changed_files(str(self.root), "--output=x"), [])
        self.assertEqual(ops_loop.changed_files(str(self.root), "HEAD"), [])
        self.assertFalse((self.root / "x").exists())

    def test_injects_changed_files_and_neighbour_tests_before_notes(self) -> None:
        sha = self.make_repo()
        ticket = self.write("work/0100.md", TICKET)
        (res,) = ops_loop.derive_action_perimeters([str(ticket)], self.chain(sha), str(self.root))
        self.assertEqual(res["status"], "injected")
        self.assertEqual(res["perimeter"], ["service/api.py", "service/tests/test_api.py", "service/util.py"])
        text = ticket.read_text(encoding="utf-8")
        self.assertIn("## Perimeter\n\n- `service/api.py`\n- `service/tests/test_api.py`\n- `service/util.py`\n\n## Notes", text)
        self.assertIsNone(spec.check(text))
        self.assertTrue(text.startswith(TICKET.split("## Notes")[0]))
        self.assertIsNone(res["readiness"], "readiness is only reported when promotion is on")

    def test_same_commit_same_bytes(self) -> None:
        sha = self.make_repo()
        a = self.write("work/a.md", TICKET)
        b = self.write("work/b.md", TICKET)
        ops_loop.derive_action_perimeters([str(a)], self.chain(sha), str(self.root))
        ops_loop.derive_action_perimeters([str(b)], self.chain(sha), str(self.root))
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_perimeter_is_appended_when_there_are_no_notes(self) -> None:
        sha = self.make_repo()
        ticket = self.write("work/0101.md", TICKET.split("## Notes")[0].rstrip() + "\n")
        ops_loop.derive_action_perimeters([str(ticket)], self.chain(sha), str(self.root))
        self.assertTrue(ticket.read_text(encoding="utf-8").rstrip().endswith("- `service/util.py`"))

    def test_existing_perimeter_is_kept_byte_for_byte(self) -> None:
        sha = self.make_repo()
        text = TICKET.replace("## Notes", "## Perimeter\n\n- `docs/x.md`\n\n## Notes")
        ticket = self.write("work/0102.md", text)
        (res,) = ops_loop.derive_action_perimeters([str(ticket)], self.chain(sha), str(self.root))
        self.assertEqual(res["status"], "kept")
        self.assertEqual(res["perimeter"], ["docs/x.md"])
        self.assertEqual(ticket.read_text(encoding="utf-8"), text)

    def test_no_chain_or_no_merge_touches_nothing(self) -> None:
        self.make_repo()
        ticket = self.write("work/0103.md", TICKET)
        for chain in (None, {"ticket": "1", "pr": 1, "merge": ""}, {"ticket": "1", "pr": 1, "merge": "f" * 40}):
            (res,) = ops_loop.derive_action_perimeters([str(ticket)], chain, str(self.root))
            self.assertEqual(res["status"], "needs-perimeter")
            self.assertEqual(ticket.read_text(encoding="utf-8"), TICKET)

    def test_map_database_supplies_test_edges_instead_of_naming_conventions(self) -> None:
        sha = self.make_repo()
        db = self.root / "map.sqlite"
        con = sqlite3.connect(db)
        con.executescript("""
            CREATE TABLE node (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                               title TEXT NOT NULL DEFAULT '', UNIQUE (kind, name));
            CREATE TABLE edge (src INTEGER NOT NULL, dst INTEGER NOT NULL, kind TEXT NOT NULL,
                               PRIMARY KEY (src, dst, kind));
            INSERT INTO node VALUES (1, 'file', 'service/api.py', '');
            INSERT INTO node VALUES (2, 'test', 'service/tests/test_contract.py', '');
            INSERT INTO edge VALUES (2, 1, 'tests');
        """)
        con.commit()
        con.close()
        ticket = self.write("work/0104.md", TICKET)
        (res,) = ops_loop.derive_action_perimeters([str(ticket)], self.chain(sha), str(self.root), str(db))
        self.assertEqual(res["perimeter"], ["service/api.py", "service/tests/test_contract.py", "service/util.py"])

    def test_unreadable_map_falls_back_to_conventions(self) -> None:
        sha = self.make_repo()
        bad = self.write("map.sqlite", "not a database")
        ticket = self.write("work/0105.md", TICKET)
        (res,) = ops_loop.derive_action_perimeters([str(ticket)], self.chain(sha), str(self.root), str(bad))
        self.assertIn("service/tests/test_api.py", res["perimeter"])

    def test_readiness_when_promotion_is_on(self) -> None:
        sha = self.make_repo()
        good = self.write("work/0106.md", TICKET)
        vague = self.write("work/0107.md", TICKET.replace("Then it stops after three attempts and the log shows the third attempt",
                                                           "Then it works correctly"))
        on = {"AUTO_PROMOTE_POSTMORTEM_TICKETS": "on"}
        r_good, r_vague = ops_loop.derive_action_perimeters(
            [str(good), str(vague)], self.chain(sha), str(self.root), env=on)
        self.assertEqual(r_good["readiness"], "spec-ready")
        self.assertEqual(r_vague["readiness"], "needs-spec")
        self.assertIn("works", r_vague["reason"])
        (r_none,) = ops_loop.derive_action_perimeters(
            [str(self.write("work/0108.md", TICKET))], None, str(self.root), env=on)
        self.assertEqual(r_none["readiness"], "needs-perimeter")
        (r_off,) = ops_loop.derive_action_perimeters(
            [str(self.write("work/0109.md", TICKET))], self.chain(sha), str(self.root), env={"AUTO_PROMOTE_POSTMORTEM_TICKETS": "off"})
        self.assertIsNone(r_off["readiness"])

    def test_missing_ticket_file_is_reported_not_raised(self) -> None:
        sha = self.make_repo()
        (res,) = ops_loop.derive_action_perimeters(["work/none.md"], self.chain(sha), str(self.root))
        self.assertEqual(res["status"], "needs-perimeter")

    def test_unsafe_paths_are_never_injected(self) -> None:
        self.assertFalse(ops_loop.safe_glob("../etc/passwd"))
        self.assertFalse(ops_loop.safe_glob("/abs/path"))
        self.assertFalse(ops_loop.safe_glob("a`b"))
        self.assertFalse(ops_loop.safe_glob("a\nb"))
        self.assertFalse(ops_loop.safe_glob(""))
        self.assertTrue(ops_loop.safe_glob("service/api.py"))


# --- 3. mitigation ---


class MitigateTests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.mark = self.root / "mark.log"
        self.stub("adapters/stub-delivery/rollback.sh", 'echo "rollback" >> "$MARK"\n')
        self.stub("adapters/stub-flags/set.sh", 'echo "set $1 $2" >> "$MARK"\n')
        self.rec = self.write(".crew/release.json", json.dumps(record(verdict="held")))
        self.env = {"AUTO_MITIGATE_ON_TRIAGE": "on", "DELIVERY": "stub-delivery", "FLAGS": "stub-flags",
                    "WORKLOAD_KILL_SWITCH": "feature-x", "MARK": str(self.mark), "PATH": os.environ.get("PATH", "")}

    def stub(self, rel: str, body: str) -> None:
        path = self.write(rel, "#!/bin/sh\nset -eu\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def result(self, action="rollback", version=VERSION, headline="error budget burn"):
        return {"alert": {"headline": headline, "version": version}, "title": "t",
                "hypotheses": [{"rank": 1, "cause": "c", "evidence": "e"}, {"rank": 2, "cause": "d", "evidence": "f"}],
                "proposal": {"action": action, "version": version, "reason": "r"}, "run": {}}

    def audit(self):
        path = self.root / ".crew" / "mitigations.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []

    def ran(self):
        return self.mark.read_text(encoding="utf-8").splitlines() if self.mark.is_file() else []

    def mitigate(self, result=None, env=None, rec=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return triage.mitigate(result or self.result(), str(rec or self.rec), dict(self.env, **(env or {})))

    def test_disabled_by_default_does_nothing_at_all(self) -> None:
        for env in ({"AUTO_MITIGATE_ON_TRIAGE": ""}, {"AUTO_MITIGATE_ON_TRIAGE": "off"}):
            res = self.mitigate(env=env)
            self.assertEqual(res, {"status": "disabled"})
        self.assertEqual(self.ran(), [])
        self.assertEqual(self.audit(), [])
        self.assertEqual(json.loads(self.rec.read_text())["verdict"], "held")

    def test_rollback_proposal_on_held_canary_is_applied_and_audited(self) -> None:
        res = self.mitigate()
        self.assertEqual(res["status"], "applied")
        self.assertEqual(self.ran(), ["rollback"], "kill-switch is opt-in")
        rec = json.loads(self.rec.read_text())
        self.assertEqual(rec["verdict"], "rolled-back")
        self.assertEqual(rec["reason"], "mitigated on triage of error budget burn")
        (entry,) = self.audit()
        self.assertEqual(set(entry), {"ts", "alert", "version", "action", "verdict", "rc"})
        self.assertEqual((entry["alert"], entry["version"], entry["action"], entry["verdict"], entry["rc"]),
                         ("error budget burn", VERSION, "rollback", "applied", 0))
        self.assertRegex(entry["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        log = [json.loads(line) for line in (self.root / ".crew" / "releases.jsonl").read_text().splitlines()]
        self.assertEqual(log[-1]["kind"], "release")
        self.assertEqual(log[-1]["record"]["verdict"], "rolled-back")

    def test_in_progress_canary_is_mitigated_too(self) -> None:
        self.write(".crew/release.json", json.dumps(record(verdict="in-progress")))
        self.assertEqual(self.mitigate()["status"], "applied")

    def test_kill_switch_runs_first_then_rollback_one_audit_line_each(self) -> None:
        res = self.mitigate(env={"MITIGATE_ACTIONS": "kill-switch,rollback"})
        self.assertEqual(res["status"], "applied")
        self.assertEqual(self.ran(), ["set feature-x off", "rollback"])
        self.assertEqual([(e["action"], e["verdict"]) for e in self.audit()],
                         [("kill-switch", "applied"), ("rollback", "applied")])

    def test_kill_switch_alone_changes_no_verdict(self) -> None:
        res = self.mitigate(env={"MITIGATE_ACTIONS": "kill-switch"})
        self.assertEqual(res["status"], "applied")
        self.assertEqual(self.ran(), ["set feature-x off"])
        self.assertEqual(json.loads(self.rec.read_text())["verdict"], "held")

    def test_hold_proposal_is_refused_and_audited_without_running_anything(self) -> None:
        res = self.mitigate(self.result(action="hold"))
        self.assertEqual(res["status"], "refused")
        self.assertEqual(self.ran(), [])
        (entry,) = self.audit()
        self.assertEqual((entry["action"], entry["verdict"], entry["rc"]), ("none", "refused", None))

    def test_version_not_the_live_canary_is_refused(self) -> None:
        res = self.mitigate(self.result(version="0.5.0"))
        self.assertEqual(res["status"], "refused")
        self.assertEqual(self.ran(), [])

    def test_stable_version_is_never_rolled_back(self) -> None:
        res = self.mitigate(self.result(version=STABLE), env={"CANARY_VERSION": STABLE})
        self.assertEqual(res["status"], "refused")
        self.assertEqual(self.ran(), [])

    def test_explicit_canary_version_stands_in_for_a_missing_record(self) -> None:
        missing = self.root / "none.json"
        self.assertEqual(self.mitigate(rec=missing)["status"], "refused")
        res = self.mitigate(rec=missing, env={"CANARY_VERSION": VERSION})
        self.assertEqual(res["status"], "applied")
        self.assertEqual(self.ran(), ["rollback"])
        self.assertFalse(missing.exists(), "no record to update, none is invented")

    def test_alert_and_proposal_versions_must_agree(self) -> None:
        doc = self.result()
        doc["alert"]["version"] = "0.5.0"
        self.assertEqual(self.mitigate(doc)["status"], "refused")
        self.assertEqual(self.ran(), [])

    def test_already_rolled_back_is_skipped(self) -> None:
        self.write(".crew/release.json", json.dumps(record(verdict="rolled-back")))
        res = self.mitigate()
        self.assertEqual(res["status"], "already-rolled-back")
        self.assertEqual(self.ran(), [])
        self.assertEqual([(e["verdict"], e["rc"]) for e in self.audit()], [("skipped", None)])

    def test_adapter_failure_is_recorded_and_the_record_is_left_alone(self) -> None:
        self.stub("adapters/stub-delivery/rollback.sh", "exit 3\n")
        res = self.mitigate()
        self.assertEqual(res["status"], "failed")
        (entry,) = self.audit()
        self.assertEqual((entry["verdict"], entry["rc"]), ("failed", 3))
        self.assertEqual(json.loads(self.rec.read_text())["verdict"], "held")

    def test_adapter_timeout_is_a_failure_not_a_hang(self) -> None:
        self.stub("adapters/stub-delivery/rollback.sh", "sleep 30\n")
        res = self.mitigate(env={"MITIGATE_TIMEOUT_S": "1"})
        self.assertEqual(res["status"], "failed")
        self.assertEqual(self.audit()[0]["verdict"], "failed")

    def test_default_timeout_matches_the_adr(self) -> None:
        self.assertEqual(triage.MITIGATE_TIMEOUT_S, 120)

    def test_unsafe_adapter_names_are_refused(self) -> None:
        for env in ({"DELIVERY": "../stub-delivery"}, {"DELIVERY": "-x"}, {"DELIVERY": "a b"}, {"DELIVERY": ""},
                    {"DELIVERY": "missing-adapter"}):
            res = self.mitigate(env=env)
            self.assertEqual(res["status"], "refused", env)
        res = self.mitigate(env={"MITIGATE_ACTIONS": "kill-switch", "WORKLOAD_KILL_SWITCH": "a;b"})
        self.assertEqual(res["status"], "refused")
        self.assertEqual(self.mitigate(env={"MITIGATE_ACTIONS": "rm"})["status"], "refused")
        self.assertEqual(self.ran(), [])

    def test_alert_text_never_reaches_an_argv(self) -> None:
        self.stub("adapters/stub-delivery/rollback.sh", 'echo "argc $#" >> "$MARK"\n')
        self.mitigate(self.result(headline="$(touch pwned); `touch pwned2`"))
        self.assertEqual(self.ran(), ["argc 0"])
        self.assertFalse((self.root / "pwned").exists() or (self.root / "pwned2").exists())

    def test_audit_is_append_only(self) -> None:
        self.write(".crew/mitigations.jsonl", '{"earlier":true}\n')
        self.mitigate()
        lines = (self.root / ".crew" / "mitigations.jsonl").read_text().splitlines()
        self.assertEqual(lines[0], '{"earlier":true}')
        self.assertEqual(len(lines), 2)

    def test_audit_holds_no_environment_values(self) -> None:
        self.mitigate(env={"SOME_TOKEN": "hunter2-secret"})
        self.assertNotIn("hunter2", (self.root / ".crew" / "mitigations.jsonl").read_text())

    def test_mitigations_log_path_is_overridable(self) -> None:
        self.mitigate(env={"MITIGATIONS_LOG": "out/audit.jsonl"})
        self.assertTrue((self.root / "out" / "audit.jsonl").is_file())

    def test_auto_incident_follows_a_mitigated_rollback_when_opted_in(self) -> None:
        res = self.mitigate(env={"AUTO_INCIDENT_ON_ROLLBACK": "on", "INCIDENT_OUT": ".crew/incidents"})
        self.assertEqual(res["status"], "applied")
        files = list((self.root / ".crew" / "incidents").glob("rollback-*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(res["incident"], str(Path(".crew/incidents") / files[0].name))

    def test_no_incident_without_the_opt_in(self) -> None:
        self.mitigate()
        self.assertFalse((self.root / ".crew" / "incidents").exists())

    def test_result_may_be_given_as_a_path(self) -> None:
        path = self.write("incident.json", json.dumps(self.result()))
        self.assertEqual(self.mitigate(str(path))["status"], "applied")
        self.assertEqual(self.mitigate("missing-incident.json")["status"], "refused")

    def test_cli_mitigate_reads_incident_and_record(self) -> None:
        path = self.write("incident.json", json.dumps(self.result()))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = triage.main(["mitigate", str(path), str(self.rec)], env=self.env)
        self.assertEqual(rc, 0)
        self.assertIn("applied", out.getvalue())
        self.assertEqual(self.ran(), ["rollback"])


class WebhookMitigationTests(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.mark = self.root / "mark.log"
        path = self.write("adapters/stub-delivery/rollback.sh", '#!/bin/sh\necho rollback >> "$MARK"\n')
        path.chmod(0o755)
        self.write(".crew/release.json", json.dumps(record(verdict="held")))
        # a triage.sh stand-in: writes the incident core/triage.sh would
        self.write("core/triage.sh", (
            '#!/bin/sh\nset -eu\nstem=$(basename "$ALERT" .json)\nmkdir -p "${TRIAGE_OUT:-.crew/triage}/$stem"\n'
            'printf \'{"alert":{"headline":"burn","version":"%s"},"title":"t","hypotheses":[],'
            '"proposal":{"action":"rollback","version":"%s","reason":"r"},"run":{}}\' "$V" "$V" '
            '> "${TRIAGE_OUT:-.crew/triage}/$stem/incident.json"\necho "GO: triage"\n'))
        self.doc = {"alerts": [{"headline": "burn", "version": VERSION}]}
        self.base = self.root / "alerts" / "a.json"
        self.base.parent.mkdir()
        self.env = {"V": VERSION, "MARK": str(self.mark), "DELIVERY": "stub-delivery",
                    "PATH": os.environ.get("PATH", "")}

    def dispatch(self, env):
        with contextlib.redirect_stderr(io.StringIO()):
            return webhook._dispatch_alerts(self.doc, self.base, dict(self.env, **env))

    def test_dispatch_is_unchanged_without_the_opt_in(self) -> None:
        (res,) = self.dispatch({})
        self.assertEqual(res, {"index": 1, "version": VERSION, "status": "ok", "line": "GO: triage"})
        self.assertFalse(self.mark.exists())
        self.assertFalse((self.root / ".crew" / "mitigations.jsonl").exists())

    def test_dispatch_mitigates_after_a_successful_triage_when_opted_in(self) -> None:
        (res,) = self.dispatch({"AUTO_MITIGATE_ON_TRIAGE": "on"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["mitigation"]["status"], "applied")
        self.assertEqual(self.mark.read_text().splitlines(), ["rollback"])

    def test_failed_triage_is_never_mitigated(self) -> None:
        self.write("core/triage.sh", "#!/bin/sh\nexit 1\n")
        (res,) = self.dispatch({"AUTO_MITIGATE_ON_TRIAGE": "on"})
        self.assertEqual(res["status"], "error")
        self.assertNotIn("mitigation", res)
        self.assertFalse(self.mark.exists())


if __name__ == "__main__":
    unittest.main()
