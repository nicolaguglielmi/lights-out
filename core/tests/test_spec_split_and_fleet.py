"""Tests for M80: autonomous spec-split, bounded parallel fleet, ReleaseTrainLock, and M24 harness (ADR 0120).

Strictly vendor-free, app-free, and model-free: covers core/spec_split.py,
core/intake.py auto-split integration, core/releases.py ReleaseTrainLock + train,
core/runner.py bounded parallel workers + serialization, core/m24_runner.py,
and ADR 0120 provenance validation.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core import adr_gov, intake, m24_runner, releases, runner, scoreboard, spec, spec_split

ROOT = Path(__file__).resolve().parents[2]
ADR_0120 = ROOT / "docs" / "adr" / "0120-auto-spec-split-parallel-fleet-and-m24-harness.md"
M24_DATA = ROOT / "docs" / "scoreboard" / "m24-tenacity.json"
GWT = "Given a valid request, when the handler runs, then the response status is 200"


def _sample_ticket_md(perimeters: list[str], extra_sections: str = "") -> str:
    lines = [
        "# 0099 — Add service validation",
        "",
        "- Status: open",
        "- Feature: core-api",
        "- Priority: P1",
        "",
        "## Problem",
        "",
        "Requests with invalid fields are accepted without validation.",
        "",
        "## Acceptance criteria",
        "",
        GWT,
        "",
        "## Perimeter",
        "",
    ]
    lines.extend(f"- `{p}`" for p in perimeters)
    if extra_sections:
        lines.extend(["", extra_sections.strip()])
    return "\n".join(lines).rstrip("\n") + "\n"


def _dataset_row(ticket: str, provider: str, merged: bool, human_edits: bool = False) -> dict:
    row = {
        "ticket": ticket,
        "provider": provider,
        "harness": f"harness-{provider}",
        "model": f"model-{provider}",
        "merged": merged,
        "attempts": 1 if merged else 0,
        "reworks": 0,
        "nogos": [],
        "stop": None if merged else {"kind": "TOKEN_BUDGET", "line": "over TOKEN_BUDGET"},
        "tokens": {"input": 1000, "output": 100, "cached": None},
        "cost_usd": {"ledger": 0.05, "list": None, "cached": None},
        "source": {
            "handoff": f"docs/handoffs/{provider}.md §2",
            "run": f"tower state tickets/{ticket}",
        },
    }
    if human_edits:
        row["human_edits"] = True
    return row


class Adr0120Tests(unittest.TestCase):
    def test_adr_0120_exists_and_passes_provenance_validation(self) -> None:
        self.assertTrue(ADR_0120.is_file(), f"missing {ADR_0120}")
        text = ADR_0120.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        for symbol in (
            "MAX_NETWORK_MEMBERS",
            "MIN_SPLITTABLE_PATHS",
            "MAX_RUNNER_WORKERS",
            "RELEASE_TRAIN_TIMEOUT_S",
            "M24_MIN_PROVIDERS",
            "M24_PASS_NUMERATOR",
        ):
            self.assertIn(symbol, text)


class SpecSplitTests(unittest.TestCase):
    def test_classify_path_partitions_tests_and_impl_docs(self) -> None:
        for test_path in (
            "tests/test_service.py",
            "tests/helpers.py",
            "src/pkg/tests/conftest.py",
            "src/test_auth.py",
            "pkg/service_test.go",
            "web/button.spec.ts",
            "web/modal.test.tsx",
            "src/main/java/ServiceTest.java",
            "tests/*",
            "*/tests/*",
        ):
            self.assertEqual(spec_split.classify_path(test_path), "tests", test_path)

        for impl_path in (
            "src/service.py",
            "docs/guide.md",
            "README.md",
            "src/contest.py",
            "src/testing_notes.md",
            "pkg/router.go",
        ):
            self.assertEqual(spec_split.classify_path(impl_path), "impl", impl_path)

    def test_propose_network_split_disjoint_impl_and_tests(self) -> None:
        perimeter = ["src/service.py", "docs/guide.md", "tests/test_service.py"]
        members = spec_split.propose_network_split(perimeter, criteria=[GWT])
        self.assertIsNotNone(members)
        assert members is not None
        self.assertEqual([m["id"] for m in members], ["impl", "tests"])
        self.assertEqual(members[0]["paths"], ["src/service.py", "docs/guide.md"])
        self.assertEqual(members[1]["paths"], ["tests/test_service.py"])
        self.assertIn("not add or change any test", members[0]["brief"].lower())
        self.assertIn("do not modify implementation", members[1]["brief"].lower())
        self.assertEqual(spec_split.validate_network_members(members, perimeter=perimeter), [])

    def test_propose_network_split_rejects_overlapping_globs(self) -> None:
        for bad in (
            ["src/*", "src/tests/test_service.py"],
            ["src/*.py", "src/*_test.py"],
            ["*.py", "tests/test_service.py"],
            ["src/*", "src/service_test.go"],
        ):
            with self.subTest(perimeter=bad):
                self.assertIsNone(spec_split.propose_network_split(bad))

    def test_propose_network_split_returns_none_when_unsplittable(self) -> None:
        for unsplittable in (
            [],
            ["src/service.py"],
            ["src/service.py", "docs/guide.md"],
            ["tests/test_a.py", "tests/test_b.py"],
        ):
            with self.subTest(perimeter=unsplittable):
                self.assertIsNone(spec_split.propose_network_split(unsplittable))
        self.assertIsNone(spec_split.propose_network_split(["src/a.py", "tests/test_a.py"], max_members=1))

    def test_validate_network_members_catches_contract_violations(self) -> None:
        too_many = [{"id": f"m{i}", "brief": "work", "paths": [f"src/m{i}/*"]} for i in range(5)]
        self.assertTrue(any("at most 4" in e for e in spec_split.validate_network_members(too_many)))

        dup = [
            {"id": "impl", "brief": "a", "paths": ["src/a.py"]},
            {"id": "impl", "brief": "b", "paths": ["tests/test_a.py"]},
        ]
        self.assertTrue(any("twice" in e for e in spec_split.validate_network_members(dup)))

        uncovered = [
            {"id": "impl", "brief": "a", "paths": ["src/a.py"]},
            {"id": "tests", "brief": "b", "paths": ["tests/test_a.py"]},
        ]
        errs = spec_split.validate_network_members(
            uncovered,
            perimeter=["src/a.py", "docs/guide.md", "tests/test_a.py"],
        )
        self.assertTrue(any("uncovered" in e for e in errs))

    def test_render_and_inject_network_section_and_preserve_existing(self) -> None:
        md = _sample_ticket_md(["src/service.py", "docs/guide.md", "tests/test_service.py"], "## Notes\n\nKeep clean.")
        injected = spec_split.inject_network_section(md)
        self.assertIsNone(spec.check(injected))
        self.assertIn("## Network", injected)
        self.assertIn("### impl", injected)
        self.assertIn("### tests", injected)
        self.assertLess(injected.index("## Perimeter"), injected.index("## Network"))
        self.assertLess(injected.index("## Network"), injected.index("## Notes"))

        custom = md.replace("## Notes", "## Network\n\n### custom\n\nCustom brief.\n\n- `src/service.py`\n\n## Notes")
        self.assertEqual(spec_split.inject_network_section(custom), custom)

        unsplittable = _sample_ticket_md(["src/service.py"])
        self.assertEqual(spec_split.inject_network_section(unsplittable), unsplittable)

    def test_cli_check_split_and_compose(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            splittable = tdir / "0099.md"
            splittable.write_text(_sample_ticket_md(["src/service.py", "tests/test_service.py"]), encoding="utf-8")
            unsplittable = tdir / "0100.md"
            unsplittable.write_text(_sample_ticket_md(["src/service.py"]), encoding="utf-8")

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = spec_split.main(["check", str(splittable), "--json"])
            self.assertEqual(rc, 0)
            self.assertTrue(json.loads(out.getvalue())["ok"])

            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc_bad = spec_split.main(["check", str(unsplittable)])
            self.assertEqual(rc_bad, 1)

            rc_split = spec_split.main(["split", str(splittable), "--in-place"])
            self.assertEqual(rc_split, 0)
            content = splittable.read_text(encoding="utf-8")
            self.assertIn("## Network", content)
            self.assertEqual(spec.check(content), None)

            with contextlib.redirect_stdout(io.StringIO()):
                rc_recheck = spec_split.main(["check", str(splittable)])
            self.assertEqual(rc_recheck, 0)

            prop = tdir / "proposal.json"
            prop.write_text(json.dumps({"perimeter": ["src/api.py", "tests/test_api.py"], "criteria": [GWT]}), encoding="utf-8")
            comp_out = io.StringIO()
            with contextlib.redirect_stdout(comp_out):
                rc_comp = spec_split.main(["compose", str(prop)])
            self.assertEqual(rc_comp, 0)
            self.assertIn("### impl", comp_out.getvalue())
            self.assertIn("- `tests/test_api.py`", comp_out.getvalue())


class IntakeAutoSplitTests(unittest.TestCase):
    def _doc(self, perim: list[str] | None = None) -> dict:
        return {
            "id": "0105",
            "kind": "intake",
            "state": "drafted",
            "proposal": [
                {
                    "n": 1,
                    "title": "Validate input",
                    "problem": "Missing checks.",
                    "criteria": [GWT],
                    "feature": "core",
                    "priority": "P1",
                    **({"perimeter": perim} if perim is not None else {}),
                }
            ],
        }

    def test_compose_opt_in_and_explicit_network_hint(self) -> None:
        ticket = {
            "n": 1,
            "title": "Validate input",
            "problem": "Missing checks.",
            "criteria": [GWT],
            "feature": "core",
            "priority": "P1",
            "perimeter": ["src/service.py", "tests/test_service.py"],
        }
        default_md = intake.compose(ticket, "0106", "0105", env={})
        self.assertNotIn("## Network", default_md)

        split_md = intake.compose(ticket, "0106", "0105", env={"INTAKE_NETWORK_SPLIT": "on"})
        self.assertIn("## Network", split_md)
        self.assertIn("### impl", split_md)
        self.assertIn("### tests", split_md)
        self.assertIsNone(spec.check(split_md))

        hinted = dict(ticket, network={"impl": ["src/service.py"], "tests": ["tests/test_service.py"]})
        hinted_md = intake.compose(hinted, "0106", "0105", env={})
        self.assertIn("## Network", hinted_md)

        bad_hint = dict(ticket, network={"impl": ["src/service.py"], "tests": ["src/service.py"]})
        with self.assertRaises(intake.Refused):
            intake.compose(bad_hint, "0106", "0105", env={})

    def test_perimeter_and_perimeter_decided_with_intake_network_split(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            (run_dir / "out").mkdir(parents=True)
            (run_dir / "out" / "run.json").write_text(json.dumps({"harness": "adk", "model": "test-model"}), encoding="utf-8")
            (run_dir / "out" / "calls.json").write_text(
                json.dumps([
                    {
                        "name": "propose_perimeter",
                        "args": {"ticket": 1, "paths": ["src/service.py", "tests/test_service.py"]},
                    }
                ]),
                encoding="utf-8",
            )
            seen_off: list[str] = []
            res_off = intake.perimeter(
                self._doc(),
                str(run_dir),
                0,
                {"AGENT_HARNESS": "replay"},
                check=lambda md: (seen_off.append(md) or "GO: spec"),
            )
            self.assertNotIn("network", res_off["proposal"][0])
            self.assertNotIn("## Network", seen_off[0])

            seen_on: list[str] = []
            res_on = intake.perimeter(
                self._doc(),
                str(run_dir),
                0,
                {"AGENT_HARNESS": "replay", "INTAKE_NETWORK_SPLIT": "on"},
                check=lambda md: (seen_on.append(md) or "GO: spec"),
            )
            self.assertIn("network", res_on["proposal"][0])
            self.assertEqual([m["id"] for m in res_on["proposal"][0]["network"]], ["impl", "tests"])
            self.assertIn("## Network", seen_on[0])

        req = intake.perimeter_request(self._doc(), ["src/service.py", "tests/test_service.py"])
        ans = {
            "answers": {
                "t1-f1": {"type": "yes_no", "p_yes": 0.95, "confidence": 0.95},
                "t1-f2": {"type": "yes_no", "p_yes": 0.92, "confidence": 0.92},
            },
            "adapter": "stub-decide",
            "replayed": False,
            "provider": "stub",
            "model": "stub-decider",
            "tokens": {"input": 10, "output": 0},
        }
        seen_dec: list[str] = []
        dec_on = intake.perimeter_decided(
            self._doc(),
            req,
            ans,
            check=lambda md: (seen_dec.append(md) or "GO: spec"),
            threshold=0.7,
            env={"INTAKE_NETWORK_SPLIT": "on"},
        )
        self.assertIn("network", dec_on["proposal"][0])
        self.assertIn("## Network", seen_dec[0])


class ReleaseTrainLockTests(unittest.TestCase):
    def test_default_lock_path_and_timeout_env(self) -> None:
        env = {"RELEASE_RECORD": ".crew/sub/release.json"}
        self.assertEqual(releases.train_lock_path(env), os.path.join(".crew/sub", "release-train.lock"))
        self.assertEqual(
            releases.train_lock_path({**env, "RELEASE_TRAIN_LOCK": "/tmp/custom.lock"}),
            "/tmp/custom.lock",
        )
        self.assertEqual(releases.train_timeout_s({}), 900.0)
        self.assertEqual(releases.train_timeout_s({"RELEASE_TRAIN_TIMEOUT_S": "0.25"}), 0.25)

    def test_lock_contention_timeout_and_release_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "release-train.lock"
            env_fast = {"RELEASE_TRAIN_LOCK": str(lock_path), "RELEASE_TRAIN_TIMEOUT_S": "0.15"}
            env_wait = {"RELEASE_TRAIN_LOCK": str(lock_path), "RELEASE_TRAIN_TIMEOUT_S": "5"}

            with releases.ReleaseTrainLock(env=env_wait):
                # Same-thread re-entrancy works cleanly.
                with releases.ReleaseTrainLock(env=env_wait):
                    pass

                # Contending thread times out cleanly.
                errors: list[BaseException] = []

                def try_timeout() -> None:
                    try:
                        with releases.ReleaseTrainLock(env=env_fast, poll_s=0.02):
                            pass
                    except BaseException as exc:
                        errors.append(exc)

                t = threading.Thread(target=try_timeout)
                t.start()
                t.join(timeout=5)
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], releases.ReleaseTrainTimeout)

            # Another thread acquires after release, and record() succeeds under lock.
            env_rec = {
                "RELEASE_RECORD": str(Path(tmp) / "release.json"),
                "RELEASES_LOG": str(Path(tmp) / "releases.jsonl"),
                "CANARY_VERSION": "v2",
                "STABLE_VERSION": "v1",
                "RELEASE_TRAIN_LOCK": str(lock_path),
            }
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                rc = releases.main(["record", "held", "25", "[]", "img@sha"], env=env_rec)
            self.assertEqual(rc, 0)

    def test_train_subcommand_holds_lock_sets_reentrancy_and_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rec_path = Path(tmp) / "release.json"
            log_path = Path(tmp) / "releases.jsonl"
            env = {
                **os.environ,
                "PYTHONPATH": str(ROOT),
                "RELEASE_RECORD": str(rec_path),
                "RELEASES_LOG": str(log_path),
                "CANARY_VERSION": "v3",
                "STABLE_VERSION": "v2",
            }
            child_script = (
                "import os, sys\n"
                "from core import releases\n"
                "assert os.environ.get('LIGHTS_OUT_TRAIN_HELD') == '1'\n"
                "rc = releases.main(['record', 'held', '25', '[]', 'img@sha'])\n"
                "sys.exit(7 if rc == 0 else 9)\n"
            )
            rc = releases.main(["train", sys.executable, "-c", child_script], env=env)
            self.assertEqual(rc, 7)
            self.assertTrue(rec_path.is_file())
            self.assertEqual(json.loads(rec_path.read_text(encoding="utf-8"))["version"], "v3")


class ParallelRunnerTests(unittest.TestCase):
    def _setup_fleet_bus(self, tmp: Path, messages: list[str], step_script: str) -> tuple[Path, Path]:
        bus_dir = tmp / "adapters" / "stub-bus"
        bus_dir.mkdir(parents=True)
        (tmp / "core").mkdir(exist_ok=True)
        queue_file = tmp / "queue.txt"
        queue_file.write_text("".join(f"{m}\n" for m in messages), encoding="utf-8")
        events_log = tmp / "events.log"
        lock_py = (
            "import fcntl, sys\n"
            "with open(sys.argv[1], 'a+') as lf:\n"
            "    fcntl.flock(lf.fileno(), fcntl.LOCK_EX)\n"
        )
        (bus_dir / "pull.sh").write_text(
            f'#!/bin/sh\nset -eu\n'
            f'python3 -c "import fcntl, sys\nwith open(sys.argv[1], \'a+\') as lf:\n'
            f'  fcntl.flock(lf.fileno(), fcntl.LOCK_EX)\n'
            f'  with open(sys.argv[2], \'r+\') as qf:\n'
            f'    lines = qf.readlines()\n'
            f'    if lines:\n'
            f'      sys.stdout.write(lines[0])\n'
            f'      qf.seek(0); qf.truncate(); qf.writelines(lines[1:])\n'
            f'" "{tmp / "q.lock"}" "{queue_file}"\n',
            encoding="utf-8",
        )
        (bus_dir / "ack.sh").write_text(
            f'#!/bin/sh\nset -eu\nprintf "ack:%s\\n" "$1" >> "{events_log}"\n',
            encoding="utf-8",
        )
        (tmp / "core" / "tower.sh").write_text(step_script, encoding="utf-8")
        _ = lock_py
        return queue_file, events_log

    def test_workers_bounds_validation(self) -> None:
        self.assertEqual(runner.resolve_workers(None, {}), 1)
        self.assertEqual(runner.resolve_workers(None, {"RUNNER_WORKERS": "4"}), 4)
        self.assertEqual(runner.resolve_workers(3, {"RUNNER_WORKERS": "6"}), 3)
        for bad_cli in (0, -1, 9):
            with self.assertRaises(ValueError):
                runner.resolve_workers(bad_cli, {})
        for bad_env in ("0", "9", "abc"):
            with self.assertRaises(ValueError):
                runner.resolve_workers(None, {"RUNNER_WORKERS": bad_env})

    def test_parallel_workers_run_disjoint_tickets_concurrently_and_serialize_same_ticket(self) -> None:
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "trace.jsonl"
            step_script = f"""#!/bin/sh
set -eu
python3 - "$2" "$3" "{trace}" <<'PY'
import json, os, sys, time
t, s, out = sys.argv[1], sys.argv[2], sys.argv[3]
t0 = time.monotonic()
time.sleep(0.15)
t1 = time.monotonic()
with open(out, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"ticket": t, "step": s, "start": t0, "end": t1, "held": os.environ.get("LIGHTS_OUT_TRAIN_HELD")}}) + "\\n")
PY
"""
            self._setup_fleet_bus(
                root,
                [
                    'm1 {"ticket":"0001","step":"assign"}',
                    'm2 {"ticket":"0002","step":"assign"}',
                    'm3 {"ticket":"0003","step":"assign"}',
                    'm4 {"ticket":"0001","step":"ci"}',
                ],
                step_script,
            )
            os.chdir(root)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = runner.main(
                        ["--once", "--workers", "4", "--lockfile", ".crew/runner.lock"],
                        env={"EVENT_BUS": "stub-bus", "RELEASE_RECORD": ".crew/release.json"},
                    )
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0)
            entries = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(entries), 4)
            t1_entries = [e for e in entries if e["ticket"] == "0001"]
            self.assertEqual([e["step"] for e in t1_entries], ["assign", "ci"])
            self.assertLessEqual(t1_entries[0]["end"], t1_entries[1]["start"] + 0.001)
            # Disjoint tickets ran concurrently (execution intervals overlap in time).
            by_t = {e["ticket"]: e for e in entries if e["step"] == "assign"}
            self.assertLess(
                max(by_t["0001"]["start"], by_t["0002"]["start"], by_t["0003"]["start"]),
                min(by_t["0001"]["end"], by_t["0002"]["end"], by_t["0003"]["end"]),
            )

    def test_parallel_workers_serialize_merge_mainline_release_under_release_train_lock(self) -> None:
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            trace = root / "serial.jsonl"
            step_script = f"""#!/bin/sh
set -eu
python3 - "$2" "$3" "{trace}" <<'PY'
import json, os, sys, time
t, s, out = sys.argv[1], sys.argv[2], sys.argv[3]
t0 = time.monotonic()
time.sleep(0.08)
t1 = time.monotonic()
with open(out, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({{"ticket": t, "step": s, "start": t0, "end": t1, "held": os.environ.get("LIGHTS_OUT_TRAIN_HELD")}}) + "\\n")
PY
"""
            self._setup_fleet_bus(
                root,
                [
                    'm1 {"ticket":"0001","step":"merge"}',
                    'm2 {"ticket":"0002","step":"mainline"}',
                    'm3 {"ticket":"0003","step":"release"}',
                    'm4 {"ticket":"0004","step":"release"}',
                ],
                step_script,
            )
            os.chdir(root)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    rc = runner.main(
                        ["--once", "--workers", "4", "--lockfile", ".crew/runner.lock"],
                        env={"EVENT_BUS": "stub-bus", "RELEASE_RECORD": ".crew/release.json"},
                    )
            finally:
                os.chdir(cwd)
            self.assertEqual(rc, 0)
            entries = sorted(
                (json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()),
                key=lambda e: e["start"],
            )
            self.assertEqual(len(entries), 4)
            self.assertTrue(all(e["held"] == "1" for e in entries))
            for prev, nxt in zip(entries, entries[1:]):
                self.assertLessEqual(prev["end"], nxt["start"] + 0.002)


class M24RunnerTests(unittest.TestCase):
    def test_committed_m24_dataset_evaluates_incomplete_honestly(self) -> None:
        data = json.loads(M24_DATA.read_text(encoding="utf-8"))
        report = m24_runner.evaluate_dataset(data)
        self.assertEqual(report["verdict"], "incomplete")
        self.assertEqual(report["providers_run"], 2)
        self.assertEqual([n["provider"] for n in report["not_run"]], ["openai"])
        by_p = {p["provider"]: p for p in report["providers"]}
        self.assertEqual((by_p["anthropic"]["auto_merged"], by_p["anthropic"]["tickets"]), (10, 10))
        self.assertEqual((by_p["gemini"]["auto_merged"], by_p["gemini"]["tickets"]), (7, 10))
        self.assertTrue(by_p["anthropic"]["passed"])
        self.assertTrue(by_p["gemini"]["passed"])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = m24_runner.main(["evaluate", "--data", str(M24_DATA), "--json"])
        self.assertEqual(rc, m24_runner.EXIT_INCOMPLETE)
        self.assertEqual(json.loads(out.getvalue())["verdict"], "incomplete")

        with contextlib.redirect_stdout(io.StringIO()):
            rc_allow = m24_runner.main(["evaluate", "--data", str(M24_DATA), "--allow-incomplete"])
        self.assertEqual(rc_allow, 0)

    def test_evaluate_pass_and_fail_thresholds_and_human_edits(self) -> None:
        tickets = ["t-01", "t-02", "t-03"]
        pass_data = {
            "schema": scoreboard.SCHEMA,
            "scope": "3 tickets across 3 providers",
            "rows": [
                _dataset_row("t-01", "p1", True),
                _dataset_row("t-02", "p1", True),
                _dataset_row("t-03", "p1", True),
                _dataset_row("t-01", "p2", True),
                _dataset_row("t-02", "p2", True),
                _dataset_row("t-03", "p2", False),
                _dataset_row("t-01", "p3", True),
                _dataset_row("t-02", "p3", False),
                _dataset_row("t-03", "p3", True),
            ],
            "totals": [],
            "not_run": [],
        }
        rep_pass = m24_runner.evaluate_dataset(pass_data)
        self.assertEqual(rep_pass["verdict"], "pass")
        self.assertEqual(rep_pass["providers_passed"], 3)

        # Provider p3 has only 1/3 auto-mergeable -> fail
        fail_data = copy.deepcopy(pass_data)
        fail_data["rows"][-1] = _dataset_row("t-03", "p3", False)
        rep_fail = m24_runner.evaluate_dataset(fail_data)
        self.assertEqual(rep_fail["verdict"], "fail")
        self.assertIn("p3", rep_fail["reason"])

        # Human edits disqualify merged row from autonomous pass -> 1/3 -> fail
        human_data = copy.deepcopy(pass_data)
        human_data["rows"][-1] = _dataset_row("t-03", "p3", True, human_edits=True)
        rep_human = m24_runner.evaluate_dataset(human_data)
        self.assertEqual(rep_human["verdict"], "fail")

        # Partial provider coverage + unexecuted zai -> incomplete
        inc_data = copy.deepcopy(pass_data)
        inc_data["rows"] = [r for r in inc_data["rows"] if r["provider"] != "p3"]
        inc_data["not_run"] = [{"provider": "zai", "harness": "coding-agent", "why": "no key", "source": "local"}]
        rep_inc = m24_runner.evaluate_dataset(inc_data)
        self.assertEqual(rep_inc["verdict"], "incomplete")
        self.assertEqual(rep_inc["not_run"][0]["provider"], "zai")
        _ = tickets

    def test_ingest_logbook_merges_valid_runs_and_rejects_bad_provenance_or_replayed_only(self) -> None:
        live_book = {
            "schema": "lights-out/logbook/1.8",
            "source": {"commit": "deadbeef1234"},
            "tickets": [
                {
                    "id": "t-01",
                    "attempt": 1,
                    "tasks": [
                        {
                            "attempt": 1,
                            "role": "dev-worker",
                            "harness": "coding-agent",
                            "provider": "openai",
                            "model": "test-model",
                            "replayed": False,
                            "tokens": {"input": 100, "output": 20},
                        }
                    ],
                    "rework": [],
                    "ci": {"line": "GO: gates"},
                    "merge": {"sha": "abc123"},
                },
                {
                    "id": "t-02",
                    "attempt": 1,
                    "tasks": [
                        {
                            "attempt": 1,
                            "role": "dev-worker",
                            "harness": "coding-agent",
                            "provider": "openai",
                            "model": "test-model",
                            "replayed": False,
                            "tokens": {"input": 120, "output": 25},
                        }
                    ],
                    "rework": [],
                    "ci": {"line": "GO: gates"},
                    "merge": {"sha": "def456"},
                },
                {
                    "id": "t-03",
                    "attempt": 1,
                    "tasks": [
                        {
                            "attempt": 1,
                            "role": "dev-worker",
                            "harness": "coding-agent",
                            "provider": "openai",
                            "model": "test-model",
                            "replayed": False,
                            "tokens": {"input": 90, "output": 10},
                        }
                    ],
                    "rework": [],
                    "ci": {"line": "NO-GO: test — failed"},
                    "merge": None,
                },
            ],
            "spend": {"tickets": {"t-01": {"cost_usd": 0.02}}},
        }

        base_dataset = {
            "schema": scoreboard.SCHEMA,
            "scope": "3 tickets",
            "rows": [
                _dataset_row("t-01", "p1", True),
                _dataset_row("t-02", "p1", True),
                _dataset_row("t-03", "p1", True),
                _dataset_row("t-01", "p2", True),
                _dataset_row("t-02", "p2", True),
                _dataset_row("t-03", "p2", False),
            ],
            "totals": [],
            "not_run": [{"provider": "openai", "harness": "coding-agent", "why": "pending", "source": "plan"}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            book_path = tdir / "openai-book.json"
            data_path = tdir / "dataset.json"
            out_path = tdir / "merged.json"
            book_path.write_text(json.dumps(live_book), encoding="utf-8")
            data_path.write_text(json.dumps(base_dataset), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                rc = m24_runner.main([
                    "ingest-logbook",
                    "--logbook",
                    str(book_path),
                    "--data",
                    str(data_path),
                    "--out",
                    str(out_path),
                    "--handoff",
                    "docs/plans/live-m24.md §4",
                ])
            self.assertEqual(rc, 0)
            merged_ds = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(merged_ds["not_run"], [])
            self.assertEqual(len(merged_ds["rows"]), 9)
            self.assertEqual(m24_runner.evaluate_dataset(merged_ds)["verdict"], "pass")

            # Duplicate ingestion refused unless --replace
            with contextlib.redirect_stderr(io.StringIO()):
                rc_dup = m24_runner.main([
                    "ingest-logbook",
                    "--logbook",
                    str(book_path),
                    "--data",
                    str(out_path),
                ])
            self.assertEqual(rc_dup, 1)

            # Missing commit SHA refused
            no_commit = copy.deepcopy(live_book)
            no_commit["source"] = {}
            bad_book = tdir / "bad.json"
            bad_book.write_text(json.dumps(no_commit), encoding="utf-8")
            with self.assertRaises(scoreboard.Refused):
                m24_runner.ingest_logbooks([("bad.json", no_commit)])

            # Replayed-only logbook refused
            replayed_only = copy.deepcopy(live_book)
            for t in replayed_only["tickets"]:
                for task in t["tasks"]:
                    task["replayed"] = True
            with self.assertRaises(scoreboard.Refused):
                m24_runner.ingest_logbooks([("replay.json", replayed_only)])


if __name__ == "__main__":
    unittest.main()
