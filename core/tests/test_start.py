"""Unit tests for core/start.py (ADR 0103).

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh): uses synthetic provider metadata,
synthetic adapters, and temporary workspace trees.
"""

from __future__ import annotations

import json
import os
import pty
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
START_PY = ROOT / "core" / "start.py"


SYNTH_PROVIDERS = {
    "about": "Synthetic providers for unit tests.",
    "providers": {
        "replay": {
            "label": "Local replay (no key)",
            "harnesses": ["replay"],
            "default_harness": "replay",
            "env_var": "",
            "default_model": "",
            "default_profile": "l0",
            "decide": "replay",
            "budget_guidance": "Zero token spend; replays recorded responses.",
        },
        "alpha": {
            "label": "Alpha AI",
            "harnesses": ["alpha-cli", "adk"],
            "default_harness": "alpha-cli",
            "env_var": "SYNTH_ALPHA_TOKEN",
            "default_model": "alpha-fast",
            "default_profile": "l1",
            "decide": "structured",
            "budget_guidance": "Set per-ticket and daily USD caps in policy/rework.env.",
        },
        "beta": {
            "label": "Beta AI",
            "harnesses": ["beta-cli"],
            "default_harness": "beta-cli",
            "env_var": "SYNTH_BETA_CREDENTIAL",
            "default_model": "beta-reason",
            "default_profile": "l1",
            "decide": "structured",
            "budget_guidance": "Paid per run; cap attempts in policy/rework.env.",
        },
    },
}


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _make_workspace(tmp: Path, *, with_contract: bool = True, baseline: str = "0.80") -> dict[str, Path]:
    """Create an isolated synthetic workspace tree matching core/start.py expectations."""
    (tmp / "profiles").mkdir(parents=True, exist_ok=True)
    _write(tmp / "profiles" / "providers.json", json.dumps(SYNTH_PROVIDERS, indent=2))
    _write(
        tmp / "profiles" / "l0.env",
        "LEVEL=l0\nMODEL_PROVIDER=\nMODEL=\nAGENT_HARNESS=replay\nDELIVERY=local-proxy\n",
    )
    _write(
        tmp / "profiles" / "l1.env",
        "LEVEL=l1\nMODEL_PROVIDER=alpha\nMODEL=alpha-fast\nAGENT_HARNESS=alpha-cli\nDELIVERY=local-proxy\n",
    )
    _write(
        tmp / "policy" / "rework.env",
        "REWORK_MAX_ATTEMPTS=4\nSPEND_BUDGET_TICKET_USD=\nSPEND_BUDGET_DAY_USD=\n",
    )
    _write(
        tmp / "policy" / "merge.env",
        "ERROR_RATE_MAX_DELTA=0.01\nLATENCY_P95_MAX_DELTA=0.25\n",
    )
    for harness in ("replay", "alpha-cli", "beta-cli", "adk"):
        _write(tmp / "adapters" / harness / "run.sh", "#!/bin/sh\nexit 0\n", executable=True)
    _write(tmp / "adapters" / "local-proxy" / "deploy.sh", "#!/bin/sh\nexit 0\n", executable=True)
    _write(tmp / "plugins" / "cloud" / "cloud-ship" / "deploy.sh", "#!/bin/sh\nexit 0\n", executable=True)
    _write(
        tmp / "core" / "doctor.sh",
        "#!/bin/sh\nprintf 'doctor: ok\\n' >&2\nprintf 'GO: doctor\\n'\nexit 0\n",
        executable=True,
    )

    contract = tmp / "target" / "contract"
    if with_contract:
        _write(
            contract / "workload.env",
            f"TOOLCHAIN_IMAGE=workbench\nMUTATION_BASELINE={baseline}\n"
            "WORKLOAD_SAMPLE_PATH=/ok\nWORKLOAD_HEALTH_PROBE=http:/ok\n",
        )
        _write(contract / "compose.yaml", "services:\n  backend:\n    image: example/app:latest\n")
        _write(contract / "policy.env", "ERROR_RATE_MAX_DELTA=0.01\n")
        _write(contract / "mcp.json", '{"servers":{}}\n')
        _write(contract / "roles" / "test-reviewer.md", "# Extra checks\n")
    tickets_dir = tmp / "tickets"
    tickets_dir.mkdir(parents=True, exist_ok=True)
    return {"root": tmp, "contract": contract, "tickets": tickets_dir}


def _write_state(db_path: Path, docs: list[tuple[str, str, dict]]) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS documents "
            "(collection TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY (collection, id))"
        )
        for col, ident, body in docs:
            conn.execute(
                "INSERT OR REPLACE INTO documents (collection, id, body) VALUES (?, ?, ?)",
                (col, ident, json.dumps(body)),
            )
        conn.commit()
    finally:
        conn.close()


def _run_cli(
    ws: dict[str, Path],
    *args: str,
    env_extra: dict[str, str] | None = None,
    with_contract_env: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(ws["root"]),
        "TICKETS_DIR": str(ws["tickets"]),
    }
    if with_contract_env:
        env["WORKLOAD_CONTRACT"] = str(ws["contract"])
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(START_PY), "--root", str(ws["root"]), *args],
        cwd=ws["root"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


class NextStageDetectionTest(unittest.TestCase):
    def test_stage_1_missing_contract_and_missing_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td), with_contract=False)
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0, res.stderr)
            out = res.stdout.strip()
            self.assertEqual(len(res.stdout.splitlines()), 1)
            self.assertRegex(out, r"^GO: next — stage 1/6 \([^)]+\): .*attach|.*init")

        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td), with_contract=True, baseline="")
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("stage 1/6", res.stdout)
            self.assertIn("mutation-baseline", res.stdout)

    def test_stage_2_needs_start_or_missing_provider_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertRegex(res.stdout.strip(), r"^GO: next — stage 2/6 \([^)]+\): .*start")

        # Start plan exists with live provider alpha, but token in .env is placeholder
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write(
                ws["root"] / ".crew" / "start-plan.env",
                "START_PRESET=strict\nSTART_MODE=expert\nSTART_PROFILE=l1\n"
                "MODEL_PROVIDER=alpha\nAGENT_HARNESS=alpha-cli\nDELIVERY_ADAPTER=local-proxy\n",
            )
            _write(ws["root"] / ".env", "SYNTH_ALPHA_TOKEN=replace-me\n")
            res = _run_cli(ws, "next", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data["stage"], 2)
            self.assertFalse(data["provider"]["key_set"])
            self.assertIn("SYNTH_ALPHA_TOKEN", data["command"] + " " + data["reason"])
            self.assertNotIn("replace-me", res.stdout)

        # Once token is real in .env, Stage 2 passes -> Stage 3, and secret value NEVER leaks
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            secret = "top-secret-alpha-xyz-987654"
            _write(
                ws["root"] / ".crew" / "start-plan.env",
                "START_PRESET=strict\nSTART_MODE=expert\nSTART_PROFILE=l1\n"
                "MODEL_PROVIDER=alpha\nAGENT_HARNESS=alpha-cli\nDELIVERY_ADAPTER=local-proxy\n",
            )
            _write(ws["root"] / ".env", f"SYNTH_ALPHA_TOKEN={secret}\n")
            res = _run_cli(ws, "next", "--json")
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data["stage"], 3)
            self.assertTrue(data["provider"]["key_set"])
            self.assertNotIn(secret, res.stdout)
            self.assertNotIn(secret, res.stderr)

    def test_stage_3_intake_and_questions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write(
                ws["root"] / ".crew" / "start-plan.env",
                "START_PRESET=junior\nSTART_MODE=junior\nSTART_PROFILE=l0\n"
                "MODEL_PROVIDER=replay\nAGENT_HARNESS=replay\nDELIVERY_ADAPTER=local-proxy\n",
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertRegex(res.stdout.strip(), r"^GO: next — stage 3/6 \([^)]+\): .*intake")

            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [("tickets", "0001", {"id": "0001", "kind": "intake", "state": "proposed"})],
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 3/6", res.stdout)
            self.assertIn("intake-confirm", res.stdout)

        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write(
                ws["root"] / ".crew" / "start-plan.env",
                "START_PRESET=junior\nSTART_MODE=junior\nSTART_PROFILE=l0\n"
                "MODEL_PROVIDER=replay\nAGENT_HARNESS=replay\nDELIVERY_ADAPTER=local-proxy\n",
            )
            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [("tickets", "0002", {"id": "0002", "state": "questions"})],
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 3/6", res.stdout)
            self.assertIn("answer TICKET=0002", res.stdout)

    def test_stage_4_build_gates_and_rework(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [("tickets", "0003", {"id": "0003", "state": "opened"})],
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 4/6", res.stdout)
            self.assertIn("assign TICKET=0003", res.stdout)

            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [
                    (
                        "tickets",
                        "0003",
                        {
                            "id": "0003",
                            "state": "ci-failed",
                            "history": [{"step": "ci", "state": "pr-opened", "error": "step failed"}],
                        },
                    )
                ],
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 4/6", res.stdout)
            self.assertIn("resume TICKET=0003", res.stdout)

    def test_stage_5_canary_and_human_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [("tickets", "0004", {"id": "0004", "state": "main-green"})],
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 5/6", res.stdout)
            self.assertIn("release", res.stdout)

            _write(
                ws["root"] / ".crew" / "release.json",
                json.dumps({"version": "0.2.0-rc1", "stable": "0.1.0", "verdict": "held", "stages": []}),
            )
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 5/6", res.stdout)
            self.assertIn("promote", res.stdout)

    def test_stage_6_observability_evidence_and_postmortem(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write_state(
                ws["root"] / ".crew" / "state.sqlite",
                [("tickets", "0005", {"id": "0005", "state": "rolled-back"})],
            )
            _write(
                ws["root"] / ".crew" / "releases.jsonl",
                json.dumps(
                    {
                        "kind": "release",
                        "at": "2026-11-24T00:00:00Z",
                        "ticket": "0005",
                        "record": {"version": "0.2.0-rc2", "stable": "0.1.0", "verdict": "rolled-back", "stages": []},
                    }
                )
                + "\n",
            )
            # Needs logbook first
            res = _run_cli(ws, "next")
            self.assertEqual(res.returncode, 0)
            self.assertIn("stage 6/6", res.stdout)
            self.assertIn("logbook", res.stdout)

            # Logbook present -> needs dora
            _write(ws["root"] / ".crew" / "logbook.json", json.dumps({"schema": "lights-out/logbook/1.8"}))
            res = _run_cli(ws, "next")
            self.assertIn("stage 6/6", res.stdout)
            self.assertIn("dora", res.stdout)

            # Dora present -> needs evidence
            _write(ws["root"] / ".crew" / "dora.json", json.dumps({"schema": "dora/1"}))
            res = _run_cli(ws, "next")
            self.assertIn("stage 6/6", res.stdout)
            self.assertIn("evidence VERSION=0.2.0-rc2", res.stdout)

            # Evidence present -> rolled back needs postmortem
            _write(ws["root"] / ".crew" / "evidence" / "0.2.0-rc2.json", "{}")
            res = _run_cli(ws, "next")
            self.assertIn("stage 6/6", res.stdout)
            self.assertTrue("postmortem" in res.stdout or "incident" in res.stdout, res.stdout)

            # Postmortem present -> all done, console
            _write(ws["root"] / ".crew" / "postmortem" / "inc-1" / "postmortem.md", "# Postmortem\n")
            res = _run_cli(ws, "next", "--json")
            self.assertEqual(res.returncode, 0)
            payload = json.loads(res.stdout)
            self.assertEqual(payload["stage"], 6)
            self.assertTrue(payload["all_done"])
            self.assertIn("console", payload["command"])


class NextModesAndErrorsTest(unittest.TestCase):
    def test_junior_vs_expert_rendering_and_adapter_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            junior = _run_cli(ws, "next", env_extra={"MODE": "junior"})
            self.assertEqual(junior.returncode, 0)
            self.assertIn("mutation", junior.stderr.lower())
            self.assertIn("canary", junior.stderr.lower())
            self.assertIn("rollback", junior.stderr.lower())

            expert = _run_cli(ws, "next", env_extra={"MODE": "expert"})
            self.assertEqual(expert.returncode, 0)
            self.assertIn("policy.env", expert.stderr)
            self.assertIn("roles/*.md", expert.stderr)
            self.assertIn("mcp.json", expert.stderr)
            self.assertIn("cloud-ship", expert.stderr)

    def test_next_error_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            bad_mode = _run_cli(ws, "next", env_extra={"MODE": "novice"})
            self.assertEqual(bad_mode.returncode, 1)
            self.assertRegex(bad_mode.stdout.strip(), r"^NO-GO: next — ")

            _write(ws["root"] / "profiles" / "providers.json", "{corrupt")
            bad_prov = _run_cli(ws, "next")
            self.assertEqual(bad_prov.returncode, 1)
            self.assertRegex(bad_prov.stdout.strip(), r"^NO-GO: next — ")

        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write(ws["root"] / ".crew" / "logbook.json", "{corrupt")
            bad_book = _run_cli(ws, "next")
            self.assertEqual(bad_book.returncode, 1)
            self.assertRegex(bad_book.stdout.strip(), r"^NO-GO: next — ")


class StartWizardTest(unittest.TestCase):
    def test_non_interactive_junior_preset_writes_non_secret_plan_and_validates_doctor(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            secret = "alpha-live-secret-should-never-touch-disk"
            _write(ws["root"] / ".env", f"SYNTH_ALPHA_TOKEN={secret}\n")
            res = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "junior",
                "--provider",
                "replay",
                "--harness",
                "replay",
                "--delivery",
                "local-proxy",
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertRegex(res.stdout.strip(), r"^GO: start — ")
            plan_path = ws["root"] / ".crew" / "start-plan.env"
            self.assertTrue(plan_path.is_file())
            plan = plan_path.read_text(encoding="utf-8")
            self.assertIn("START_PRESET=junior", plan)
            self.assertIn("START_MODE=junior", plan)
            self.assertIn("MODEL_PROVIDER=replay", plan)
            self.assertIn("AGENT_HARNESS=replay", plan)
            self.assertIn("DELIVERY_ADAPTER=local-proxy", plan)
            self.assertIn("DOCTOR_STATUS=GO", plan)
            self.assertNotIn(secret, plan)
            self.assertNotIn(secret, res.stdout)
            self.assertNotIn(secret, res.stderr)
            self.assertIn("./go next", res.stderr)
            self.assertIn("./go intake", res.stderr)

    def test_non_interactive_strict_and_cloud_delivery_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            secret = "strict-alpha-token-never-persisted"
            _write(ws["root"] / ".env", f"SYNTH_ALPHA_TOKEN={secret}\n")
            res = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "strict",
                "--provider",
                "alpha",
                "--harness",
                "alpha-cli",
                "--delivery",
                "cloud-ship",
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            plan = (ws["root"] / ".crew" / "start-plan.env").read_text(encoding="utf-8")
            self.assertIn("START_PRESET=strict", plan)
            self.assertIn("START_MODE=expert", plan)
            self.assertIn("PROVIDER_KEY_VAR=SYNTH_ALPHA_TOKEN", plan)
            self.assertIn("PROVIDER_KEY_CONFIGURED=yes", plan)
            self.assertIn("DELIVERY_SOURCE=plugins/cloud", plan)
            self.assertNotIn(secret, plan)
            self.assertNotIn(secret, res.stdout)
            self.assertNotIn(secret, res.stderr)

    def test_start_rejects_invalid_provider_harness_or_non_tty_without_flag(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            bad_harness = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "custom",
                "--provider",
                "alpha",
                "--harness",
                "beta-cli",
                "--delivery",
                "local-proxy",
            )
            self.assertEqual(bad_harness.returncode, 1)
            self.assertRegex(bad_harness.stdout.strip(), r"^NO-GO: start — ")

            non_tty = _run_cli(ws, "start")
            self.assertEqual(non_tty.returncode, 1)
            self.assertRegex(non_tty.stdout.strip(), r"^NO-GO: start — ")

    def test_interactive_tty_wizard_writes_non_secret_plan(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            master, slave = pty.openpty()
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(START_PY), "--root", str(ws["root"]), "start"],
                    cwd=ws["root"],
                    env={
                        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "HOME": str(ws["root"]),
                        "WORKLOAD_CONTRACT": str(ws["contract"]),
                    },
                    stdin=slave,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                os.close(slave)
                slave = -1
                os.write(master, b"junior\nreplay\nreplay\nlocal-proxy\n")
                try:
                    out, err = proc.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    raise
            finally:
                if slave != -1:
                    os.close(slave)
                os.close(master)
            self.assertEqual(proc.returncode, 0, out + err)
            self.assertRegex(out.strip(), r"^GO: start — ")
            plan = (ws["root"] / ".crew" / "start-plan.env").read_text(encoding="utf-8")
            self.assertIn("START_PRESET=junior", plan)
            self.assertIn("MODEL_PROVIDER=replay", plan)
            self.assertIn("never enter secret", err.lower())

    def test_start_persists_make_consumable_plan_budgets_and_cloud_adapters_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            _write(ws["root"] / ".env", "SYNTH_ALPHA_TOKEN=alpha-tok-ok\n")
            res = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "strict",
                "--provider",
                "alpha",
                "--harness",
                "alpha-cli",
                "--delivery",
                "cloud-ship",
                "--ticket-budget-usd",
                "3.25",
                "--day-budget-usd",
                "15.50",
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            plan_lines = (ws["root"] / ".crew" / "start-plan.env").read_text(encoding="utf-8").splitlines()
            plan_map = dict(ln.split("=", 1) for ln in plan_lines if "=" in ln and not ln.startswith("#"))
            self.assertEqual(plan_map["START_PRESET"], "strict")
            self.assertEqual(plan_map["START_MODE"], "expert")
            self.assertEqual(plan_map["START_PROFILE"], "l1")
            self.assertEqual(plan_map["MODEL_PROVIDER"], "alpha")
            self.assertEqual(plan_map["MODEL"], "alpha-fast")
            self.assertEqual(plan_map["AGENT_HARNESS"], "alpha-cli")
            self.assertEqual(plan_map["DELIVERY"], "outside/cloud-ship")
            self.assertEqual(plan_map["DELIVERY_ADAPTER"], "cloud-ship")
            self.assertEqual(plan_map["DELIVERY_TARGET"], "outside/cloud-ship")
            self.assertEqual(plan_map["DELIVERY_SOURCE"], "plugins/cloud")
            self.assertEqual(plan_map["DECIDE"], "structured")
            self.assertEqual(plan_map["DECIDE_ADAPTER"], "structured")
            self.assertEqual(plan_map["ADAPTERS_PATH"], str((ws["root"] / "plugins" / "cloud").resolve()))
            self.assertEqual(plan_map["AUTO_INCIDENT_ON_ROLLBACK"], "on")
            self.assertEqual(plan_map["SPEND_BUDGET_TICKET_USD"], "3.25")
            self.assertEqual(plan_map["SPEND_BUDGET_DAY_USD"], "15.50")
            self.assertEqual(plan_map["SUGGESTED_TICKET_BUDGET_USD"], "3.25")
            self.assertEqual(plan_map["SUGGESTED_DAY_BUDGET_USD"], "15.50")
            self.assertEqual(plan_map["START_WORKLOAD_CONTRACT"], str(ws["contract"]))
            self.assertNotIn("WORKLOAD_CONTRACT", plan_map)

        # Without budgets and with built-in delivery, omit SPEND_BUDGET_* and ADAPTERS_PATH
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            res = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "junior",
                "--provider",
                "replay",
                "--harness",
                "replay",
                "--delivery",
                "local-proxy",
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            plan_lines = (ws["root"] / ".crew" / "start-plan.env").read_text(encoding="utf-8").splitlines()
            plan_map = dict(ln.split("=", 1) for ln in plan_lines if "=" in ln and not ln.startswith("#"))
            self.assertEqual(plan_map["DELIVERY"], "local-proxy")
            self.assertEqual(plan_map["DECIDE"], "replay")
            self.assertEqual(plan_map["MODEL"], "")
            self.assertNotIn("ADAPTERS_PATH", plan_map)
            self.assertNotIn("SPEND_BUDGET_TICKET_USD", plan_map)
            self.assertNotIn("SPEND_BUDGET_DAY_USD", plan_map)

        # Invalid budget values must be refused cleanly
        with tempfile.TemporaryDirectory() as td:
            ws = _make_workspace(Path(td))
            bad = _run_cli(
                ws,
                "start",
                "--non-interactive",
                "--preset",
                "junior",
                "--provider",
                "replay",
                "--harness",
                "replay",
                "--delivery",
                "local-proxy",
                "--ticket-budget-usd",
                "0",
            )
            self.assertEqual(bad.returncode, 1)
            self.assertRegex(bad.stdout.strip(), r"^NO-GO: start — ")

    def test_next_and_start_refuse_secret_keys_in_start_plan_env(self) -> None:
        for secret_key in ("SYNTH_ALPHA_TOKEN", "SOME_KEY", "AUTH_SECRET", "DB_PASSWORD", "METRICS_AUTH_HEADER"):
            with tempfile.TemporaryDirectory() as td:
                ws = _make_workspace(Path(td))
                secret_val = "never-print-me-999"
                _write(
                    ws["root"] / ".crew" / "start-plan.env",
                    f"START_PROFILE=l0\n{secret_key}={secret_val}\n",
                )
                res_next = _run_cli(ws, "next")
                self.assertEqual(res_next.returncode, 1, f"{secret_key} was accepted by next")
                self.assertRegex(res_next.stdout.strip(), r"^NO-GO: next — ")
                self.assertIn(secret_key, res_next.stdout)
                self.assertNotIn(secret_val, res_next.stdout + res_next.stderr)

                res_start = _run_cli(
                    ws,
                    "start",
                    "--non-interactive",
                    "--preset",
                    "junior",
                    "--provider",
                    "replay",
                    "--harness",
                    "replay",
                    "--delivery",
                    "local-proxy",
                )
                self.assertEqual(res_start.returncode, 1, f"{secret_key} was accepted by start")
                self.assertRegex(res_start.stdout.strip(), r"^NO-GO: start — ")
                self.assertNotIn(secret_val, res_start.stdout + res_start.stderr)


if __name__ == "__main__":
    unittest.main()
