"""Tests for core/gates/tracker.py, written from docs/adr/0116 and the
Tracker port contract before the implementation.

A fair Tracker (get, put, and optional sync) gets GO; a tracker broken in
any of the six conformance invariants gets a NO-GO naming its case; external
adapters under outside/<name> resolve cleanly via adapters/outside/<name> or
ADAPTERS_PATH.
"""

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from core.gates import NoGo, tracker


class FakeTracker:
    """An in-memory Tracker port, honest unless brk names a defect."""

    def __init__(self, brk=None, *, with_sync=True, with_ready=True):
        self.brk = brk
        self.with_sync = with_sync
        self.with_ready = with_ready
        self.tickets = {}
        self.seq = 0
        self.sync_calls = 0

    def __call__(self, name, *args, data=None, env=None):
        if name == "ready":
            if not self.with_ready:
                raise AssertionError("ready called unexpectedly")
            if self.brk == "ready-fails":
                return 1, "", "not ready\n"
            if self.brk == "ready-stdout":
                return 0, "ready!\n", ""
            return 0, "", ""

        if name == "put":
            title = args[0] if len(args) > 0 else ""
            origin = args[1] if len(args) > 1 else ""
            if (not title and self.brk != "accept-empty-title") or (not origin and self.brk != "accept-empty-origin"):
                if self.brk == "empty-put-stdout":
                    return 1, "bad-id\n", "usage error\n"
                return 1, "", "usage: put.sh <title> <origin>\n"
            if self.brk == "put-fails":
                return 1, "", "backend error\n"
            if self.brk != "put-duplicate":
                for tid, rec in self.tickets.items():
                    if rec["origin"] == origin:
                        return 0, f"{tid}\n", ""
            self.seq += 1
            tid = f"{self.seq:04d}"
            if self.brk == "put-empty-id":
                return 0, "\n", ""
            if self.brk == "put-spaced-id":
                tid = f"bad id {self.seq}"
            body = data.decode("utf-8") if data is not None else ""
            self.tickets[tid] = {
                "title": title,
                "origin": origin,
                "status": "open",
                "pr": "",
                "adr": "",
                "body": body,
            }
            return 0, f"{tid}\n", "filed ticket\n"

        if name == "get":
            tid = args[0] if args else ""
            if not tid or tid not in self.tickets:
                if self.brk == "missing-get-succeeds":
                    return 0, "# missing\n", ""
                if self.brk == "missing-get-stdout":
                    return 1, "not found\n", ""
                return 1, "", f"no such ticket: {tid}\n"
            if self.brk == "get-fails":
                return 1, "", "read error\n"
            rec = self.tickets[tid]
            lines = [f"# {tid} — {'other title' if self.brk == 'get-missing-title' else rec['title']}", ""]
            if self.brk != "get-missing-status":
                lines.append(f"- Status: {rec['status']}")
            if self.brk != "get-missing-origin":
                lines.append(f"- Origin: {rec['origin']}")
            if rec["pr"] and self.brk != "get-missing-pr":
                lines.append(f"- PR: {rec['pr']}")
            if rec["adr"] and self.brk != "get-missing-adr":
                lines.append(f"- ADR: {rec['adr']}")
            if self.brk == "sync-duplicates" and self.sync_calls >= 2:
                lines.append(f"- Status: {rec['status']}")
            lines.append("")
            if self.brk != "get-missing-body":
                lines.append(rec["body"].rstrip("\n"))
            return 0, "\n".join(lines) + "\n", ""

        if name == "sync":
            if not self.with_sync:
                raise AssertionError("sync called when absent")
            self.sync_calls += 1
            tid = args[0] if len(args) > 0 else ""
            state = args[1] if len(args) > 1 else ""
            pr = args[2] if len(args) > 2 else ""
            adr = args[3] if len(args) > 3 else ""
            if not tid or not state or tid not in self.tickets:
                return 1, "", "missing ticket or state\n"
            if self.brk == "sync-fails":
                return 1, "", "sync failed\n"
            if self.brk == "sync-stdout":
                return 0, "synced\n", ""
            if self.brk == "sync-not-idempotent" and self.sync_calls >= 2:
                return 1, "", "duplicate sync refused\n"
            rec = self.tickets[tid]
            rec["status"] = "wrong-state" if self.brk == "sync-wrong-status" else state
            if pr:
                rec["pr"] = pr
            if adr:
                rec["adr"] = adr
            return 0, "", ""

        raise AssertionError(f"unexpected verb {name}")


class TrackerSuiteTest(unittest.TestCase):
    def run_suite(self, fake, *, tracker_name="fake", env_extra=None):
        err = io.StringIO()
        env = {"TRACKER": tracker_name, **(env_extra or {})}
        with contextlib.redirect_stderr(err):
            try:
                got = (
                    "GO",
                    tracker.judge(
                        [],
                        env=env,
                        verb=fake,
                        has_sync=fake.with_sync,
                        has_ready=fake.with_ready,
                    ),
                )
            except NoGo as no:
                got = ("NO-GO", no.reason)
        self.err = err.getvalue()
        return got

    def test_fair_tracker_with_sync_passes_all_6_cases(self):
        fake = FakeTracker(with_sync=True, with_ready=True)
        verdict, detail = self.run_suite(fake)
        self.assertEqual(verdict, "GO", detail)
        self.assertEqual(detail, "fake, 6 cases")
        self.assertIn("case 6 missing-get-refused: GO", self.err)
        self.assertIn("  [put] filed ticket", self.err)

    def test_fair_tracker_without_sync_passes_and_notes_skipped_case4(self):
        fake = FakeTracker(with_sync=False, with_ready=False)
        verdict, detail = self.run_suite(fake)
        self.assertEqual(verdict, "GO", detail)
        self.assertIn("no sync.sh", detail)
        self.assertIn("case 4 sync-metadata: skipped", self.err)

    def test_each_defect_names_its_case(self):
        cases = (
            ("ready-fails", "preflight ready"),
            ("ready-stdout", "preflight ready"),
            ("put-fails", "case1 put-create"),
            ("put-empty-id", "case1 put-create"),
            ("put-spaced-id", "case1 put-create"),
            ("get-fails", "case2 get-roundtrip"),
            ("get-missing-title", "case2 get-roundtrip"),
            ("get-missing-origin", "case2 get-roundtrip"),
            ("get-missing-body", "case2 get-roundtrip"),
            ("put-duplicate", "case3 put-idempotent"),
            ("sync-fails", "case4 sync-metadata"),
            ("sync-stdout", "case4 sync-metadata"),
            ("sync-wrong-status", "case4 sync-metadata"),
            ("get-missing-pr", "case4 sync-metadata"),
            ("get-missing-adr", "case4 sync-metadata"),
            ("sync-not-idempotent", "case4 sync-metadata"),
            ("sync-duplicates", "case4 sync-metadata"),
            ("accept-empty-title", "case5 invalid-put-refused"),
            ("accept-empty-origin", "case5 invalid-put-refused"),
            ("empty-put-stdout", "case5 invalid-put-refused"),
            ("missing-get-succeeds", "case6 missing-get-refused"),
            ("missing-get-stdout", "case6 missing-get-refused"),
        )
        for brk, prefix in cases:
            with self.subTest(brk=brk):
                verdict, reason = self.run_suite(FakeTracker(brk=brk))
                self.assertEqual(verdict, "NO-GO", reason)
                self.assertTrue(reason.startswith(prefix + ":"), f"{brk}: {reason}")
                self.assertNotIn("\n", reason)

    def test_resolve_adapter_dir_in_repo_and_outside(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chdir(root)
            try:
                (root / "adapters" / "local-tr").mkdir(parents=True)
                self.assertEqual(tracker.resolve_adapter_dir("local-tr", {}), "adapters/local-tr")

                ext = root / "ext"
                (ext / "ext-tr").mkdir(parents=True)
                resolved = tracker.resolve_adapter_dir("outside/ext-tr", {"ADAPTERS_PATH": str(ext)})
                self.assertEqual(Path(resolved), ext / "ext-tr")

                (root / "adapters" / "outside").mkdir(parents=True)
                os.symlink(ext / "ext-tr", root / "adapters" / "outside" / "linked-tr")
                self.assertEqual(
                    tracker.resolve_adapter_dir("outside/linked-tr", {}),
                    "adapters/outside/linked-tr",
                )

                for bad_name in ("", "../escape", "outside/Bad", "outside/../x", "absent"):
                    with self.assertRaises(NoGo, msg=bad_name):
                        tracker.resolve_adapter_dir(bad_name, {"ADAPTERS_PATH": str(ext)})

                with self.assertRaises(NoGo):
                    tracker.resolve_adapter_dir("outside/ext-tr", {"ADAPTERS_PATH": "relative/path"})
            finally:
                os.chdir(cwd)

    def test_judge_requires_tracker_and_mandatory_verbs(self):
        with self.assertRaises(NoGo) as ctx:
            tracker.judge([], env={})
        self.assertIn("TRACKER", ctx.exception.reason)

        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chdir(root)
            try:
                (root / "adapters" / "incomplete").mkdir(parents=True)
                (root / "adapters" / "incomplete" / "get.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                with self.assertRaises(NoGo) as ctx2:
                    tracker.judge([], env={"TRACKER": "incomplete"})
                self.assertIn("put.sh", ctx2.exception.reason)
            finally:
                os.chdir(cwd)


if __name__ == "__main__":
    unittest.main()
