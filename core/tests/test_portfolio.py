# SPDX-License-Identifier: Apache-2.0
"""Unit tests for core/portfolio.py, core/portfolio.sh, and core/contract_spec.py (docs/adr/0138).

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh). Uses synthetic repo names only.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import contract_spec, portfolio

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIRNAME = "." + "lights" + "-out"


def _code_lines(path: Path) -> list[str]:
    return [
        ln
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _base_workload_env(**overrides: str | None) -> str:
    defaults = {
        "CANARY_VERSION": "0.2.0-canary",
        "MUTATION_BASELINE": "0.85",
        "WORKLOAD_KILL_SWITCH": "kill-switch",
        "WORKLOAD_SAMPLE_PATH": "/ping",
        "WORKLOAD_LOCKFILES": "uv.lock",
        "WORKLOAD_MUTATION_SCOPE": "src/",
        "WORKLOAD_TEST_ROOT": "",
        "WORKLOAD_TEST_PATHS": "tests/test_*.py",
        "TOOLCHAIN_IMAGE": "workbench",
        "TEST_CMD": "pytest -q",
        "SAST_CMD": "bandit -r src -q",
        "MUTATION_CMD": "sh runners/mutation.sh src",
    }
    for k, v in overrides.items():
        if v is None:
            defaults.pop(k, None)
        else:
            defaults[k] = v
    return "\n".join(f"{k}={v}" for k, v in defaults.items()) + "\n"


def _write_repo_contract(repo: Path, text: str, *, use_crew_attached: bool = False) -> Path:
    if use_crew_attached:
        cdir = repo / ".crew" / "attached" / CONTRACT_DIRNAME
    else:
        cdir = repo / CONTRACT_DIRNAME
    cdir.mkdir(parents=True, exist_ok=True)
    path = cdir / "workload.env"
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot_tree(root: Path) -> list[tuple[str, int, bytes]]:
    snap: list[tuple[str, int, bytes]] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_dir():
            snap.append((rel, -1, b""))
        else:
            st = p.stat()
            snap.append((rel, st.st_mtime_ns, p.read_bytes()))
    return snap


class ContractSpecTests(unittest.TestCase):
    """Validate Contract v1.0 rules and backward compatibility."""

    def test_v1_constants_and_backward_compatibility(self) -> None:
        self.assertEqual(contract_spec.CONTRACT_VERSION, "1.0")
        self.assertEqual(len(contract_spec.REQUIRED_WORKLOAD_KEYS), 12)
        self.assertEqual(
            set(contract_spec.OPTIONAL_WORKLOAD_KEYS),
            {"CONTRACT_VERSION", "WORKLOAD_HEALTH_PROBE", "WORKLOAD_PORT"},
        )

        omitted = contract_spec.validate_workload_env_text(_base_workload_env())
        self.assertTrue(omitted["valid"], omitted["errors"])
        self.assertEqual(omitted["contract_version"], "1.0")
        self.assertFalse(omitted["version_explicit"])

        explicit = contract_spec.validate_workload_env_text(
            _base_workload_env(
                CONTRACT_VERSION="1.0",
                WORKLOAD_PORT="8080",
                WORKLOAD_HEALTH_PROBE="http:/healthz",
            )
        )
        self.assertTrue(explicit["valid"], explicit["errors"])
        self.assertEqual(explicit["contract_version"], "1.0")
        self.assertTrue(explicit["version_explicit"])

    def test_rejects_bad_major_version_keys_and_toolchains(self) -> None:
        for bad_ver in ("2.0", "0.9", "", "v1.0", "abc"):
            res = contract_spec.validate_workload_env_text(_base_workload_env(CONTRACT_VERSION=bad_ver))
            self.assertFalse(res["valid"], bad_ver)
            self.assertTrue(any("CONTRACT_VERSION" in e for e in res["errors"]), res["errors"])

        missing = contract_spec.validate_workload_env_text(_base_workload_env(TEST_CMD=None))
        self.assertFalse(missing["valid"])
        self.assertTrue(any("TEST_CMD" in e for e in missing["errors"]))

        extra = contract_spec.validate_workload_env_text(_base_workload_env() + "EXTRA_FIELD=1\n")
        self.assertFalse(extra["valid"])
        self.assertTrue(any("EXTRA_FIELD" in e for e in extra["errors"]))

        for tc, want_form in (
            ("workbench", "workbench"),
            ("compose:backend-test", "compose"),
            ("docker.io/library/python:3.13", "image"),
            ("ghcr.io/org/tool:1.0@sha256:" + ("a" * 64), "image"),
        ):
            self.assertEqual(contract_spec.classify_toolchain_image(tc), want_form)
        for bad_tc in ("", "compose:", "compose:bad service", "Not An Image"):
            self.assertIsNone(contract_spec.classify_toolchain_image(bad_tc))


class PortfolioAggregationTests(unittest.TestCase):
    """Multi-repo portfolio aggregation, unread handling, DORA, and read-only safety."""

    def test_multi_repo_aggregation_dora_mainline_and_unread_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            alpha = base / "repo-alpha"
            beta = base / "repo-beta"
            svc = base / "svc-api"
            for d in (alpha, beta, svc):
                (d / ".crew").mkdir(parents=True)

            _write_repo_contract(
                alpha,
                _base_workload_env(
                    CONTRACT_VERSION="1.0",
                    WORKLOAD_PORT="8080",
                    WORKLOAD_HEALTH_PROBE="http:/healthz",
                ),
            )
            (alpha / ".crew" / "logbook.json").write_text(
                json.dumps(
                    {
                        "schema": "lights-out/logbook/1.8",
                        "tickets": [
                            {
                                "id": "0001",
                                "state": "rolled-back",
                                "priority": "P1",
                                "outcome": {"verdict": "NO-GO", "judge": "verify"},
                            },
                            {
                                "id": "0002",
                                "state": "held",
                                "priority": "P1",
                                "outcome": {"verdict": "GO", "judge": "human"},
                            },
                            {
                                "id": "0003",
                                "state": "needs-human",
                                "priority": "P1",
                                "updated_at": "2026-11-24T10:00:00Z",
                                "outcome": {"verdict": "needs-human", "judge": "review", "line": "cap reached"},
                            },
                            {
                                "id": "0004",
                                "state": "questions",
                                "priority": "P2",
                                "updated_at": "2026-11-24T11:00:00Z",
                                "questions": [{"from": "product", "question": "Confirm timeout?", "asked_at": "2026-11-24T11:00:00Z"}],
                                "outcome": {"verdict": "pending", "judge": None},
                            },
                        ],
                        "releases": [{"version": "0.4.0", "verdict": "held", "ticket": None, "answer": None}],
                        "launch": {
                            "pressable": True,
                            "record": {
                                "version": "0.4.0",
                                "held_at": 25,
                                "verdict": "held",
                                "stages": [{"pct": 25, "verdict": "GO", "judged_at": "2026-11-24T09:00:00Z"}],
                            },
                        },
                        "spend": {
                            "total": {"cost_usd": 3.5, "runs": 4, "unknown": 0},
                            "burn": {"day": "2026-11-24", "spent_usd": 1.25},
                        },
                        "dora": {
                            "schema": "dora/1",
                            "window_days": 30,
                            "measures": {
                                "deployment_frequency": {"measured": True, "count": 3, "per_day": 0.1},
                                "lead_time": {"measured": True, "median_s": 3600, "n": 3},
                                "change_fail_rate": {"measured": False, "why": "not measured"},
                                "recovery_time": {"measured": False, "why": "not measured"},
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            # repo-beta uses .crew/attached contract fallback, omitted dora -> .crew/dora.json,
            # and red mainline in .crew/state.sqlite.
            _write_repo_contract(beta, _base_workload_env(), use_crew_attached=True)
            (beta / ".crew" / "logbook.json").write_text(
                json.dumps(
                    {
                        "schema": "lights-out/logbook/1.8",
                        "tickets": [
                            {
                                "id": "0010",
                                "state": "opened",
                                "priority": "P0",
                                "updated_at": "2026-11-24T08:00:00Z",
                                "outcome": {"verdict": "stranded", "judge": "step", "line": "step exited 1"},
                            }
                        ],
                        "releases": [],
                        "launch": {"record": None},
                        "spend": {
                            "total": {"cost_usd": 2.0, "runs": 2, "unknown": 0},
                            "burn": {"day": "2026-11-24", "spent_usd": 0.75},
                        },
                    }
                ),
                encoding="utf-8",
            )
            (beta / ".crew" / "dora.json").write_text(
                json.dumps(
                    {
                        "schema": "dora/1",
                        "window_days": 30,
                        "measures": {
                            "deployment_frequency": {"measured": True, "count": 6, "per_day": 0.2},
                            "lead_time": {"measured": False, "why": "no ticket link"},
                            "change_fail_rate": {"measured": True, "rate": 0.25, "failed": 1, "decided": 4},
                            "recovery_time": {"measured": False, "why": "no recovery sample"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            db = sqlite3.connect(str(beta / ".crew" / "state.sqlite"))
            db.execute("CREATE TABLE documents (collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (collection, id))")
            db.execute(
                "INSERT INTO documents VALUES ('mainline', 'main', ?)",
                (json.dumps({"state": "red", "line": "NO-GO: test failed"}),),
            )
            db.commit()
            db.close()

            # svc-api has broken contract (v2.0) and missing logbook.
            _write_repo_contract(svc, _base_workload_env(CONTRACT_VERSION="2.0", TOOLCHAIN_IMAGE="Bad Image"))

            snap_alpha = _snapshot_tree(alpha)
            snap_beta = _snapshot_tree(beta)
            snap_svc = _snapshot_tree(svc)

            doc = portfolio.collect([alpha, beta, svc])

            # Strictly read-only verification.
            self.assertEqual(_snapshot_tree(alpha), snap_alpha)
            self.assertEqual(_snapshot_tree(beta), snap_beta)
            self.assertEqual(_snapshot_tree(svc), snap_svc)

            self.assertEqual(doc["schema"], "lights-out/portfolio/1.0")
            self.assertEqual(doc["repo_count"], 3)
            self.assertEqual(doc["healthy_repos"], 1)
            self.assertEqual(doc["unread_repos"], 1)
            self.assertEqual(doc["held_canaries_total"], 1)
            self.assertEqual(doc["your_turn_total"], 4)
            self.assertEqual(doc["mainline_red_repos"], 1)
            self.assertEqual(doc["contract_drift_repos"], 1)
            self.assertEqual(doc["spend_day_usd_total"], 2.0)
            self.assertEqual(doc["spend_total_usd_total"], 5.5)

            by_name = {r["name"]: r for r in doc["repos"]}
            r_alpha = by_name["repo-alpha"]
            self.assertEqual(r_alpha["status"], "ok")
            self.assertTrue(r_alpha["contract_valid"])
            self.assertEqual(r_alpha["contract_version"], "1.0")
            self.assertEqual(r_alpha["open_tickets"], 2)
            self.assertEqual(r_alpha["held_canaries"], 1)
            self.assertEqual(r_alpha["your_turn_count"], 3)
            self.assertFalse(r_alpha["mainline_red"])
            self.assertEqual(r_alpha["spend_day_usd"], 1.25)
            self.assertEqual(r_alpha["spend_total_usd"], 3.5)
            self.assertEqual(set(r_alpha["dora"].keys()), {"deployment_frequency", "lead_time"})

            r_beta = by_name["repo-beta"]
            self.assertEqual(r_beta["status"], "ok")
            self.assertTrue(r_beta["contract_valid"])
            self.assertEqual(r_beta["contract_version"], "1.0")
            self.assertEqual(r_beta["open_tickets"], 1)
            self.assertEqual(r_beta["held_canaries"], 0)
            self.assertEqual(r_beta["your_turn_count"], 1)
            self.assertTrue(r_beta["mainline_red"])
            self.assertEqual(set(r_beta["dora"].keys()), {"deployment_frequency", "change_fail_rate"})

            r_svc = by_name["svc-api"]
            self.assertEqual(r_svc["status"], "unread")
            self.assertIn("missing logbook", r_svc["error"])
            self.assertFalse(r_svc["contract_valid"])
            self.assertIsNone(r_svc["open_tickets"])
            self.assertIsNone(r_svc["held_canaries"])
            self.assertIsNone(r_svc["your_turn_count"])
            self.assertIsNone(r_svc["spend_day_usd"])
            self.assertIsNone(r_svc["spend_total_usd"])
            self.assertEqual(r_svc["dora"], {})

            # Fleet DORA averages ONLY over measured repos.
            fleet_dora = doc["dora"]
            self.assertEqual(fleet_dora["deployment_frequency"]["repos_measured"], 2)
            self.assertAlmostEqual(fleet_dora["deployment_frequency"]["mean_per_day"], 0.15, places=6)
            self.assertEqual(fleet_dora["deployment_frequency"]["total_count"], 9)
            self.assertEqual(fleet_dora["lead_time"]["repos_measured"], 1)
            self.assertEqual(fleet_dora["lead_time"]["mean_median_s"], 3600.0)
            self.assertEqual(fleet_dora["change_fail_rate"]["repos_measured"], 1)
            self.assertEqual(fleet_dora["change_fail_rate"]["mean_rate"], 0.25)
            self.assertEqual(fleet_dora["recovery_time"], {"measured": False, "repos_measured": 0})

            # Queue ordering: held canary -> needs-human -> stranded -> ticket-question.
            kinds = [item["kind"] for item in doc["your_turn_queue"]]
            self.assertEqual(kinds, ["go-for-launch", "needs-human", "stranded", "ticket-question"])

    def test_corrupt_logbook_and_missing_repo_are_unread_never_fabricated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            bad_json = base / "repo-bad-json"
            (bad_json / ".crew").mkdir(parents=True)
            _write_repo_contract(bad_json, _base_workload_env())
            (bad_json / ".crew" / "logbook.json").write_text("{broken json", encoding="utf-8")

            bad_schema = base / "repo-bad-schema"
            (bad_schema / ".crew").mkdir(parents=True)
            _write_repo_contract(bad_schema, _base_workload_env())
            (bad_schema / ".crew" / "logbook.json").write_text(
                json.dumps({"schema": "lights-out/logbook/2.0", "tickets": []}),
                encoding="utf-8",
            )

            missing = base / "repo-missing"

            doc = portfolio.collect([bad_json, bad_schema, missing])
            self.assertEqual(doc["repo_count"], 3)
            self.assertEqual(doc["unread_repos"], 3)
            self.assertEqual(doc["healthy_repos"], 0)
            self.assertEqual(doc["contract_drift_repos"], 1)
            for row in doc["repos"]:
                self.assertEqual(row["status"], "unread")
                self.assertTrue(row["error"])
                self.assertIsNone(row["open_tickets"])
                self.assertIsNone(row["spend_total_usd"])
                self.assertEqual(row["dora"], {})

    def test_resolve_repo_paths_supports_args_env_and_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            a = base / "repo-a"
            b = base / "repo-b"
            a.mkdir()
            b.mkdir()
            resolved = portfolio.resolve_repo_paths([f"{a},{b}", str(a)])
            self.assertEqual(resolved, [os.path.abspath(a), os.path.abspath(b)])

            from_env = portfolio.resolve_repo_paths(env={"PORTFOLIO_REPOS": f"{a},{b}"})
            self.assertEqual(from_env, [os.path.abspath(a), os.path.abspath(b)])

            default_dot = portfolio.resolve_repo_paths(env={})
            self.assertEqual(default_dot, [os.path.abspath(".")])


class PortfolioWrapperTests(unittest.TestCase):
    """Verify core/portfolio.sh wrapper contract, line cap, shellcheck, and JSON modes."""

    def test_wrapper_budget_and_shellcheck(self) -> None:
        wrapper = ROOT / "core" / "portfolio.sh"
        self.assertTrue(wrapper.is_file())
        code = _code_lines(wrapper)
        self.assertLessEqual(len(code), 25, f"core/portfolio.sh has {len(code)} code lines > 25")
        text = wrapper.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^name=portfolio$")
        self.assertRegex(text, r"(?m)^\. ci/lib\.sh$")
        if shutil.which("shellcheck"):
            proc = subprocess.run(
                ["shellcheck", "-s", "sh", "ci/lib.sh", "core/portfolio.sh"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_wrapper_one_line_contract_and_json_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            r1 = base / "repo-alpha"
            r2 = base / "repo-beta"
            (r1 / ".crew").mkdir(parents=True)
            (r2 / ".crew").mkdir(parents=True)
            _write_repo_contract(r1, _base_workload_env())
            _write_repo_contract(r2, _base_workload_env())
            (r1 / ".crew" / "logbook.json").write_text(
                json.dumps(
                    {
                        "schema": "lights-out/logbook/1.8",
                        "tickets": [],
                        "releases": [],
                        "launch": {"record": {"version": "0.2.0", "verdict": "held", "held_at": 25}},
                        "spend": {"total": {"cost_usd": 1.0, "runs": 1, "unknown": 0}, "burn": {"spent_usd": 1.0}},
                    }
                ),
                encoding="utf-8",
            )
            (r2 / ".crew" / "logbook.json").write_text(
                json.dumps({"schema": "lights-out/logbook/1.8", "tickets": [], "releases": [], "launch": {"record": None}}),
                encoding="utf-8",
            )

            # Default invocation with no args scans '.' and prints 1-line GO contract.
            cold = subprocess.run(
                ["sh", "core/portfolio.sh"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cold.returncode, 0, cold.stdout + cold.stderr)
            self.assertRegex(cold.stdout, r"^GO: portfolio — 1 repos, \d+ held, \d+ your-turn\n$")

            # Explicit --repos emits exact counts.
            one_line = subprocess.run(
                ["sh", "core/portfolio.sh", "--repos", f"{r1},{r2}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(one_line.returncode, 0, one_line.stdout + one_line.stderr)
            self.assertEqual(one_line.stdout, "GO: portfolio — 2 repos, 1 held, 1 your-turn\n")
            self.assertIn("repo-alpha", one_line.stderr)

            # --json and JSON=1 via LIGHTS_OUT_CLI_ARGS emit portfolio/1.0 JSON.
            json_proc = subprocess.run(
                ["sh", "core/portfolio.sh", "--json", "--repos", f"{r1},{r2}"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(json_proc.returncode, 0, json_proc.stdout + json_proc.stderr)
            parsed = json.loads(json_proc.stdout)
            self.assertEqual(parsed["schema"], "lights-out/portfolio/1.0")
            self.assertEqual(parsed["repo_count"], 2)
            self.assertEqual(parsed["held_canaries_total"], 1)

            cli_proc = subprocess.run(
                ["sh", "core/portfolio.sh"],
                cwd=ROOT,
                env={**os.environ, "LIGHTS_OUT_CLI_ARGS": f"--json\x1f--repo\x1f{r1}", "JSON": "1"},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(cli_proc.returncode, 0, cli_proc.stdout + cli_proc.stderr)
            parsed_cli = json.loads(cli_proc.stdout)
            self.assertEqual(parsed_cli["repo_count"], 1)


if __name__ == "__main__":
    unittest.main()
