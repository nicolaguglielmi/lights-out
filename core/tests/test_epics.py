"""Specification tests for M79 (ADR 0119): Depends-On / Epic spec headers,
epic DAG validation & wave scheduling, tower dependency gating & main-green
auto-unblock, and multi-service monorepo workspace coordination.

Strictly stdlib-only, vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from core import epics, spec, tower


def _ticket_md(
    tid: str,
    title: str = "Task",
    *,
    feature: str | None = None,
    priority: str | None = None,
    epic: str | None = None,
    depends_on: str | None = None,
    perimeter: list[str] | None = None,
) -> str:
    lines = [f"# {tid} — {title}", "", "- Status: open"]
    if feature is not None:
        lines.append(f"- Feature: {feature}")
    if priority is not None:
        lines.append(f"- Priority: {priority}")
    if epic is not None:
        lines.append(f"- Epic: {epic}")
    if depends_on is not None:
        lines.append(f"- Depends-On: {depends_on}")
    lines += [
        "",
        "## Problem",
        "",
        "A concrete system capability is missing.",
        "",
        "## Acceptance criteria",
        "",
        "Given a repository state",
        "When the ticket change runs",
        "Then the output matches the target contract",
        "",
        "## Perimeter",
        "",
    ]
    for glob_pat in (perimeter if perimeter is not None else ["svc/main.py"]):
        lines.append(f"- `{glob_pat}`")
    return "\n".join(lines) + "\n"


class SpecDependenciesTests(unittest.TestCase):
    def test_absent_headers_preserve_fields_and_return_empty_deps(self) -> None:
        text = _ticket_md("0001", feature="auth", priority="P1")
        self.assertEqual(spec.fields(text), {"feature": "auth", "priority": "P1"})
        self.assertEqual(spec.dependencies(text), ([], None))
        self.assertEqual(spec.depends_on(text), [])
        self.assertIsNone(spec.epic(text))
        self.assertIsNone(spec.check(text))

    def test_valid_depends_on_and_epic_headers_parse_and_pass_check(self) -> None:
        text = _ticket_md(
            "0003",
            feature="checkout",
            priority="P0",
            epic="checkout-v2",
            depends_on="0001, 0002, 0001",
        )
        self.assertEqual(spec.fields(text), {"feature": "checkout", "priority": "P0"})
        deps, epic_id = spec.dependencies(text)
        self.assertEqual(deps, ["0001", "0002"])
        self.assertEqual(epic_id, "checkout-v2")
        self.assertEqual(spec.depends_on(text), ["0001", "0002"])
        self.assertEqual(spec.epic(text), "checkout-v2")
        self.assertIsNone(spec.check(text))

    def test_malformed_depends_on_and_self_dependency_rejected(self) -> None:
        bad_cases = [
            _ticket_md("0002", depends_on=""),
            _ticket_md("0002", depends_on="12"),
            _ticket_md("0002", depends_on="0001, "),
            _ticket_md("0002", depends_on="0001 0003"),
            _ticket_md("0002", depends_on="0002"),
            _ticket_md("0002", depends_on="0001, 0002"),
            _ticket_md("0002", depends_on="0001") + "",
        ]
        for text in bad_cases[:-1]:
            with self.subTest(text=text):
                self.assertIsNotNone(spec.check(text))
                with self.assertRaises(ValueError):
                    spec.dependencies(text)

        # Duplicate header line
        dup_dep = _ticket_md("0002", depends_on="0001").replace(
            "- Depends-On: 0001\n",
            "- Depends-On: 0001\n- Depends-On: 0003\n",
        )
        self.assertIn("2 times", spec.check(dup_dep))
        with self.assertRaises(ValueError):
            spec.dependencies(dup_dep)

        # Explicit own_id check even when heading has no numeric id
        draft_text = _ticket_md("draft", depends_on="0005")
        self.assertIsNone(spec.check(draft_text))
        self.assertIsNotNone(spec.check(draft_text, own_id="0005"))
        with self.assertRaises(ValueError):
            spec.dependencies(draft_text, own_id="0005")

    def test_malformed_epic_rejected_and_body_lines_ignored(self) -> None:
        for bad_epic in ("", "bad epic", "../escape", "-leading", "a--b"):
            text = _ticket_md("0002", epic=bad_epic)
            self.assertIn("Epic", spec.check(text), bad_epic)
            with self.assertRaises(ValueError):
                spec.dependencies(text)

        dup_epic = _ticket_md("0002", epic="e1").replace("- Epic: e1\n", "- Epic: e1\n- Epic: e2\n")
        self.assertIn("2 times", spec.check(dup_epic))

        # Lines inside ## Problem are not headers
        body_text = _ticket_md("0002").replace(
            "A concrete system capability is missing.",
            "- Depends-On: invalid\n- Epic: invalid space",
        )
        self.assertIsNone(spec.check(body_text))
        self.assertEqual(spec.dependencies(body_text), ([], None))

    def test_draft_supports_optional_epic_and_depends_on(self) -> None:
        title, body = spec.draft({
            "title": "Downstream service",
            "context": "Needs upstream schema.",
            "feature": "api",
            "priority": "P1",
            "epic": "epic-01",
            "depends_on": ["0001", "0002"],
            "criteria": [{"given": "schema is green", "when": "api boots", "then": "returns 200"}],
            "perimeter": ["svc/api/*"],
        })
        self.assertEqual(title, "Downstream service")
        full = f"# 0003 — {title}\n{body}"
        self.assertIsNone(spec.check(full))
        self.assertEqual(spec.dependencies(full), (["0001", "0002"], "epic-01"))


class DagAndWavesTests(unittest.TestCase):
    def test_compute_waves_diamond_dag(self) -> None:
        dag = {
            "0001": [],
            "0002": ["0001"],
            "0003": ["0001"],
            "0004": ["0002", "0003"],
        }
        self.assertEqual(epics.compute_waves(dag), [["0001"], ["0002", "0003"], ["0004"]])

    def test_compute_waves_refuses_unknown_dependency(self) -> None:
        with self.assertRaises(epics.Refused) as ctx:
            epics.compute_waves({"0001": [], "0002": ["9999"]})
        self.assertIn("9999", str(ctx.exception))

    def test_compute_waves_refuses_cycles_naming_exact_path(self) -> None:
        with self.assertRaises(epics.Refused) as ctx2:
            epics.compute_waves({"0001": ["0002"], "0002": ["0001"]})
        self.assertIn("0001 -> 0002 -> 0001", str(ctx2.exception))

        # Cycle with a non-cycle lead-in node 0000
        with self.assertRaises(epics.Refused) as ctx3:
            epics.compute_waves({
                "0000": ["0001"],
                "0001": ["0002"],
                "0002": ["0003"],
                "0003": ["0001"],
            })
        msg = str(ctx3.exception)
        self.assertIn("0001 -> 0002 -> 0003 -> 0001", msg)
        self.assertNotIn("0000", msg)

    def test_partition_disjoint_waves_serializes_overlaps(self) -> None:
        waves = [["0001", "0002", "0003"], ["0004"]]
        perimeters = {
            "0001": ["svc/api/routes.py"],
            "0002": ["svc/api/*.py"],  # overlaps 0001
            "0003": ["svc/web/*"],     # disjoint from 0001 & 0002
            "0004": ["svc/api/routes.py"],  # next topological wave stays separate
        }
        sub_waves = epics.partition_disjoint_waves(waves, perimeters)
        self.assertEqual(sub_waves, [["0001", "0003"], ["0002"], ["0004"]])


class EpicStoreAndUnblockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.tickets_dir = self.root / "tickets"
        self.state_dir = self.root / "state"
        self.tickets_dir.mkdir()
        self.state_dir.mkdir()
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _write_ticket(self, tid: str, **kwargs) -> None:
        (self.tickets_dir / f"{tid}.md").write_text(_ticket_md(tid, **kwargs), encoding="utf-8")

    def test_create_epic_isolates_epics_collection_and_validates_dag(self) -> None:
        self._write_ticket("0001", feature="db", epic="epic-01", perimeter=["db/001.sql"])
        self._write_ticket("0002", feature="api", epic="epic-01", depends_on="0001", perimeter=["svc/api/*"])
        self._write_ticket("0003", feature="ui", epic="epic-01", depends_on="0001", perimeter=["svc/web/*"])
        self._write_ticket("0004", feature="e2e", epic="epic-01", depends_on="0002, 0003", perimeter=["tests/*"])

        doc = epics.create_epic(
            "epic-01",
            "Full Stack Feature",
            str(self.tickets_dir),
            ["0001", "0002", "0003", "0004"],
            state_store_dir=str(self.state_dir),
        )
        self.assertEqual(doc["kind"], "epic")
        self.assertEqual(doc["state"], "open")
        self.assertEqual(doc["waves"], [["0001"], ["0002", "0003"], ["0004"]])
        self.assertEqual(doc["sub_waves"], [["0001"], ["0002", "0003"], ["0004"]])
        self.assertTrue((self.state_dir / "epics.epic-01.json").is_file())
        self.assertFalse((self.state_dir / "tickets.epic-01.json").exists())

        status = epics.epic_status("epic-01", state_store_dir=str(self.state_dir))
        self.assertEqual(status["progress"]["total"], 4)
        self.assertEqual(status["progress"]["completed"], 0)
        self.assertEqual(status["progress"]["blocked"], 3)

    def test_create_epic_refuses_mismatched_epic_cycle_or_unknown_dep(self) -> None:
        self._write_ticket("0001", epic="other-epic")
        with self.assertRaises(epics.Refused):
            epics.create_epic("epic-01", "Bad", str(self.tickets_dir), ["0001"], state_store_dir=str(self.state_dir))

        self._write_ticket("0001", epic="epic-01", depends_on="0002")
        self._write_ticket("0002", epic="epic-01", depends_on="0001")
        with self.assertRaises(epics.Refused) as ctx:
            epics.create_epic("epic-01", "Cycle", str(self.tickets_dir), ["0001", "0002"], state_store_dir=str(self.state_dir))
        self.assertIn("0001 -> 0002 -> 0001", str(ctx.exception))

        self._write_ticket("0003", epic="epic-01", depends_on="9999")
        with self.assertRaises(epics.Refused) as ctx_u:
            epics.create_epic("epic-01", "Unknown", str(self.tickets_dir), ["0003"], state_store_dir=str(self.state_dir))
        self.assertIn("9999", str(ctx_u.exception))

    def test_on_main_green_updates_blockers_respects_red_main_and_publishes_assign(self) -> None:
        # Setup stub adapters for EVENT_BUS + STATE_STORE
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        (self.root / "adapters" / "stub-bus").mkdir(parents=True)
        (self.root / "adapters" / "stub-state" / "get.sh").write_text(
            'set -eu\nf="state/$1.$2.json"\n[ -f "$f" ] || exit 1\ncat "$f"\n', encoding="utf-8"
        )
        (self.root / "adapters" / "stub-state" / "put.sh").write_text(
            'set -eu\ncat > "state/$1.$2.json"\n', encoding="utf-8"
        )
        (self.root / "adapters" / "stub-bus" / "publish.sh").write_text(
            'set -eu\nprintf "%s\\n" "$1" >> bus.log\n', encoding="utf-8"
        )
        env = {"STATE_STORE": "stub-state", "EVENT_BUS": "stub-bus", "TICKETS_DIR": str(self.tickets_dir)}

        self._write_ticket("0001", epic="epic-01", perimeter=["svc/a.py"])
        self._write_ticket("0002", epic="epic-01", perimeter=["svc/b.py"])
        self._write_ticket("0003", epic="epic-01", depends_on="0001, 0002", perimeter=["svc/c.py"])
        epics.create_epic("epic-01", "Fan-in", str(self.tickets_dir), ["0001", "0002", "0003"], env=env)

        (self.state_dir / "tickets.0001.json").write_text(json.dumps({"id": "0001", "state": "main-green", "epic": "epic-01"}))
        (self.state_dir / "tickets.0002.json").write_text(json.dumps({"id": "0002", "state": "pr-opened", "epic": "epic-01"}))
        (self.state_dir / "tickets.0003.json").write_text(json.dumps({
            "id": "0003",
            "state": "opened",
            "epic": "epic-01",
            "depends_on": ["0001", "0002"],
            "wait": {"kind": "depends_on", "blocking": ["0001", "0002"]},
        }))

        with contextlib.redirect_stderr(io.StringIO()):
            res1 = epics.on_main_green("0001", env=env, tickets_dir=str(self.tickets_dir))
        self.assertEqual(res1["published"], [])
        self.assertEqual(res1["still_blocked"], {"0003": ["0002"]})
        t3 = json.loads((self.state_dir / "tickets.0003.json").read_text())
        self.assertEqual(t3["wait"], {"kind": "depends_on", "blocking": ["0002"]})
        self.assertEqual(epics.epic_status("epic-01", env=env)["state"], "in-progress")

        # Now main is red -> 0002 lands main-green, but BLOCKED_BY_RED_MAIN suppresses assign publish
        (self.state_dir / "mainline.main.json").write_text(json.dumps({"state": "red", "line": "NO-GO"}))
        (self.state_dir / "tickets.0002.json").write_text(json.dumps({"id": "0002", "state": "main-green", "epic": "epic-01"}))
        with contextlib.redirect_stderr(io.StringIO()):
            res2 = epics.on_main_green("0002", env=env, tickets_dir=str(self.tickets_dir))
        self.assertEqual(res2["published"], [])
        self.assertEqual(res2["skipped_red_main"], ["0003"])
        self.assertFalse((self.root / "bus.log").exists())

        # Main turns green -> unblock publishes assign for 0003
        (self.state_dir / "mainline.main.json").write_text(json.dumps({"state": "green"}))
        with contextlib.redirect_stderr(io.StringIO()):
            res3 = epics.unblock(env=env, tickets_dir=str(self.tickets_dir))
        self.assertEqual(res3["published"], ["0003"])
        self.assertEqual(res3["unblocked"], ["0003"])
        t3_after = json.loads((self.state_dir / "tickets.0003.json").read_text())
        self.assertNotIn("wait", t3_after)
        self.assertEqual(
            [json.loads(line) for line in (self.root / "bus.log").read_text().splitlines()],
            [{"ticket": "0003", "step": "assign"}],
        )

        # Completing 0003 marks epic completed
        (self.state_dir / "tickets.0003.json").write_text(json.dumps({"id": "0003", "state": "main-green", "epic": "epic-01"}))
        with contextlib.redirect_stderr(io.StringIO()):
            epics.on_main_green("0003", env=env, tickets_dir=str(self.tickets_dir))
        final_epic = epics.epic_status("epic-01", env=env)
        self.assertEqual(final_epic["state"], "completed")
        self.assertEqual(final_epic["progress"]["completed"], 3)


class TowerDependencyIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._cwd = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "adapters" / "stub-state").mkdir(parents=True)
        (self.root / "adapters" / "stub-bus").mkdir(parents=True)
        (self.root / "state").mkdir()
        (self.root / "steps").mkdir()
        (self.root / "tickets").mkdir()
        (self.root / "adapters" / "stub-state" / "get.sh").write_text(
            'set -eu\nf="state/$1.$2.json"\n[ -f "$f" ] || exit 1\ncat "$f"\n', encoding="utf-8"
        )
        (self.root / "adapters" / "stub-state" / "put.sh").write_text(
            'set -eu\ncat > "state/$1.$2.json"\n', encoding="utf-8"
        )
        (self.root / "adapters" / "stub-bus" / "publish.sh").write_text(
            'set -eu\nprintf "%s\\n" "$1" >> bus.log\n', encoding="utf-8"
        )
        self.env = {
            "STATE_STORE": "stub-state",
            "EVENT_BUS": "stub-bus",
            "TOWER_STEPS": "steps",
            "TICKETS_DIR": "tickets",
        }
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def _run_tower(self, *argv: str) -> tuple[int, str]:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = tower.main(list(argv), dict(self.env))
        return code, err.getvalue()

    def test_open_request_spec_capture_depends_on_and_epic(self) -> None:
        (self.root / "tickets" / "0001.md").write_text(_ticket_md("0001", epic="epic-01"))
        (self.root / "tickets" / "0002.md").write_text(_ticket_md("0002", epic="epic-01", depends_on="0001"))
        (self.root / "tickets" / "0003.md").write_text(_ticket_md("0003", epic="epic-01", depends_on="0001"))

        self.assertEqual(self._run_tower("open", "0002")[0], 0)
        d2 = json.loads((self.root / "state" / "tickets.0002.json").read_text())
        self.assertEqual(d2["depends_on"], ["0001"])
        self.assertEqual(d2["epic"], "epic-01")

        self.assertEqual(self._run_tower("request", "0003")[0], 0)
        d3 = json.loads((self.root / "state" / "tickets.0003.json").read_text())
        self.assertEqual(d3["depends_on"], ["0001"])
        self.assertEqual(d3["epic"], "epic-01")

        # spec step transitioning spec-ready -> opened picks up Depends-On & Epic from ticket.md
        (self.root / "tickets" / "0004.md").write_text(_ticket_md("0004", epic="epic-01", depends_on="0001, 0002"))
        (self.root / "state" / "tickets.0004.json").write_text(json.dumps({
            "id": "0004", "state": "spec-ready", "history": []
        }))
        (self.root / "steps" / "spec.sh").write_text(
            '#!/bin/sh\necho \'{"state":"opened","spec":{"verdict":"GO"}}\'\n', encoding="utf-8"
        )
        self.assertEqual(self._run_tower("step", "0004", "spec")[0], 0)
        d4 = json.loads((self.root / "state" / "tickets.0004.json").read_text())
        self.assertEqual(d4["state"], "opened")
        self.assertEqual(d4["depends_on"], ["0001", "0002"])
        self.assertEqual(d4["epic"], "epic-01")

    def test_rolled_back_without_main_green_proof_keeps_downstream_blocked(self) -> None:
        (self.root / "state" / "tickets.0001.json").write_text(json.dumps({"id": "0001", "state": "rolled-back"}))
        (self.root / "state" / "tickets.0002.json").write_text(json.dumps({
            "id": "0002", "state": "opened", "depends_on": ["0001"], "history": []
        }))
        (self.root / "steps" / "assign.sh").write_text(
            '#!/bin/sh\ntouch ran\necho \'{"state":"pr-opened"}\'\n', encoding="utf-8"
        )
        code, err = self._run_tower("step", "0002", "assign")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "ran").exists())
        d2 = json.loads((self.root / "state" / "tickets.0002.json").read_text())
        self.assertEqual(d2["wait"], {"kind": "depends_on", "blocking": ["0001"]})

    def test_step_assign_blocks_idempotently_until_upstream_main_green(self) -> None:
        (self.root / "tickets" / "0001.md").write_text(_ticket_md("0001", epic="epic-01"))
        (self.root / "tickets" / "0002.md").write_text(_ticket_md("0002", epic="epic-01", depends_on="0001"))
        epics.create_epic("epic-01", "Sequential", "tickets", ["0001", "0002"], env=self.env)

        (self.root / "steps" / "assign.sh").write_text(
            '#!/bin/sh\necho "$TICKET" >> assign_ran.log\n'
            'echo \'{"state":"pr-opened","attempt":1}\'\n',
            encoding="utf-8",
        )
        (self.root / "steps" / "mainline.sh").write_text(
            '#!/bin/sh\necho \'{"state":"main-green","mainline":{"verdict":"GO","green":"g1"}}\'\n',
            encoding="utf-8",
        )

        self.assertEqual(self._run_tower("open", "0001")[0], 0)
        self.assertEqual(self._run_tower("open", "0002")[0], 0)
        (self.root / "bus.log").unlink()

        # Assign 0002 while 0001 is still opened -> blocked cleanly at opened, no failure recorded
        code1, err1 = self._run_tower("step", "0002", "assign")
        self.assertEqual(code1, 0, err1)
        self.assertIn("blocked by depends_on", err1)
        self.assertFalse((self.root / "assign_ran.log").exists())
        d2 = json.loads((self.root / "state" / "tickets.0002.json").read_text())
        self.assertEqual(d2["state"], "opened")
        self.assertEqual(d2["wait"], {"kind": "depends_on", "blocking": ["0001"]})
        self.assertEqual(len(d2["history"]), 1)  # no failed history entry

        # Idempotent re-delivery while still blocked
        code2, _ = self._run_tower("step", "0002", "assign")
        self.assertEqual(code2, 0)
        self.assertFalse((self.root / "assign_ran.log").exists())

        # Move 0001 to merged -> mainline -> main-green: tower invokes on_main_green and publishes assign for 0002
        d1 = json.loads((self.root / "state" / "tickets.0001.json").read_text())
        d1["state"] = "merged"
        (self.root / "state" / "tickets.0001.json").write_text(json.dumps(d1))
        code_m, err_m = self._run_tower("step", "0001", "mainline")
        self.assertEqual(code_m, 0, err_m)

        msgs = [json.loads(line) for line in (self.root / "bus.log").read_text().splitlines()]
        self.assertEqual(msgs[0], {"ticket": "0002", "step": "assign"})
        self.assertEqual(msgs[1], {"ticket": "0001", "step": "release"})

        # Now assign 0002 succeeds
        code3, err3 = self._run_tower("step", "0002", "assign")
        self.assertEqual(code3, 0, err3)
        self.assertEqual((self.root / "assign_ran.log").read_text().strip(), "0002")
        d2_final = json.loads((self.root / "state" / "tickets.0002.json").read_text())
        self.assertEqual(d2_final["state"], "pr-opened")
        self.assertNotIn("wait", d2_final)


class WorkspaceCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_files(self, mapping: dict[str, str]) -> Path:
        repo = self.root / "monorepo"
        for rel, body in mapping.items():
            path = repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        return repo

    def test_build_workspace_manifest_and_affected_services(self) -> None:
        repo = self._write_files({
            "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["services/*"]}),
            "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
            "shared/common.js": "export const VERSION = 1;\n",
            "docs/architecture.md": "# Architecture\n",
            "services/api/pyproject.toml": '[project]\nname = "api-svc"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
            "services/api/uv.lock": "version = 1\n",
            "services/api/api_svc/__init__.py": "def ok():\n    return True\n",
            "services/api/tests/test_api.py": "def test_ok():\n    assert True\n",
            "services/web/package.json": json.dumps({"name": "web-svc", "scripts": {"test": "node --test"}}),
            "services/web/src/index.js": "export const x = 1;\n",
            "services/web/test/index.test.js": "import test from 'node:test';\n",
        })
        out_dir = self.root / "ws-out"
        manifest = epics.build_workspace_manifest(
            str(repo),
            out_dir=str(out_dir),
            today="2026-11-24",
            baseline_mode="incremental",
        )
        self.assertEqual(manifest["schema"], "lights-out.workspace/1")
        self.assertEqual([s["id"] for s in manifest["services"]], ["services/api", "services/web"])
        self.assertEqual([s["stack"] for s in manifest["services"]], ["python", "javascript"])
        self.assertIn("shared/", manifest["shared_paths"])
        self.assertIn("pnpm-lock.yaml", manifest["shared_paths"])
        self.assertNotIn("docs/", manifest["shared_paths"])

        ws_file = out_dir / "workspace.json"
        self.assertTrue(ws_file.is_file())
        self.assertTrue((out_dir / "services" / "services" / "api" / "workload.env").is_file())
        self.assertTrue((out_dir / "services" / "services" / "web" / "workload.env").is_file())

        self.assertEqual(epics.affected_services(manifest, ["services/api/api_svc/__init__.py"]), ["services/api"])
        self.assertEqual(epics.affected_services(manifest, ["services/web/src/index.js"]), ["services/web"])
        self.assertEqual(epics.affected_services(manifest, ["shared/common.js"]), ["services/api", "services/web"])
        self.assertEqual(epics.affected_services(manifest, ["pnpm-lock.yaml"]), ["services/api", "services/web"])
        self.assertEqual(epics.affected_services(manifest, ["docs/architecture.md"]), [])

    def test_affected_services_propagates_service_dependencies(self) -> None:
        manifest = {
            "schema": "lights-out.workspace/1",
            "services": [
                {"id": "services/core", "path": "services/core", "depends_on": []},
                {"id": "services/api", "path": "services/api", "depends_on": ["services/core"]},
                {"id": "services/web", "path": "services/web", "depends_on": ["services/api"]},
            ],
            "shared_paths": ["shared/"],
        }
        self.assertEqual(
            epics.affected_services(manifest, ["services/core/lib.py"]),
            ["services/core", "services/api", "services/web"],
        )

    def test_cli_create_status_unblock_workspace(self) -> None:
        repo = self._write_files({
            "services/alpha/pyproject.toml": '[project]\nname = "alpha"\nversion = "0.1.0"\n',
            "services/alpha/uv.lock": "version = 1\n",
            "services/alpha/alpha/__init__.py": "def a():\n    return 1\n",
            "services/beta/pyproject.toml": '[project]\nname = "beta"\nversion = "0.1.0"\n',
            "services/beta/uv.lock": "version = 1\n",
            "services/beta/beta/__init__.py": "def b():\n    return 2\n",
            "shared/util.py": "VAL = 1\n",
        })
        tdir = self.root / "tickets"
        sdir = self.root / "state"
        tdir.mkdir()
        sdir.mkdir()
        (tdir / "0001.md").write_text(_ticket_md("0001", epic="e1"))
        (tdir / "0002.md").write_text(_ticket_md("0002", epic="e1", depends_on="0001"))

        out1 = io.StringIO()
        with contextlib.redirect_stdout(out1):
            rc1 = epics.main(["create", "--id", "e1", "--title", "E1", "--tickets", "0001,0002",
                              "--tickets-dir", str(tdir), "--state-dir", str(sdir), "--json"])
        self.assertEqual(rc1, 0)
        self.assertEqual(json.loads(out1.getvalue())["waves"], [["0001"], ["0002"]])

        out2 = io.StringIO()
        with contextlib.redirect_stdout(out2):
            rc2 = epics.main(["status", "e1", "--state-dir", str(sdir), "--json"])
        self.assertEqual(rc2, 0)
        self.assertEqual(json.loads(out2.getvalue())["id"], "e1")

        ws_out = self.root / "ws"
        out3 = io.StringIO()
        with contextlib.redirect_stdout(out3):
            rc3 = epics.main([
                "workspace", "--repo", str(repo), "--out", str(ws_out),
                "--changed", "shared/util.py", "--json",
            ])
        self.assertEqual(rc3, 0)
        ws_data = json.loads(out3.getvalue())
        self.assertEqual(ws_data["schema"], "lights-out.workspace/1")
        self.assertEqual(ws_data["affected_services"], ["services/alpha", "services/beta"])


if __name__ == "__main__":
    unittest.main()
