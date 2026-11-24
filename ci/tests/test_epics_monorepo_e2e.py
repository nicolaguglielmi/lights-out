"""End-to-end tests for M79 (ADR 0119): Multi-Ticket Epics, depends_on DAG,
automated main-green unblocking via adapters/inproc/run.sh + core/tower.sh,
gates/spec.sh header enforcement, and multi-service monorepo coordination.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from ci.checks import contract
from core import adr_gov, epics

ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = ROOT / "docs" / "adr" / "0119-epics-dag-and-multi-service-monorepos.md"
EPICS_SH = ROOT / "core" / "epics.sh"


def _ticket_md(
    tid: str,
    title: str,
    *,
    feature: str,
    epic: str | None = None,
    depends_on: str | None = None,
    perimeter: list[str],
) -> str:
    lines = [f"# {tid} — {title}", "", "- Status: open", f"- Feature: {feature}", "- Priority: P1"]
    if epic:
        lines.append(f"- Epic: {epic}")
    if depends_on:
        lines.append(f"- Depends-On: {depends_on}")
    lines += [
        "",
        "## Problem",
        "",
        f"Deliver {title}.",
        "",
        "## Acceptance criteria",
        "",
        "Given the prerequisite branch is merged",
        "When the module tests run",
        "Then every assertion returns exit 0",
        "",
        "## Perimeter",
        "",
    ]
    for pat in perimeter:
        lines.append(f"- `{pat}`")
    return "\n".join(lines) + "\n"


class EpicsGovernanceAndContractE2ETest(unittest.TestCase):
    def test_adr_0119_validates_with_zero_defects(self) -> None:
        text = ADR_PATH.read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(text), [])
        self.assertIn("## Provenance", text)

    def test_epics_sh_is_executable_and_within_shell_boundary(self) -> None:
        mode = EPICS_SH.stat().st_mode
        self.assertTrue(bool(mode & stat.S_IXUSR), "core/epics.sh must be +x")
        code_lines = [
            ln for ln in EPICS_SH.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25, code_lines)

    def test_gates_spec_enforces_depends_on_and_epic_headers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "0002.md"
            good.write_text(
                _ticket_md("0002", "API layer", feature="api", epic="epic-01", depends_on="0001", perimeter=["svc/api/*"]),
                encoding="utf-8",
            )
            res_go = subprocess.run(["sh", "gates/spec.sh", str(good)], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual((res_go.returncode, res_go.stdout), (0, "GO: spec\n"), res_go.stderr)
            self.assertIsNone(contract.shape("spec", res_go.returncode, res_go.stdout))

            bad_self = Path(tmp) / "0002-self.md"
            bad_self.write_text(
                _ticket_md("0002", "Self loop", feature="api", epic="epic-01", depends_on="0002", perimeter=["svc/api/*"]),
                encoding="utf-8",
            )
            res_nogo = subprocess.run(["sh", "gates/spec.sh", str(bad_self)], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(res_nogo.returncode, 1)
            self.assertTrue(res_nogo.stdout.startswith("NO-GO: spec — "), res_nogo.stdout)
            self.assertIsNone(contract.shape("spec", res_nogo.returncode, res_nogo.stdout))


class EpicsDagOrchestrationE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="epics-e2e-")
        self.root = Path(self._tmp.name)
        (self.root / "core").mkdir()
        for entry in (ROOT / "core").iterdir():
            if entry.name == "__pycache__":
                continue
            (self.root / "core" / entry.name).symlink_to(entry)
        (self.root / "policy").symlink_to(ROOT / "policy")
        (self.root / "adapters").mkdir()
        (self.root / "adapters" / "inproc").symlink_to(ROOT / "adapters" / "inproc")
        (self.root / "adapters" / "sqlite").symlink_to(ROOT / "adapters" / "sqlite")
        (self.root / "adapters" / "stub-bus").mkdir()
        (self.root / "bus").mkdir()
        (self.root / "steps").mkdir()
        (self.root / "tickets").mkdir()

        # Stub EventBus FIFO matching ci/tower.sh
        (self.root / "adapters" / "stub-bus" / "publish.sh").write_text(
            '#!/bin/sh\nset -eu\nd=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n'
            'n=$(cat "$d/bus/.seq" 2>/dev/null || printf 0); n=$((n + 1)); printf "%s" "$n" > "$d/bus/.seq"\n'
            'printf "%s\\n" "$1" > "$d/bus/$(printf "%06d" "$n").msg"\n',
            encoding="utf-8",
        )
        (self.root / "adapters" / "stub-bus" / "pull.sh").write_text(
            '#!/bin/sh\nset -eu\nd=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\n'
            'for f in "$d"/bus/*.msg; do [ -e "$f" ] || exit 0; printf "%s %s\\n" "$(basename "$f")" "$(cat "$f")"; exit 0; done\n',
            encoding="utf-8",
        )
        (self.root / "adapters" / "stub-bus" / "ack.sh").write_text(
            '#!/bin/sh\nset -eu\nd=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)\nrm -f "$d/bus/$1"\n',
            encoding="utf-8",
        )

        # Stub tower steps recording order of assignments
        (self.root / "steps" / "assign.sh").write_text(
            '#!/bin/sh\nset -eu\nprintf "%s\\n" "$TICKET" >> assign.order\n'
            'jq -cn --arg t "$TICKET" \'{state:"pr-opened",attempt:1,pr:{number:1,url:"stub://pr/1",base_sha:"b",head_sha:("h-"+$t)}}\'\n',
            encoding="utf-8",
        )
        (self.root / "steps" / "ci.sh").write_text(
            '#!/bin/sh\nset -eu\njq -cn --arg h "$HEAD_SHA" \'{state:"ci-passed",ci:{verdict:"GO",head_sha:$h}}\'\n',
            encoding="utf-8",
        )
        (self.root / "steps" / "merge.sh").write_text(
            '#!/bin/sh\nset -eu\njq -cn --arg h "$HEAD_SHA" \'{state:"merged",merge:{sha:("m-"+$h),head_sha:$h}}\'\n',
            encoding="utf-8",
        )
        (self.root / "steps" / "mainline.sh").write_text(
            '#!/bin/sh\nset -eu\nprintf "%s\\n" "$TICKET" >> green.order\n'
            'jq -cn --arg m "$(jq -r .merge.sha "$DOC_FILE")" \'{state:"main-green",mainline:{verdict:"GO",green:$m}}\'\n',
            encoding="utf-8",
        )
        (self.root / "steps" / "release.sh").write_text(
            '#!/bin/sh\nset -eu\necho \'{"state":"held","release":{"version":"0.1.0","verdict":"held"}}\'\n',
            encoding="utf-8",
        )

        self.env = {
            **os.environ,
            "STATE_STORE": "sqlite",
            "STATE_FILE": str(self.root / "state.sqlite"),
            "EVENT_BUS": "stub-bus",
            "TOWER_STEPS": str(self.root / "steps"),
            "TICKETS_DIR": str(self.root / "tickets"),
            "PYTHONPATH": str(self.root),
        }

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(argv, cwd=self.root, env=self.env, capture_output=True, text=True, check=False)

    def test_diamond_epic_executes_in_dag_order_and_updates_epic_status(self) -> None:
        (self.root / "tickets" / "0001.md").write_text(
            _ticket_md("0001", "Expand schema", feature="db", epic="epic-checkout", perimeter=["db/001.sql"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0002.md").write_text(
            _ticket_md("0002", "Backend endpoint", feature="api", epic="epic-checkout", depends_on="0001", perimeter=["svc/api/*"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0003.md").write_text(
            _ticket_md("0003", "Web UI", feature="web", epic="epic-checkout", depends_on="0001", perimeter=["svc/web/*"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0004.md").write_text(
            _ticket_md("0004", "Contract cleanup", feature="db", epic="epic-checkout", depends_on="0002, 0003", perimeter=["db/002.sql"]),
            encoding="utf-8",
        )

        cr = self._run(["sh", "core/epics.sh", "create", "--id", "epic-checkout", "--title", "Checkout Epic",
                        "--tickets", "0001,0002,0003,0004", "--json"])
        self.assertEqual(cr.returncode, 0, cr.stderr)
        created = json.loads(cr.stdout)
        self.assertEqual(created["waves"], [["0001"], ["0002", "0003"], ["0004"]])
        self.assertEqual(created["state"], "open")

        # Open downstream tickets BEFORE upstream so dependency gating is exercised immediately
        for tid in ("0004", "0003", "0002", "0001"):
            op = self._run(["sh", "core/tower.sh", "open", tid])
            self.assertEqual(op.returncode, 0, op.stderr)

        # Drain full inproc event bus: should resolve 0001 -> (0002, 0003) -> 0004 automatically
        drained = self._run(["sh", "adapters/inproc/run.sh"])
        self.assertEqual(drained.returncode, 0, drained.stderr)

        assign_order = (self.root / "assign.order").read_text(encoding="utf-8").splitlines()
        self.assertEqual(assign_order[0], "0001", assign_order)
        self.assertEqual(set(assign_order[1:3]), {"0002", "0003"}, assign_order)
        self.assertEqual(assign_order[3], "0004", assign_order)

        st = self._run(["sh", "core/epics.sh", "status", "epic-checkout", "--json"])
        self.assertEqual(st.returncode, 0, st.stderr)
        status = json.loads(st.stdout)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["progress"]["completed"], 4)
        self.assertEqual(status["progress"]["blocked"], 0)

    def test_cli_overlapping_subwaves_cycle_refusal_and_red_main_unblock_e2e(self) -> None:
        (self.root / "tickets" / "0001.md").write_text(
            _ticket_md("0001", "Base A", feature="core", epic="e2", perimeter=["svc/shared/*.py"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0002.md").write_text(
            _ticket_md("0002", "Base B", feature="core", epic="e2", perimeter=["svc/shared/mod.py"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0003.md").write_text(
            _ticket_md("0003", "Downstream", feature="api", epic="e2", depends_on="0001, 0002", perimeter=["svc/api/*"]),
            encoding="utf-8",
        )
        cr = self._run(["sh", "core/epics.sh", "create", "--id", "e2", "--title", "Overlap",
                        "--tickets", "0001,0002,0003", "--json"])
        self.assertEqual(cr.returncode, 0, cr.stderr)
        created = json.loads(cr.stdout)
        self.assertEqual(created["waves"], [["0001", "0002"], ["0003"]])
        self.assertEqual(created["sub_waves"], [["0001"], ["0002"], ["0003"]])

        # Cycle refusal through core/epics.sh
        (self.root / "tickets" / "0004.md").write_text(
            _ticket_md("0004", "Cyc A", feature="core", depends_on="0005", perimeter=["svc/x.py"]),
            encoding="utf-8",
        )
        (self.root / "tickets" / "0005.md").write_text(
            _ticket_md("0005", "Cyc B", feature="core", depends_on="0004", perimeter=["svc/y.py"]),
            encoding="utf-8",
        )
        bad = self._run(["sh", "core/epics.sh", "create", "--id", "cyc", "--title", "Cycle",
                         "--tickets", "0004,0005", "--json"])
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("0004 -> 0005 -> 0004", bad.stderr)

        # Red main suppresses unblock until main returns green
        for tid, st_name in (("0001", "main-green"), ("0002", "main-green")):
            subprocess.run(
                ["sh", "adapters/sqlite/put.sh", "tickets", tid],
                cwd=self.root,
                env=self.env,
                input=json.dumps({"id": tid, "state": st_name, "epic": "e2"}) + "\n",
                text=True,
                check=True,
            )
        subprocess.run(
            ["sh", "adapters/sqlite/put.sh", "tickets", "0003"],
            cwd=self.root,
            env=self.env,
            input=json.dumps({
                "id": "0003", "state": "opened", "epic": "e2",
                "depends_on": ["0001", "0002"],
                "wait": {"kind": "depends_on", "blocking": ["0001", "0002"]},
            }) + "\n",
            text=True,
            check=True,
        )
        subprocess.run(
            ["sh", "adapters/sqlite/put.sh", "mainline", "main"],
            cwd=self.root,
            env=self.env,
            input=json.dumps({"state": "red", "line": "NO-GO: test"}) + "\n",
            text=True,
            check=True,
        )
        ub_red = self._run(["sh", "core/epics.sh", "unblock", "0002", "--json"])
        self.assertEqual(ub_red.returncode, 0, ub_red.stderr)
        self.assertEqual(json.loads(ub_red.stdout)["skipped_red_main"], ["0003"])
        self.assertEqual(list((self.root / "bus").glob("*.msg")), [])

        subprocess.run(
            ["sh", "adapters/sqlite/put.sh", "mainline", "main"],
            cwd=self.root,
            env=self.env,
            input=json.dumps({"state": "green"}) + "\n",
            text=True,
            check=True,
        )
        ub_green = self._run(["sh", "core/epics.sh", "unblock", "--json"])
        self.assertEqual(ub_green.returncode, 0, ub_green.stderr)
        self.assertEqual(json.loads(ub_green.stdout)["published"], ["0003"])
        self.assertEqual(len(list((self.root / "bus").glob("*.msg"))), 1)


class MonorepoWorkspaceE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="epics-ws-e2e-")
        self.root = Path(self._tmp.name)
        self.repo = self.root / "mono"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, rel: str, text: str) -> None:
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_workspace_cli_builds_per_service_contracts_and_routes_shared_changes(self) -> None:
        self._write("package.json", json.dumps({"name": "acme-mono", "private": True, "workspaces": ["services/*"]}))
        self._write("pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        self._write("shared/schema.py", "SCHEMA = 1\n")
        self._write("docs/README.md", "# Monorepo\n")

        self._write("services/billing/pyproject.toml", '[project]\nname = "billing"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n')
        self._write("services/billing/uv.lock", "version = 1\n")
        self._write("services/billing/billing/__init__.py", "def charge():\n    return 1\n")
        self._write("services/billing/tests/test_billing.py", "def test_charge():\n    assert True\n")

        self._write("services/gateway/package.json", json.dumps({"name": "gateway", "scripts": {"test": "node --test"}}))
        self._write("services/gateway/src/index.js", "export const ok = true;\n")
        self._write("services/gateway/test/index.test.js", "import test from 'node:test';\n")

        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "commit.gpgsign=false",
             "commit", "-q", "-m", "init"],
            check=True,
        )

        out_dir = self.root / "workspace-out"
        proc = subprocess.run(
            ["sh", "core/epics.sh", "workspace", "--repo", str(self.repo), "--out", str(out_dir), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        manifest_path = out_dir / "workspace.json"
        self.assertTrue(manifest_path.is_file())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema"], "lights-out.workspace/1")
        self.assertEqual([s["id"] for s in manifest["services"]], ["services/billing", "services/gateway"])
        self.assertTrue((out_dir / "services" / "services" / "billing" / "workload.env").is_file())
        self.assertTrue((out_dir / "services" / "services" / "gateway" / "workload.env").is_file())

        self.assertEqual(epics.affected_services(manifest, ["services/billing/billing/__init__.py"]), ["services/billing"])
        self.assertEqual(epics.affected_services(manifest, ["services/gateway/src/index.js"]), ["services/gateway"])
        self.assertEqual(epics.affected_services(manifest, ["shared/schema.py"]), ["services/billing", "services/gateway"])
        self.assertEqual(epics.affected_services(manifest, ["docs/README.md"]), [])


if __name__ == "__main__":
    unittest.main()
