"""End-to-end wiring tests for Wave B Makefile targets and ./go forwarding (ADRs 0107, 0109)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.is_file():
        return []
    raw = log_path.read_bytes()
    calls: list[list[str]] = []
    for chunk in raw.split(b"\x00\x00"):
        if not chunk:
            continue
        args = [part.decode("utf-8", errors="replace") for part in chunk.split(b"\x00") if part]
        if args:
            calls.append(args)
    return calls


def _extract_env_flags(argv: list[str]) -> list[str]:
    envs: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "-e" and i + 1 < len(argv):
            envs.append(argv[i + 1])
            i += 2
        elif argv[i].startswith("-e="):
            envs.append(argv[i][3:])
            i += 1
        else:
            i += 1
    return envs


def _extract_port_flags(argv: list[str]) -> list[str]:
    ports: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "-p" and i + 1 < len(argv):
            ports.append(argv[i + 1])
            i += 2
        elif argv[i].startswith("-p="):
            ports.append(argv[i][3:])
            i += 1
        else:
            i += 1
    return ports


class WaveBMakefileAndGoWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        if not shutil.which("make"):
            self.skipTest("make is required")
        self.tmp = Path(tempfile.mkdtemp(prefix="wave-b-wiring-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.ws = self.tmp / "repo"
        shutil.copytree(
            ROOT,
            self.ws,
            ignore=shutil.ignore_patterns(".git", ".crew", ".attached", ".gates-out", ".claude", "__pycache__"),
        )
        subprocess.run(["git", "-C", str(self.ws), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@invalid", "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@invalid", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "snap"],
            check=True,
        )

    def _setup_fake_podman(self) -> tuple[Path, Path, Path]:
        tools = self.tmp / "tools"
        tools.mkdir(exist_ok=True)
        for tool in (
            "sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut", "cksum",
            "cat", "uname", "basename", "dirname", "mkdir", "rm", "mktemp", "id",
            "env", "wc", "stat", "date", "git", "python3",
        ):
            found = shutil.which(tool)
            if found and not (tools / tool).exists():
                (tools / tool).symlink_to(found)
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        go_log = self.tmp / "go-podman.log"
        sock = self.tmp / "podman.sock"
        sock.write_text("", encoding="utf-8")
        podman = bindir / "podman"
        podman.write_text(
            f"""#!/bin/sh
python3 - {str(go_log)!r} "$@" <<'PY'
import sys
log = sys.argv[1]
with open(log, "ab") as fh:
    fh.write(b"\\x00".join(a.encode("utf-8") for a in sys.argv[2:]) + b"\\x00\\x00")
PY
case "$1" in
  info)
    for a in "$@"; do
      case "$a" in
        *RemoteSocket.Path*) printf 'unix://{sock}\\n'; exit 0 ;;
        *RemoteSocket.Exists*) printf 'true\\n'; exit 0 ;;
      esac
    done
    exit 0 ;;
  image) exit 0 ;;
  run) exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        podman.chmod(0o755)
        return bindir, tools, go_log

    def test_make_help_and_go_header_document_all_wave_b_targets(self) -> None:
        help_res = subprocess.run(
            ["make", "--no-print-directory", "help"],
            cwd=self.ws,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(help_res.returncode, 0, help_res.stderr)
        go_text = (self.ws / "go").read_text(encoding="utf-8")
        for target in (
            "registry-conformance", "flags-conformance", "tracker-conformance", "charts-conformance",
            "watch", "alert-webhook", "auto-incident", "cli", "top",
            "adr-check", "migration-safety", "provenance", "console-build",
        ):
            self.assertRegex(help_res.stdout, rf"(?m)^\s*{target}\s+", f"make help missing {target}")
            self.assertIn(f"./go {target}", go_text, f"./go header missing {target}")

    def test_watch_alert_webhook_and_auto_incident_targets_run_cleanly(self) -> None:
        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}
        # Standalone exemptions: work even with missing workload.env and unadmitted cloud DELIVERY in start-plan.env
        crew = self.ws / ".crew"
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "start-plan.env").write_text(
            "START_PROFILE=l0\nDELIVERY=outside/gcp-cloud-deploy\nADAPTERS_PATH="
            + str((self.ws / "plugins" / "cloud").resolve())
            + "\n",
            encoding="utf-8",
        )
        missing_wl = self.tmp / "empty-contract"
        missing_wl.mkdir(exist_ok=True)

        alert_src = self.tmp / "alert.json"
        alert_src.write_text(
            json.dumps({"alertname": "High5xx", "severity": "critical", "summary": "Spike in 5xx"}),
            encoding="utf-8",
        )
        res_wh = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "alert-webhook",
                f"WORKLOAD_CONTRACT={missing_wl}",
                f"ALERT_FILE={alert_src}",
            ],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_wh.returncode, 0, res_wh.stdout + res_wh.stderr)
        written_alert = Path(res_wh.stdout.strip())
        self.assertTrue((self.ws / written_alert).is_file() or written_alert.is_file(), res_wh.stdout)

        # Standalone auto-incident works without workload.env and without state ticket 0001
        (crew / "release.json").write_text(
            json.dumps({
                "version": "0.2.0-rc1",
                "stable": "0.1.0",
                "verdict": "rolled-back",
                "started_at": "2026-11-24T01:00:00Z",
                "finished_at": "2026-11-24T01:02:00Z",
                "stages": [{"stage": "5%", "verdict": "NO-GO: slo — error_rate"}],
            }),
            encoding="utf-8",
        )
        res_ai = subprocess.run(
            ["make", "--no-print-directory", "auto-incident", f"WORKLOAD_CONTRACT={missing_wl}"],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_ai.returncode, 0, res_ai.stdout + res_ai.stderr)
        inc_path = self.ws / res_ai.stdout.strip()
        self.assertTrue(inc_path.is_file(), res_ai.stdout)
        inc_doc = json.loads(inc_path.read_text(encoding="utf-8"))
        self.assertIsNone(inc_doc["chain"])

        # watch --once runs cleanly (and ONCE=1 + POLL_S=2 avoids argparse mutual-exclusion failure)
        (crew / "start-plan.env").unlink(missing_ok=True)
        res_watch = subprocess.run(
            ["make", "--no-print-directory", "watch", "ONCE=1", "POLL_S=2"],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_watch.returncode, 0, res_watch.stdout + res_watch.stderr)

    def test_flags_and_registry_conformance_targets_via_makefile(self) -> None:
        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}
        res_fl = subprocess.run(
            ["make", "--no-print-directory", "flags-conformance", "FLAGS=flags-file"],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_fl.returncode, 0, res_fl.stdout + res_fl.stderr)
        self.assertTrue(res_fl.stdout.strip().startswith("GO: flags-conformance — flags-file"), res_fl.stdout)

        ext_dir = (self.ws / "fixtures" / "outside-adapters").resolve()
        reg_file = self.tmp / "reg-state"
        reg_env = dict(base_env, FILE_REGISTRY_DIR=str(self.tmp / "file-registry-state"))
        res_reg = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "registry-conformance",
                "REGISTRY=outside/file-registry",
                f"ADAPTERS_PATH={ext_dir}",
                f"ADAPTERS_REGISTER={reg_file}",
            ],
            cwd=self.ws,
            env=reg_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_reg.returncode, 0, res_reg.stdout + res_reg.stderr)
        self.assertTrue(res_reg.stdout.strip().startswith("GO: registry-conformance — "), res_reg.stdout)

    def test_go_forwards_wave_b_vars_safely_ports_webhook_and_handles_cloud_setup_slo_mode(self) -> None:
        bindir, tools, go_log = self._setup_fake_podman()
        sample_wh_val = "sample-wh-value-000"
        sample_hdr_val = "Bearer sample-hdr-000"
        env_base = {
            "PATH": f"{bindir}:{tools}",
            "HOME": str(self.tmp),
            "POLL_S": "3",
            "ONCE": "1",
            "ALERT_FILE": "/tmp/a.json",
            "ALERT_WEBHOOK_TOKEN": sample_wh_val,
            "AUTO_INCIDENT_ON_ROLLBACK": "on",
            "AUTO_POSTMORTEM_ON_ROLLBACK": "off",
            "SLO_PROMQL": "plugins/cloud/gcp-cloud-deploy/slo-ingress.promql",
            "SLO_MODE": "proxy",
            "INIT_SERVICE": "services/api",
            "SMOKE_AUTH_HEADER": sample_hdr_val,
        }

        # Server mode publishes 8097 & binds 0.0.0.0 inside container; sensitive vars passed by name only
        env_srv = {k: v for k, v in env_base.items() if k != "ALERT_FILE"}
        res_srv = subprocess.run(
            ["sh", "./go", "alert-webhook"],
            cwd=self.ws,
            env=env_srv,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_srv.returncode, 0, res_srv.stderr)
        argv_srv = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_srv = _extract_env_flags(argv_srv)
        ports_srv = _extract_port_flags(argv_srv)
        self.assertIn("127.0.0.1:8097:8097", ports_srv)
        self.assertIn("WEBHOOK_BIND=0.0.0.0", envs_srv)
        self.assertIn("ALERT_WEBHOOK_TOKEN", envs_srv)
        self.assertIn("SMOKE_AUTH_HEADER", envs_srv)
        self.assertNotIn(sample_wh_val, " ".join(argv_srv) + res_srv.stdout + res_srv.stderr)
        self.assertNotIn(sample_hdr_val, " ".join(argv_srv) + res_srv.stdout + res_srv.stderr)

        for key in (
            "POLL_S", "ONCE", "AUTO_INCIDENT_ON_ROLLBACK", "AUTO_POSTMORTEM_ON_ROLLBACK",
            "SLO_PROMQL", "SLO_MODE", "INIT_SERVICE",
        ):
            self.assertIn(key, envs_srv, f"go missed forwarding {key}")

        # Normalizer mode with ALERT_FILE=... must NOT bind host port 8097
        go_log.unlink(missing_ok=True)
        res_norm = subprocess.run(
            ["sh", "./go", "alert-webhook", "ALERT_FILE=/tmp/a.json"],
            cwd=self.ws,
            env=env_srv,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_norm.returncode, 0, res_norm.stderr)
        argv_norm = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        self.assertEqual(_extract_port_flags(argv_norm), [])

        # cloud-setup accepts --slo-mode proxy and passes SLO_MODE=proxy to make
        go_log.unlink(missing_ok=True)
        res_cs = subprocess.run(
            ["sh", "./go", "cloud-setup", "CLOUD=gcp-cloud-deploy", "--slo-mode", "proxy"],
            cwd=self.ws,
            env=env_srv,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_cs.returncode, 0, res_cs.stderr)
        argv_cs = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        self.assertIn("SLO_MODE=proxy", argv_cs[-4:])

    def test_cli_top_adr_check_and_migration_safety_wiring(self) -> None:
        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}
        crew = self.ws / ".crew"
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "start-plan.env").write_text(
            "START_PROFILE=l0\nDELIVERY=outside/gcp-cloud-deploy\nADAPTERS_PATH="
            + str((self.ws / "plugins" / "cloud").resolve())
            + "\n",
            encoding="utf-8",
        )
        missing_wl = self.tmp / "empty-contract"
        missing_wl.mkdir(exist_ok=True)

        # Standalone cli & top work even without workload.env or admitted outside adapter
        res_cli = subprocess.run(
            [
                "make", "--no-print-directory", "cli",
                f"WORKLOAD_CONTRACT={missing_wl}",
                "CLI_ARGS=--dry-run ticket assign --ticket 0001",
            ],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_cli.returncode, 0, res_cli.stdout + res_cli.stderr)
        cli_plan = json.loads(res_cli.stdout)
        self.assertEqual(cli_plan["flat"], "assign")
        self.assertTrue(cli_plan["argv"][1:] == ["assign", "TICKET=0001"])

        res_top_once = subprocess.run(
            [
                "make", "--no-print-directory", "top",
                f"WORKLOAD_CONTRACT={missing_wl}",
                "LOGBOOK_FILE=fixtures/logbook/golden-1.8.json",
                "ONCE=1",
            ],
            cwd=self.ws,
            env=dict(base_env, NO_COLOR="1"),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_top_once.returncode, 0, res_top_once.stdout + res_top_once.stderr)
        self.assertEqual(len(res_top_once.stdout.splitlines()), 24)

        res_top_json = subprocess.run(
            [
                "make", "--no-print-directory", "top",
                f"WORKLOAD_CONTRACT={missing_wl}",
                "LOGBOOK_FILE=fixtures/logbook/golden-1.8.json",
                "JSON=1",
            ],
            cwd=self.ws,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_top_json.returncode, 0, res_top_json.stdout + res_top_json.stderr)
        self.assertEqual(json.loads(res_top_json.stdout)["schema"], "lights-out/tui-summary/1.0")

        # adr-check and migration-safety default to last commit when BASE/HEAD omitted
        mig_dir = self.ws / "app" / "backend" / "migrations"
        mig_dir.mkdir(parents=True, exist_ok=True)
        (mig_dir / "900_expand.sql").write_text("ALTER TABLE items ADD COLUMN note TEXT;\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.ws), "add", "app/backend/migrations/900_expand.sql"], check=True)
        subprocess.run(
            ["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "safe expand"],
            check=True,
        )
        res_adr = subprocess.run(
            ["make", "--no-print-directory", "adr-check"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_adr.returncode, 0, res_adr.stdout + res_adr.stderr)
        self.assertTrue(res_adr.stdout.startswith("GO: adr-check"), res_adr.stdout)

        res_mig_ok = subprocess.run(
            ["make", "--no-print-directory", "migration-safety"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_mig_ok.returncode, 0, res_mig_ok.stdout + res_mig_ok.stderr)
        self.assertTrue(res_mig_ok.stdout.startswith("GO: migration-safety"), res_mig_ok.stdout)

        (mig_dir / "901_drop.sql").write_text("ALTER TABLE items DROP COLUMN note;\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.ws), "add", "app/backend/migrations/901_drop.sql"], check=True)
        subprocess.run(
            ["git", "-C", str(self.ws), "-c", "user.name=t", "-c", "user.email=t@invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "unsafe drop"],
            check=True,
        )
        res_mig_bad = subprocess.run(
            ["make", "--no-print-directory", "migration-safety"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_mig_bad.returncode, 2, res_mig_bad.stdout + res_mig_bad.stderr)
        self.assertTrue(res_mig_bad.stdout.startswith("NO-GO: migration-safety — "), res_mig_bad.stdout)

        # ./go cli & ./go top flag forwarding, plus socket-free gates
        bindir, tools, go_log = self._setup_fake_podman()
        go_env = {"PATH": f"{bindir}:{tools}", "HOME": str(self.tmp), "NO_COLOR": "1"}

        res_go_cli = subprocess.run(
            ["sh", "./go", "cli", "ticket", "assign", "TICKET=0001", "--json"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_go_cli.returncode, 0, res_go_cli.stderr)
        argv_cli = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_cli = _extract_env_flags(argv_cli)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_cli)
        self.assertEqual(argv_cli[-2:], ["make", "cli"])

        go_log.unlink(missing_ok=True)
        res_go_top = subprocess.run(
            ["sh", "./go", "top", "--once", "--view", "canary", "LOGBOOK_FILE=fixtures/logbook/golden-1.8.json"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_go_top.returncode, 0, res_go_top.stderr)
        argv_top = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_top = _extract_env_flags(argv_top)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_top)
        self.assertIn("NO_COLOR", envs_top)
        self.assertFalse(any("DOCKER_HOST" in e for e in envs_top))
        self.assertEqual(argv_top[-3:], ["make", "top", "LOGBOOK_FILE=fixtures/logbook/golden-1.8.json"])

        for gate in ("adr-check", "migration-safety"):
            go_log.unlink(missing_ok=True)
            res_g = subprocess.run(
                ["sh", "./go", gate],
                cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(res_g.returncode, 0, res_g.stderr)
            argv_g = next(c for c in _read_calls(go_log) if c and c[0] == "run")
            envs_g = _extract_env_flags(argv_g)
            self.assertFalse(any("DOCKER_HOST" in e for e in envs_g), gate)
            self.assertEqual(argv_g[-2:], ["make", gate])

    def test_sprint2_targets_and_zero_copy_go_init_apply_wiring(self) -> None:
        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}

        res_cb = subprocess.run(
            ["make", "--no-print-directory", "console-build", "CONSOLE_BUILD_ARGS=--check"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_cb.returncode, 0, res_cb.stdout + res_cb.stderr)

        res_tr = subprocess.run(
            ["make", "--no-print-directory", "tracker-conformance", "TRACKER=file"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_tr.returncode, 0, res_tr.stdout + res_tr.stderr)
        self.assertTrue(res_tr.stdout.strip().startswith("GO: tracker-conformance — file"), res_tr.stdout)

        ext_dir = (self.ws / "fixtures" / "outside-adapters").resolve()
        reg_file = self.tmp / "reg-state-s2"
        tr_env = dict(base_env, FILE_TRACKER_DIR=str(self.tmp / "file-tracker-state"))
        res_tr_out = subprocess.run(
            [
                "make", "--no-print-directory", "tracker-conformance",
                "TRACKER=outside/file-tracker", f"ADAPTERS_PATH={ext_dir}", f"ADAPTERS_REGISTER={reg_file}",
            ],
            cwd=self.ws, env=tr_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_tr_out.returncode, 0, res_tr_out.stdout + res_tr_out.stderr)
        self.assertTrue(res_tr_out.stdout.strip().startswith("GO: tracker-conformance — "), res_tr_out.stdout)

        res_ch = subprocess.run(
            ["make", "--no-print-directory", "charts-conformance"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_ch.returncode, 2, res_ch.stdout + res_ch.stderr)
        self.assertTrue(res_ch.stdout.strip().startswith("NO-GO: charts-conformance — "), res_ch.stdout)

        res_prov = subprocess.run(
            ["make", "--no-print-directory", "provenance"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_prov.returncode, 2, res_prov.stdout + res_prov.stderr)
        self.assertTrue(res_prov.stdout.strip().startswith("NO-GO: provenance — "), res_prov.stdout)
        self.assertNotIn("0001", res_prov.stdout, "Makefile default TICKET=0001 leaked into provenance")

        # ./go init --apply mounts target repo :rw without cloning into .attached
        ext_repo = self.tmp / "ext-target"
        ext_repo.mkdir()
        subprocess.run(["git", "-C", str(ext_repo), "init", "-q"], check=True)
        (ext_repo / "README.md").write_text("# ext\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(ext_repo), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(ext_repo), "-c", "user.name=t", "-c", "user.email=t@invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
            check=True,
        )

        bindir, tools, go_log = self._setup_fake_podman()
        go_env = {"PATH": f"{bindir}:{tools}", "HOME": str(self.tmp)}
        res_apply = subprocess.run(
            ["sh", "./go", "init", str(ext_repo), "--apply", "--branch", "lo/apply", "--commit",
             "--baseline-mode", "incremental", "--baseline-max-files", "2"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_apply.returncode, 0, res_apply.stderr)
        self.assertFalse((self.ws / ".attached").exists(), "zero-copy init --apply must not clone into .attached")
        argv_apply = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        resolved_ext = str(ext_repo.resolve())
        self.assertTrue(
            any(m == f"{resolved_ext}:{resolved_ext}:rw" or m.startswith(f"{resolved_ext}:{resolved_ext}:rw,") for m in argv_apply),
            argv_apply,
        )
        envs_apply = _extract_env_flags(argv_apply)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_apply)
        self.assertEqual(argv_apply[-3:], ["make", "init", f"WORKLOAD={resolved_ext}"])

        go_log.unlink(missing_ok=True)
        res_cb_go = subprocess.run(
            ["sh", "./go", "console-build", "--check"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_cb_go.returncode, 0, res_cb_go.stderr)
        argv_cb = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        self.assertEqual(argv_cb[-3:], ["make", "console-build", "CONSOLE_BUILD_ARGS=--check"])

    def test_sprint3_wave_b_cli_make_go_and_console_oidc_forwarding(self):
        help_res = subprocess.run(
            ["make", "--no-print-directory", "help"],
            cwd=self.ws, capture_output=True, text=True, check=True,
        )
        self.assertIn("genesis", help_res.stdout)
        self.assertIn("epics", help_res.stdout)
        go_header = "\n".join((self.ws / "go").read_text(encoding="utf-8").splitlines()[:60])
        self.assertIn("./go genesis", go_header)
        self.assertIn("./go epics", go_header)
        self.assertIn("--workspace", go_header)

        for target, script in (("genesis", "core/genesis.sh"), ("epics", "core/epics.sh")):
            dry = subprocess.run(
                ["make", "--no-print-directory", "-n", target],
                cwd=self.ws, capture_output=True, text=True, check=True,
            )
            self.assertIn(script, dry.stdout)

        bindir, tools, go_log = self._setup_fake_podman()
        go_env = {"PATH": f"{bindir}:{tools}", "HOME": str(self.tmp)}

        # ./go init --workspace stays read-only when --apply is omitted
        ext_repo = self.tmp / "mono-target"
        ext_repo.mkdir()
        subprocess.run(["git", "-C", str(ext_repo), "init", "-q"], check=True)
        (ext_repo / "README.md").write_text("# mono\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(ext_repo), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(ext_repo), "-c", "user.name=t", "-c", "user.email=t@invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed"],
            check=True,
        )
        res_ws = subprocess.run(
            ["sh", "./go", "init", str(ext_repo), "--workspace"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_ws.returncode, 0, res_ws.stderr)
        argv_ws = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_ws = _extract_env_flags(argv_ws)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_ws)
        self.assertFalse(any(m.startswith(f"{ext_repo.resolve()}:") and ":rw" in m for m in argv_ws), argv_ws)

        # ./go genesis mounts host dir :rw cleanly even without git
        gen_dir = self.tmp / "new-app"
        gen_dir.mkdir()
        go_log.unlink(missing_ok=True)
        res_gen = subprocess.run(
            ["sh", "./go", "genesis", str(gen_dir), "--brief", "notes api", "--no-commit"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_gen.returncode, 0, res_gen.stderr)
        argv_gen = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        resolved_gen = str(gen_dir.resolve())
        self.assertTrue(
            any(m == f"{resolved_gen}:{resolved_gen}:rw" or m.startswith(f"{resolved_gen}:{resolved_gen}:rw,") for m in argv_gen),
            argv_gen,
        )
        envs_gen = _extract_env_flags(argv_gen)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_gen)
        self.assertEqual(argv_gen[-2:], ["make", "genesis"])

        # ./go epics forwards LIGHTS_OUT_CLI_ARGS
        go_log.unlink(missing_ok=True)
        res_ep = subprocess.run(
            ["sh", "./go", "epics", "--id", "E1", "--title", "Core"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_ep.returncode, 0, res_ep.stderr)
        argv_ep = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_ep = _extract_env_flags(argv_ep)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_ep)
        self.assertEqual(argv_ep[-2:], ["make", "epics"])

        # ./go console and ./go production-readiness forward CONSOLE_* OIDC/RBAC vars by name only
        secret_canary = "wave-b-jwt-hmac-secret-99999"
        oidc_env = {
            **go_env,
            "CONSOLE_AUTH": "proxy",
            "CONSOLE_VERIFY_JWT": "on",
            "CONSOLE_JWKS_URL": "https://idp.invalid/.well-known/jwks.json",
            "CONSOLE_JWT_SECRET": secret_canary,
            "CONSOLE_RBAC": "strict",
            "CONSOLE_VIEWERS": "viewer@invalid",
            "CONSOLE_AUDIT_LOG": ".crew/console-audit.jsonl",
        }
        for cmd_name in ("console", "production-readiness"):
            go_log.unlink(missing_ok=True)
            res_c = subprocess.run(
                ["sh", "./go", cmd_name],
                cwd=self.ws, env=oidc_env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(res_c.returncode, 0, res_c.stderr)
            argv_c = next(c for c in _read_calls(go_log) if c and c[0] == "run")
            self.assertNotIn(secret_canary, " ".join(argv_c), "secret value leaked onto podman CLI")
            flags_c = _extract_env_flags(argv_c)
            for key in ("CONSOLE_AUTH", "CONSOLE_VERIFY_JWT", "CONSOLE_JWKS_URL",
                        "CONSOLE_JWT_SECRET", "CONSOLE_RBAC", "CONSOLE_VIEWERS", "CONSOLE_AUDIT_LOG"):
                self.assertIn(key, flags_c, f"{key} missing on {cmd_name}")

    def test_sprint4_wave_b_spec_split_m24_and_watch_workers_wiring(self) -> None:
        help_res = subprocess.run(
            ["make", "--no-print-directory", "help"],
            cwd=self.ws, capture_output=True, text=True, check=True,
        )
        self.assertIn("spec-split", help_res.stdout)
        self.assertIn("m24-check", help_res.stdout)
        go_header = "\n".join((self.ws / "go").read_text(encoding="utf-8").splitlines()[:60])
        self.assertIn("./go spec-split", go_header)
        self.assertIn("./go m24-check", go_header)

        for target, script in (("spec-split", "core/spec_split.sh"), ("m24-check", "core/m24_runner.sh")):
            dry = subprocess.run(
                ["make", "--no-print-directory", "-n", target],
                cwd=self.ws, capture_output=True, text=True, check=True,
            )
            self.assertIn(script, dry.stdout)

        dry_watch = subprocess.run(
            ["make", "--no-print-directory", "-n", "watch", "ONCE=1", "RUNNER_WORKERS=3", "WORKERS=4"],
            cwd=self.ws, capture_output=True, text=True, check=True,
        )
        self.assertIn('RUNNER_WORKERS="3"', dry_watch.stdout)
        self.assertIn('--workers "4"', dry_watch.stdout)

        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}
        crew = self.ws / ".crew"
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "start-plan.env").write_text(
            "START_PROFILE=l0\nDELIVERY=outside/gcp-cloud-deploy\nADAPTERS_PATH="
            + str((self.ws / "plugins" / "cloud").resolve())
            + "\n",
            encoding="utf-8",
        )
        missing_wl = self.tmp / "empty-contract-s4"
        missing_wl.mkdir(exist_ok=True)

        ticket_path = self.tmp / "s4-ticket.md"
        ticket_path.write_text(
            "# 0900: demo\n\n## Perimeter\n\n- src/app.py\n- tests/test_app.py\n",
            encoding="utf-8",
        )
        res_ss = subprocess.run(
            ["make", "--no-print-directory", "spec-split", f"WORKLOAD_CONTRACT={missing_wl}",
             f"SPEC_SPLIT_ARGS=check {ticket_path} --json"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_ss.returncode, 0, res_ss.stdout + res_ss.stderr)
        self.assertTrue(json.loads(res_ss.stdout)["ok"])

        res_m24 = subprocess.run(
            ["make", "--no-print-directory", "m24-check", f"WORKLOAD_CONTRACT={missing_wl}",
             "M24_ARGS=evaluate --allow-incomplete --json"],
            cwd=self.ws, env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_m24.returncode, 0, res_m24.stdout + res_m24.stderr)
        self.assertEqual(json.loads(res_m24.stdout)["verdict"], "incomplete")

        bindir, tools, go_log = self._setup_fake_podman()
        go_env = {
            "PATH": f"{bindir}:{tools}",
            "HOME": str(self.tmp),
            "RUNNER_WORKERS": "3",
            "INTAKE_NETWORK_SPLIT": "on",
            "NETWORK_AUTO_SPLIT": "on",
        }
        res_go_ss = subprocess.run(
            ["sh", "./go", "spec-split", "check", "tickets/0001.md", "--json"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_go_ss.returncode, 0, res_go_ss.stderr)
        argv_ss = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs_ss = _extract_env_flags(argv_ss)
        self.assertIn("LIGHTS_OUT_CLI_ARGS", envs_ss)
        self.assertIn("RUNNER_WORKERS", envs_ss)
        self.assertIn("INTAKE_NETWORK_SPLIT", envs_ss)
        self.assertIn("NETWORK_AUTO_SPLIT", envs_ss)
        self.assertEqual(argv_ss[-2:], ["make", "spec-split"])

        go_log.unlink(missing_ok=True)
        res_go_w = subprocess.run(
            ["sh", "./go", "watch", "--once", "--workers", "4"],
            cwd=self.ws, env=go_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_go_w.returncode, 0, res_go_w.stderr)
        argv_w = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        self.assertEqual(argv_w[-4:], ["make", "watch", "ONCE=1", "WORKERS=4"])


if __name__ == "__main__":
    unittest.main()
