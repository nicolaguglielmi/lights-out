"""Unit tests for core/map_brief_eval.py and core/live_preflight.py.

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh): builds isolated synthetic git repos,
synthetic provider catalogs, and mocked CLI probes, and asserts that NO
secret value ever leaks into stdout, stderr, or JSON output.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import live_preflight, map as project_map, map_brief_eval

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, content: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if executable:
        path.chmod(0o755)


class _TempGitRepo:
    """Minimal isolated git repository on branch main."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            [
                "git",
                "-c", "user.name=tester",
                "-c", "user.email=tester@lights-out.invalid",
                "-c", "commit.gpgsign=false",
                "-c", "init.defaultBranch=main",
                "-C", str(self.path),
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout

    def commit(self, files: dict[str, str], message: str = "commit") -> str:
        for rel, body in files.items():
            _write(self.path / rel, body)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()


class MapBriefEvalTests(unittest.TestCase):
    def _build_fixture_repo(self, root: Path) -> tuple[_TempGitRepo, str]:
        repo = _TempGitRepo(root)
        sha = repo.commit(
            {
                "src/__init__.py": "",
                "src/calc.py": "def add(a, b):\n    return a + b\n",
                "src/unused.py": "def noop():\n    return None\n",
                "tests/__init__.py": "",
                "tests/test_calc.py": "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
                "docs/adr/0001-calc.md": "# ADR 0001\n\n- Status: accepted\n\nCovers `src/calc.py`.\n",
                "docs/guide.md": "# Guide\n\nSee `src/calc.py`.\n",
                "contract/worker.md": "# Worker brief\n\nWork inside `src/`.\n",
                "tickets/0001.md": (
                    "# 0001 — Fix addition\n\n- Status: open\n\n"
                    "## Problem\n\nAddition needs coverage.\n\n"
                    "## Acceptance criteria\n\nGiven 1 and 2 When added Then 3.\n\n"
                    "## Perimeter\n\n- `src/calc.py`\n"
                ),
                "tickets/0002.md": (
                    "# 0002 — Intake note without perimeter\n\n- Status: open\n\n"
                    "## Problem\n\nPending scope.\n"
                ),
            },
            message="seed ticket/0001",
        )
        return repo, sha

    def test_byte_token_math_coverage_and_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            repo, sha = self._build_fixture_repo(root)
            canary = "CANARY-MAP-SECRET-XYZ-98765"
            _write(root / ".env", f"SOME_SECRET={canary}\n")

            report = map_brief_eval.evaluate_map_brief(
                repo=str(root),
                rev="HEAD",
                map_path=str(root / ".crew" / "map.sqlite"),
                tickets="tickets",
                worker_brief="contract/worker.md",
                write_map=True,
                check_determinism=True,
            )

            self.assertEqual(report["schema"], map_brief_eval.SCHEMA)
            self.assertEqual(report["sha"], sha)
            self.assertTrue((root / ".crew" / "map.sqlite").is_file())
            self.assertTrue(report["determinism"]["checked"])
            self.assertTrue(report["determinism"]["deterministic"])
            self.assertEqual(report["determinism"]["checked_tickets"], 1)

            summary = report["summary"]
            self.assertEqual(summary["tickets_total"], 2)
            self.assertEqual(summary["tickets_with_perimeter"], 1)
            self.assertEqual(summary["tickets_without_perimeter"], 1)

            by_id = {r["ticket"]: r for r in report["tickets"]}
            t1 = by_id["0001"]
            t2 = by_id["0002"]

            worker_bytes = len((root / "contract" / "worker.md").read_bytes())
            t1_bytes = len((root / "tickets" / "0001.md").read_bytes())
            expected_off_bytes = t1_bytes + len(b"\n---\n\n") + worker_bytes
            self.assertEqual(t1["off_bytes"], expected_off_bytes)
            self.assertEqual(t1["off_tokens"], expected_off_bytes / 4)
            self.assertGreater(t1["slice_bytes"], 0)
            self.assertEqual(
                t1["on_bytes"],
                t1["off_bytes"] + len(b"\n---\n\n") + t1["slice_bytes"],
            )
            self.assertEqual(t1["on_tokens"], t1["on_bytes"] / 4)
            self.assertEqual(t1["delta_bytes"], t1["on_bytes"] - t1["off_bytes"])
            self.assertEqual(t1["delta_tokens"], t1["delta_bytes"] / 4)
            self.assertEqual(t1["adrs"], ["docs/adr/0001-calc.md"])
            self.assertEqual(t1["pages"], ["docs/guide.md"])
            self.assertEqual(t1["tests"], ["tests/test_calc.py"])
            self.assertEqual(t1["adrs_count"], 1)
            self.assertEqual(t1["pages_count"], 1)
            self.assertEqual(t1["tests_count"], 1)
            self.assertEqual(t1["files_count"], 1)
            self.assertIsNotNone(t1["slice_sha256"])

            # Ticket without perimeter has zero slice delta
            self.assertFalse(t2["has_perimeter"])
            self.assertEqual(t2["slice_bytes"], 0)
            self.assertEqual(t2["delta_bytes"], 0)
            self.assertEqual(t2["on_bytes"], t2["off_bytes"])

            # Totals match sums
            self.assertEqual(summary["total_off_bytes"], t1["off_bytes"] + t2["off_bytes"])
            self.assertEqual(summary["total_on_bytes"], t1["on_bytes"] + t2["on_bytes"])
            self.assertEqual(summary["total_delta_bytes"], t1["delta_bytes"])
            self.assertEqual(summary["total_delta_tokens"], summary["total_delta_bytes"] / 4)

            # Secret non-leakage check
            dumped = json.dumps(report)
            self.assertNotIn(canary, dumped)

    def test_cli_json_and_go_line_and_no_secret_leak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._build_fixture_repo(root)
            canary = "CANARY-CLI-SECRET-54321"
            _write(root / ".env", f"TOKEN={canary}\n")

            out_json, err_json = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_json), contextlib.redirect_stderr(err_json):
                rc = map_brief_eval.main(
                    ["--repo", str(root), "--worker-brief", "contract/worker.md", "--json"]
                )
            self.assertEqual(rc, 0, err_json.getvalue())
            parsed = json.loads(out_json.getvalue())
            self.assertEqual(parsed["summary"]["tickets_total"], 2)
            self.assertNotIn(canary, out_json.getvalue())
            self.assertNotIn(canary, err_json.getvalue())

            out_txt, err_txt = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out_txt), contextlib.redirect_stderr(err_txt):
                rc2 = map_brief_eval.main(
                    ["--repo", str(root), "--worker-brief", "contract/worker.md"]
                )
            self.assertEqual(rc2, 0, err_txt.getvalue())
            lines = out_txt.getvalue().strip().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("GO: map-brief-eval — 2 tickets (1 with perimeter)"))
            self.assertIn("deterministic=yes", lines[0])
            self.assertNotIn(canary, out_txt.getvalue())
            self.assertNotIn(canary, err_txt.getvalue())

    def test_non_deterministic_slice_refuses_no_go(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._build_fixture_repo(root)
            calls = {"n": 0}
            real_slice_of = project_map.slice_of

            def flaky_slice(db, globs):
                calls["n"] += 1
                res = real_slice_of(db, globs)
                return res if calls["n"] % 2 == 1 else res + "\n# drift\n"

            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(project_map, "slice_of", side_effect=flaky_slice):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = map_brief_eval.main(["--repo", str(root)])
            self.assertEqual(rc, 1)
            self.assertTrue(out.getvalue().strip().startswith("NO-GO: map-brief-eval — non-deterministic"))

    def test_wrapper_script_runs_via_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            self._build_fixture_repo(root)
            proc = subprocess.run(
                ["sh", str(ROOT / "core" / "map_brief_eval.sh"), "--repo", str(root)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(proc.stdout.strip().startswith("GO: map-brief-eval — "))


class LivePreflightTests(unittest.TestCase):
    def _build_synth_workspace(self, root: Path) -> None:
        providers = {
            "about": "Synthetic providers for unit test.",
            "providers": {
                "replay": {
                    "label": "Replay",
                    "harnesses": ["replay"],
                    "default_harness": "replay",
                    "env_var": "",
                    "default_model": "",
                    "default_profile": "l0",
                    "decide": "replay",
                },
                "alpha": {
                    "label": "Alpha",
                    "harnesses": ["alpha-cli"],
                    "default_harness": "alpha-cli",
                    "env_var": "SYNTH_ALPHA_CRED",
                    "default_model": "alpha-fast",
                    "default_profile": "l1",
                    "decide": "alpha-decisions",
                },
                "beta": {
                    "label": "Beta",
                    "harnesses": ["beta-cli"],
                    "default_harness": "beta-cli",
                    "env_var": "SYNTH_BETA_CRED",
                    "default_model": "beta-pro",
                    "default_profile": "l1",
                    "decide": "structured",
                },
                "gamma": {
                    "label": "Gamma",
                    "harnesses": ["gamma-cli"],
                    "default_harness": "gamma-cli",
                    "env_var": "SYNTH_GAMMA_CRED",
                    "default_model": "gamma-mini",
                    "default_profile": "l1",
                    "decide": "structured",
                },
                "delta": {
                    "label": "Delta",
                    "harnesses": ["beta-cli"],
                    "default_harness": "beta-cli",
                    "env_var": "SYNTH_DELTA_CRED",
                    "default_model": "delta-1",
                    "default_profile": "l1",
                    "decide": "structured",
                },
            },
        }
        _write(root / "profiles" / "providers.json", json.dumps(providers, indent=2))
        _write(
            root / "profiles" / "l2.env",
            "LEVEL=l2\nCODE_HOST=forge\nTRACKER=forge-issues\nEVENT_BUS=bus-svc\n"
            "STATE_STORE=store-svc\nDELIVERY=ship-svc\nMETRICS=prom-svc\nREGISTRY=reg-svc\n",
        )
        _write(root / "profiles" / "l3.env", "LEVEL=l3\nORCHESTRATOR=wf-svc\n")
        _write(
            root / "adapters" / "forge" / "env.sh",
            '#!/bin/sh\nif [ -z "${FORGE_TOKEN:-}" ] || [ -z "${FORGE_REPO:-}" ]; then exit 1; fi\n',
            executable=True,
        )
        _write(root / "plugins" / "cloud" / "gcp-ship-plugin" / "deploy.sh", "#!/bin/sh\nexit 0\n", executable=True)
        _write(root / "plugins" / "cloud" / "alt-ship-plugin" / "README.md", "# Alt plugin\n")
        _write(
            root / ".env.example",
            "GOOGLE_CLOUD_PROJECT=replace-me\nGOOGLE_CLOUD_LOCATION=europe-west1\n",
        )
        _write(
            root / ".forge" / "workflows" / "cloud-conformance.yml",
            "env:\n  CLOUD_CONFORMANCE_ENABLED: ${{ vars.CLOUD_CONFORMANCE_ENABLED }}\n"
            "  GCP_WIF_PROVIDER: ${{ vars.GCP_WIF_PROVIDER }}\n"
            "  GCP_DEPLOYER_SA: ${{ vars.GCP_DEPLOYER_SA }}\n"
            "  GCP_PROJECT: ${{ vars.GCP_PROJECT }}\n"
            "  GCP_REGION: ${{ vars.GCP_REGION }}\n"
            "  SHIP_PIPELINE: ${{ vars.SHIP_PIPELINE }}\n",
        )

    def test_never_leaks_secret_values_in_json_stdout_or_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_synth_workspace(root)

            sec_alpha = "sk-alpha-NEVER-LEAK-THIS-VALUE-1111"
            sec_beta = "sk-beta-NEVER-LEAK-PROC-2222"
            sec_forge = "tok-forge-NEVER-LEAK-3333"
            sec_gh_cli = "gho_NEVER_LEAK_FROM_CLI_4444"
            sec_placeholder = "replace-me"

            _write(
                root / ".env",
                f'SYNTH_ALPHA_CRED="{sec_alpha}"\n'
                f"SYNTH_GAMMA_CRED={sec_placeholder}\n"
                f"FORGE_TOKEN={sec_forge}\n",
            )
            proc_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "SYNTH_BETA_CRED": sec_beta,
            }

            def fake_which(name: str) -> str | None:
                return f"/usr/bin/{name}" if name in ("gh", "gcloud", "podman") else None

            def fake_run_ro(argv, timeout_s: int = 5):
                exe = Path(argv[0]).name
                if exe == "gh":
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        stdout="",
                        stderr=(
                            "forge.example\n"
                            "  ✓ Logged in to forge.example account octocat (keyring)\n"
                            f"  - Token: {sec_gh_cli}\n"
                        ),
                    )
                if exe == "gcloud" and argv[1:3] == ["auth", "list"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        stdout=json.dumps([{"account": "dev@example.com", "status": "ACTIVE"}]),
                        stderr="",
                    )
                if exe == "gcloud" and argv[1:3] == ["config", "get-value"]:
                    return subprocess.CompletedProcess(argv, 0, stdout="demo-proj\n", stderr="")
                if exe == "podman":
                    return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
                return None

            with (
                mock.patch.object(live_preflight.shutil, "which", side_effect=fake_which),
                mock.patch.object(live_preflight, "_run_ro", side_effect=fake_run_ro),
                mock.patch.object(live_preflight, "_in_container", return_value=False),
            ):
                report = live_preflight.audit_live_readiness(repo=str(root), env=proc_env)

                out_json, err_json = io.StringIO(), io.StringIO()
                with (
                    mock.patch.dict(os.environ, proc_env, clear=True),
                    contextlib.redirect_stdout(out_json),
                    contextlib.redirect_stderr(err_json),
                ):
                    rc_json = live_preflight.main(["--repo", str(root), "--json"])

                out_txt, err_txt = io.StringIO(), io.StringIO()
                with (
                    mock.patch.dict(os.environ, proc_env, clear=True),
                    contextlib.redirect_stdout(out_txt),
                    contextlib.redirect_stderr(err_txt),
                ):
                    rc_txt = live_preflight.main(["--repo", str(root)])

            self.assertEqual(rc_json, 0)
            self.assertEqual(rc_txt, 0)

            all_text = "\n".join(
                [
                    json.dumps(report),
                    out_json.getvalue(),
                    err_json.getvalue(),
                    out_txt.getvalue(),
                    err_txt.getvalue(),
                ]
            )
            for secret in (sec_alpha, sec_beta, sec_forge, sec_gh_cli):
                self.assertNotIn(secret, all_text)

            # Verify boolean key presence
            provs = report["providers"]
            self.assertTrue(provs["alpha"]["present"])
            self.assertEqual(provs["alpha"]["sources"], [".env"])
            self.assertTrue(provs["beta"]["present"])
            self.assertEqual(provs["beta"]["sources"], ["env"])
            self.assertFalse(provs["gamma"]["present"])  # replace-me is treated as missing
            self.assertFalse(provs["delta"]["present"])

            # Verify CLI auth parsing
            self.assertTrue(report["clis"]["gh"]["authenticated"])
            self.assertEqual(report["clis"]["gh"]["account"], "octocat")
            self.assertTrue(report["clis"]["gcloud"]["authenticated"])
            self.assertEqual(report["clis"]["gcloud"]["active_account"], "dev@example.com")
            self.assertTrue(report["clis"]["container_engine"]["ready"])

            # Verify readiness matrix
            m24 = report["readiness_matrix"]["M24"]
            self.assertEqual(m24["status"], "missing-key")
            self.assertEqual(m24["ready_providers"], ["alpha", "beta"])
            self.assertEqual(m24["missing_providers"], ["gamma", "delta"])

            m21 = report["readiness_matrix"]["M21"]
            self.assertEqual(m21["status"], "missing-key")
            self.assertTrue(m21["required_env"]["FORGE_TOKEN"])
            self.assertFalse(m21["required_env"]["FORGE_REPO"])

            cloud = report["readiness_matrix"]["M8_M10_M44"]
            self.assertEqual(cloud["status"], "missing-auth")
            self.assertIn("gcp-ship-plugin", cloud["cloud_plugins"])

            # Verify single GO line contract
            go_lines = out_txt.getvalue().strip().splitlines()
            self.assertEqual(len(go_lines), 1)
            self.assertTrue(go_lines[0].startswith("GO: live-preflight — "))

    def test_container_visibility_and_ready_states_and_strict_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_synth_workspace(root)

            # Container environment with no host CLIs on PATH and missing keys
            with (
                mock.patch.object(live_preflight.shutil, "which", return_value=None),
                mock.patch.object(live_preflight, "_in_container", return_value=True),
            ):
                container_rep = live_preflight.audit_live_readiness(repo=str(root), env={})
            self.assertEqual(container_rep["environment"]["kind"], "container")
            for cli_name in ("gh", "gcloud", "aws", "az", "container_engine"):
                self.assertEqual(container_rep["clis"][cli_name]["status"], "not-visible-in-container")

            # Strict mode returns exit 1 when not ready
            out_strict = io.StringIO()
            with (
                mock.patch.object(live_preflight.shutil, "which", return_value=None),
                mock.patch.object(live_preflight, "_in_container", return_value=True),
                mock.patch.dict(os.environ, {}, clear=True),
                contextlib.redirect_stdout(out_strict),
            ):
                rc_strict = live_preflight.main(["--repo", str(root), "--strict", "--json"])
            self.assertEqual(rc_strict, 1)

            # Now configure all keys + OIDC federation => ready across all 3 tracks
            full_env = {
                "SYNTH_ALPHA_CRED": "val-a",
                "SYNTH_BETA_CRED": "val-b",
                "SYNTH_GAMMA_CRED": "val-c",
                "FORGE_TOKEN": "val-ft",
                "FORGE_REPO": "org/repo",
                "CLOUD_CONFORMANCE_ENABLED": "true",
                "GCP_WIF_PROVIDER": "projects/1/locations/global/workloadIdentityPools/p/providers/g",
                "GCP_DEPLOYER_SA": "deployer@proj.iam.gserviceaccount.com",
            }
            with (
                mock.patch.object(live_preflight.shutil, "which", return_value=None),
                mock.patch.object(live_preflight, "_in_container", return_value=False),
                mock.patch.dict(os.environ, full_env, clear=True),
            ):
                out_ok = io.StringIO()
                with contextlib.redirect_stdout(out_ok):
                    rc_ok = live_preflight.main(["--repo", str(root), "--strict", "--json"])
                self.assertEqual(rc_ok, 0)
                ok_doc = json.loads(out_ok.getvalue())
                self.assertEqual(ok_doc["readiness_matrix"]["M24"]["status"], "ready")
                self.assertEqual(ok_doc["readiness_matrix"]["M21"]["status"], "ready")
                self.assertEqual(ok_doc["readiness_matrix"]["M8_M10_M44"]["status"], "ready")
                for secret_val in full_env.values():
                    if len(secret_val) > 5 and secret_val != "org/repo":
                        self.assertNotIn(secret_val, out_ok.getvalue())

    def test_wrapper_script_runs_via_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_synth_workspace(root)
            proc = subprocess.run(
                ["sh", str(ROOT / "core" / "live_preflight.sh"), "--repo", str(root)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(proc.stdout.strip().startswith("GO: live-preflight — "))


if __name__ == "__main__":
    unittest.main()
