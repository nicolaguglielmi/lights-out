"""Unit tests for ADR 0108 UX Glue: start-plan.env Make wiring, wrapper --engine, and promote clarity.

Strictly vendor-free, app-free, and model-free (ci/vendor-free.sh,
ci/app-free.sh, ci/model-free.sh). Uses synthetic workloads, synthetic
profiles, and fake engine binaries.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(0o755)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@invalid",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def _read_calls(log_path: Path) -> list[list[str]]:
    if not log_path.is_file():
        return []
    raw = log_path.read_bytes()
    if not raw:
        return []
    calls: list[list[str]] = []
    for chunk in raw.split(b"\x00\x00"):
        if not chunk:
            continue
        args = [part.decode("utf-8", errors="replace") for part in chunk.split(b"\x00") if part]
        if args:
            calls.append(args)
    return calls


def _extract_mount_pairs(argv: list[str]) -> list[str]:
    mounts: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "-v" and i + 1 < len(argv):
            mounts.append(argv[i + 1])
            i += 2
        elif argv[i].startswith("-v="):
            mounts.append(argv[i][3:])
            i += 1
        else:
            i += 1
    return mounts


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


class PromoteHintAndShellBoundaryTests(unittest.TestCase):
    """ADR 0108 §3: promote_hint.py emits clear stderr guidance; promote.sh is a thin wrapper."""

    def test_promote_sh_code_line_count_respects_shell_boundary(self) -> None:
        lines = (ROOT / "core" / "promote.sh").read_text(encoding="utf-8").splitlines()
        code_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        self.assertLessEqual(len(code_lines), 25, f"core/promote.sh has {len(code_lines)} code lines, want <= 25")

    def test_promote_hint_stdout_empty_and_guides_local_proxy_vs_external(self) -> None:
        hint_py = ROOT / "core" / "promote_hint.py"
        self.assertTrue(hint_py.is_file(), "core/promote_hint.py does not exist")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            rec = tmp / "release.json"
            rec.write_text(
                json.dumps({"version": "0.2.0-rc1", "stable": "0.1.0", "verdict": "held", "held_at": 25}),
                encoding="utf-8",
            )
            # Local edge proxy adapter (no slo.promql in adapters/<name>)
            _write(tmp / "adapters" / "local-edge" / "ready.sh", "#!/bin/sh\nexit 0\n", executable=True)
            res_local = subprocess.run(
                [sys.executable, str(hint_py), "0.2.0-rc1"],
                cwd=tmp,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "DELIVERY": "local-edge",
                    "LEVEL": "l0",
                    "RELEASE_RECORD": str(rec),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_local.returncode, 0, res_local.stderr)
            self.assertEqual(res_local.stdout, "")
            self.assertIn("[go-for-launch] 0.2.0-rc1 at 100% of traffic (local-edge)", res_local.stderr)
            self.assertIn("0.1.0", res_local.stderr)
            self.assertIn("stable", res_local.stderr.lower())

            # External / attached repo promotion
            res_ext = subprocess.run(
                [sys.executable, str(hint_py), "0.2.0-rc1"],
                cwd=tmp,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "DELIVERY": "outside/cloud-ship",
                    "LEVEL": "l2",
                    "WORKLOAD_ATTACHED": "1",
                    "RELEASE_RECORD": str(rec),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res_ext.returncode, 0, res_ext.stderr)
            self.assertEqual(res_ext.stdout, "")
            self.assertIn("[go-for-launch] 0.2.0-rc1 at 100% of traffic (outside/cloud-ship)", res_ext.stderr)
            self.assertIn("attached", res_ext.stderr.lower())
            self.assertIn("./go evidence VERSION=0.2.0-rc1", res_ext.stderr)

    def test_promote_sh_preserves_exact_stdout_and_prints_hint_on_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "core").symlink_to(ROOT / "core")
            (root / "ci").symlink_to(ROOT / "ci")
            _write(root / "roles" / "release" / "role.env", "THINKING=low\nTIMEOUT_S=60\n")
            _write(
                root / "adapters" / "local-edge" / "status.sh",
                '#!/bin/sh\necho \'{"stable":"0.1.0","canary":"0.2.0-rc1","weight":25}\'\n',
                executable=True,
            )
            _write(root / "adapters" / "local-edge" / "ready.sh", "#!/bin/sh\nexit 0\n", executable=True)
            _write(root / "adapters" / "local-edge" / "shift.sh", "#!/bin/sh\nexit 0\n", executable=True)
            _write(root / "adapters" / "local-edge" / "rollback.sh", "#!/bin/sh\nexit 0\n", executable=True)
            rec = root / "release.json"
            rec.write_text(
                json.dumps(
                    {
                        "version": "0.2.0-rc1",
                        "stable": "0.1.0",
                        "verdict": "held",
                        "held_at": 25,
                        "image": "localhost:5050/demo:1",
                        "stages": [{"pct": 5, "verdict": "GO"}, {"pct": 25, "verdict": "GO"}],
                    }
                ),
                encoding="utf-8",
            )
            res = subprocess.run(
                ["sh", "core/promote.sh"],
                cwd=root,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "DELIVERY": "local-edge",
                    "AGENT_HARNESS": "replay",
                    "GO_FOR_LAUNCH": "GO",
                    "RELEASE_RECORD": str(rec),
                    "RELEASES_LOG": str(root / "releases.jsonl"),
                    "PROMOTE_OUT": str(root / "promote-out"),
                },
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(res.stdout, "GO: go-for-launch\n")
            self.assertIn("[go-for-launch] 0.2.0-rc1 at 100% of traffic (local-edge)", res.stderr)
            self.assertIn("stable", res.stderr.lower())


class MakefileStartPlanWiringTests(unittest.TestCase):
    """ADR 0108 §1: Makefile loads .crew/start-plan.env with strict precedence, exclusions, and secret refusal."""

    def setUp(self) -> None:
        if not shutil.which("make"):
            self.fail("make is not on PATH: this test runs in the workbench, ./go ci")
        self.tmp = Path(tempfile.mkdtemp(prefix="mk-wiring-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        shutil.copy2(ROOT / "Makefile", self.tmp / "Makefile")
        shutil.copytree(ROOT / "core", self.tmp / "core", ignore=shutil.ignore_patterns("__pycache__", "tests"))
        shutil.copytree(ROOT / "policy", self.tmp / "policy")
        _write(
            self.tmp / "profiles" / "l0.env",
            "LEVEL=l0\nMODEL_PROVIDER=\nMODEL=\nAGENT_HARNESS=replay\nCODE_HOST=local-host\n"
            "TRACKER=file\nEVENT_BUS=spool\nSTATE_STORE=sqlite\nORCHESTRATOR=inproc\n"
            "DELIVERY=local-proxy\nMETRICS=promql\nFLAGS=flags-file\nREGISTRY=local-reg\n"
            "MAP_BRIEF=off\nDAST=off\nDECIDE=replay\n",
        )
        _write(
            self.tmp / "profiles" / "l1.env",
            "LEVEL=l1\nMODEL_PROVIDER=alpha\nMODEL=alpha-fast\nAGENT_HARNESS=alpha-cli\nCODE_HOST=local-host\n"
            "TRACKER=file\nEVENT_BUS=spool\nSTATE_STORE=sqlite\nORCHESTRATOR=inproc\n"
            "DELIVERY=local-proxy\nMETRICS=promql\nFLAGS=flags-file\nREGISTRY=local-reg\n"
            "MAP_BRIEF=off\nDAST=off\nDECIDE=structured\n",
        )
        _write(
            self.tmp / "wl" / "workload.env",
            "TOOLCHAIN_IMAGE=workbench\nMUTATION_BASELINE=0.80\nWORKLOAD_HEALTH_PROBE=http:/ok\nWORKLOAD_SAMPLE_PATH=/ok\n",
        )
        _write(self.tmp / "wl" / "compose.yaml", "services:\n  backend:\n    image: example/svc:1\n")
        _write(self.tmp / "probe.mk", "include Makefile\n.PHONY: show-vars demo-probe ci-probe e2e corpus\n"
               "show-vars:\n\t@printf 'PROFILE=%s|MODEL_PROVIDER=%s|MODEL=%s|AGENT_HARNESS=%s|DELIVERY=%s|DECIDE=%s|CODE_HOST=%s|APP_PORT=%s|AUTO_INCIDENT_ON_ROLLBACK=%s|SPEND_BUDGET_TICKET_USD=%s|NO_PLAN=%s\\n' "
               "\"$(PROFILE)\" \"$(MODEL_PROVIDER)\" \"$(MODEL)\" \"$(AGENT_HARNESS)\" \"$(DELIVERY)\" \"$(DECIDE)\" \"$(CODE_HOST)\" \"$(APP_PORT)\" \"$(AUTO_INCIDENT_ON_ROLLBACK)\" \"$(SPEND_BUDGET_TICKET_USD)\" \"$(LIGHTS_OUT_NO_START_PLAN)\"\n"
               "demo-probe:\n\t@$(MAKE) --no-print-directory -f probe.mk show-vars WORKLOAD=wl WORKLOAD_CONTRACT=wl\n"
               "ci-probe:\n\t@$(MAKE) --no-print-directory -f probe.mk show-vars WORKLOAD=wl WORKLOAD_CONTRACT=wl\n"
               "e2e:\n\t@$(MAKE) --no-print-directory -f probe.mk show-vars WORKLOAD=wl WORKLOAD_CONTRACT=wl\n"
               "corpus:\n\t@$(MAKE) --no-print-directory -f probe.mk show-vars WORKLOAD=wl WORKLOAD_CONTRACT=wl\n")
        _git(self.tmp, "init", "-q")
        _git(self.tmp, "add", "-A")
        _git(self.tmp, "commit", "-q", "-m", "init")

    def _run_make(self, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)}
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["make", "--no-print-directory", "-f", "probe.mk", *args, "WORKLOAD=wl", "WORKLOAD_CONTRACT=wl"],
            cwd=self.tmp,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_precedence_and_start_profile_honored(self) -> None:
        _write(
            self.tmp / ".crew" / "start-plan.env",
            "START_PROFILE=l1\nMODEL_PROVIDER=beta\nMODEL=beta-reason\nAGENT_HARNESS=beta-cli\n"
            "DELIVERY=plan-proxy\nDECIDE=structured\nAUTO_INCIDENT_ON_ROLLBACK=on\nSPEND_BUDGET_TICKET_USD=4.50\n",
        )
        res = self._run_make("show-vars")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(
            res.stdout.strip(),
            "PROFILE=l1|MODEL_PROVIDER=beta|MODEL=beta-reason|AGENT_HARNESS=beta-cli|"
            "DELIVERY=plan-proxy|DECIDE=structured|CODE_HOST=local-host|APP_PORT=8080|"
            "AUTO_INCIDENT_ON_ROLLBACK=on|SPEND_BUDGET_TICKET_USD=4.50|NO_PLAN=",
        )

        # ports.env, remote.env, .env, and CLI overrides take precedence over start-plan.env
        _write(self.tmp / ".crew" / "ports.env", "APP_PORT=8089\n")
        _write(self.tmp / ".crew" / "remote.env", "CODE_HOST=remote-host\n")
        _write(self.tmp / ".env", "DELIVERY=dotenv-proxy\n")
        res2 = self._run_make("show-vars", "AGENT_HARNESS=cli-harness")
        self.assertEqual(res2.returncode, 0, res2.stderr)
        self.assertIn("DELIVERY=dotenv-proxy", res2.stdout)
        self.assertIn("AGENT_HARNESS=cli-harness", res2.stdout)
        self.assertIn("APP_PORT=8089", res2.stdout)
        self.assertIn("CODE_HOST=remote-host", res2.stdout)

        # Explicit PROFILE=l0 overrides START_PROFILE=l1
        res3 = self._run_make("show-vars", "PROFILE=l0")
        self.assertEqual(res3.returncode, 0, res3.stderr)
        self.assertIn("PROFILE=l0|", res3.stdout)

    def test_demo_ci_e2e_corpus_ignore_start_plan_env_even_recursively(self) -> None:
        _write(
            self.tmp / ".crew" / "start-plan.env",
            "START_PROFILE=l1\nMODEL_PROVIDER=beta\nDELIVERY=plan-proxy\nAUTO_INCIDENT_ON_ROLLBACK=on\n",
        )
        for goal in ("demo-probe", "ci-probe", "e2e", "corpus"):
            res = self._run_make(goal)
            self.assertEqual(res.returncode, 0, f"{goal}: {res.stdout} {res.stderr}")
            self.assertIn("PROFILE=l0|MODEL_PROVIDER=|MODEL=|AGENT_HARNESS=replay|DELIVERY=local-proxy|", res.stdout, goal)
            self.assertIn("NO_PLAN=1", res.stdout, goal)

    def test_make_refuses_secret_keys_in_start_plan_env(self) -> None:
        _write(self.tmp / ".crew" / "start-plan.env", "START_PROFILE=l0\nFOO_TOKEN=secret-123\n")
        res = self._run_make("show-vars")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("secret-123", res.stdout)
        self.assertIn("start-plan.env", res.stderr)


class WrapperEngineOptInTests(unittest.TestCase):
    """ADR 0108 §2: default gates stay socket-free; explicit --engine / LIGHTS_OUT_ENGINE_GATES=1 opts in."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wrap-engine-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in (
            "sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut", "cksum",
            "cat", "uname", "basename", "dirname", "mkdir", "rm", "mktemp", "id",
            "env", "wc", "stat", "tar", "ln", "cp", "git",
        ):
            found = shutil.which(tool)
            if found:
                (self.tools / tool).symlink_to(found)

        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q")
        (self.repo / "README.md").write_text("# sample\n", encoding="utf-8")
        _git(self.repo, "add", "README.md")
        _git(self.repo, "commit", "-q", "-m", "initial")

        self.out = self.tmp / "out"
        self.wrapper = ROOT / "lights-out"
        with (ROOT / "pyproject.toml").open("rb") as fh:
            self.version = tomllib.load(fh)["project"]["version"]

    def _make_fake_engine(self, name: str = "podman") -> tuple[Path, Path]:
        bindir = self.tmp / f"bin-{name}"
        bindir.mkdir(exist_ok=True)
        log = self.tmp / f"calls-{name}.bin"
        script = bindir / name
        script.write_text(
            f"""#!/bin/sh
for a do printf '%s\\0' "$a" >> {str(log)!r}; done
printf '\\0' >> {str(log)!r}
case "$*" in
  *"RemoteSocket.Path"*) printf '%s\\n' "${{FAKE_REMOTE_SOCK:-/tmp/fake-podman.sock}}"; exit 0 ;;
  *"RemoteSocket.Exists"*) printf '%s\\n' "${{FAKE_REMOTE_EXISTS:-true}}"; exit 0 ;;
  *"Client.Version"*) printf '5.0.0\\n'; exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bindir, log

    def _run(self, args: list[str], bindir: Path, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        env = {"PATH": f"{bindir}:{self.tools}", "HOME": str(self.tmp), "LIGHTS_OUT_OUT": str(self.out)}
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["sh", str(self.wrapper), *args],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_engine_flag_only_allowed_on_gate_commands_and_refused_before_engine(self) -> None:
        bindir, log = self._make_fake_engine("podman")
        for bad in (["up", "--engine"], ["next", "--engine"], ["doctor", "--engine"], ["triage", "--engine"], ["version", "--engine"]):
            res = self._run(bad, bindir)
            self.assertNotEqual(res.returncode, 0, f"{bad} unexpectedly succeeded")
            self.assertEqual(res.stdout, "", f"{bad} wrote stdout")
            self.assertFalse(log.exists(), f"{bad} touched engine")

    def test_default_gates_stay_socket_free_while_engine_flag_and_env_opt_in(self) -> None:
        bindir, log = self._make_fake_engine("podman")
        fake_sock = str(self.tmp / "fake-podman.sock")

        # Default remains socket-free on gates image
        for cmd in ("init", "gates", "test-proof", "test-integrity"):
            log.unlink(missing_ok=True)
            res = self._run([cmd], bindir)
            self.assertEqual(res.returncode, 0, res.stderr)
            argv = next(c for c in _read_calls(log) if c and c[0] == "run")
            self.assertFalse(any(".sock" in m for m in _extract_mount_pairs(argv)), cmd)
            self.assertFalse(any(e.startswith("DOCKER_HOST") for e in _extract_env_flags(argv)), cmd)
            self.assertIn(f"ghcr.io/nicolaguglielmi/lights-out-gates:{self.version}", argv)
            self.assertEqual([ln for ln in res.stderr.splitlines() if "socket" in ln.lower()], [])

        # Opt-in via --engine and LIGHTS_OUT_ENGINE_GATES=1 mounts socket, platform/crew, and prints 1-line notice
        cases = [
            (["gates", "--engine"], {}),
            (["init", "--engine"], {}),
            (["test-proof", "--engine"], {}),
            (["test-integrity", "--engine"], {}),
            (["gates"], {"LIGHTS_OUT_ENGINE_GATES": "1"}),
        ]
        for args, extra in cases:
            log.unlink(missing_ok=True)
            res = self._run(args, bindir, extra_env={"FAKE_REMOTE_SOCK": fake_sock, "FAKE_REMOTE_EXISTS": "true", **extra})
            self.assertEqual(res.returncode, 0, f"{args}: {res.stderr}")
            argv = next(c for c in _read_calls(log) if c and c[0] == "run")
            mounts = _extract_mount_pairs(argv)
            envs = _extract_env_flags(argv)
            plat = str(self.out / "platform")
            crew = str(self.out / "crew")
            self.assertIn(f"{fake_sock}:/var/run/docker.sock", mounts)
            self.assertIn("DOCKER_HOST=unix:///var/run/docker.sock", envs)
            self.assertIn("LIGHTS_OUT_ENGINE_GATES=1", envs)
            self.assertIn(f"LIGHTS_OUT_PLATFORM_DIR={plat}", envs)
            self.assertIn(f"LIGHTS_OUT_CREW_DIR={crew}", envs)
            self.assertIn(f"{self.repo}:/repo:ro", mounts)
            self.assertIn(f"{self.out}:/out", mounts)
            self.assertIn(f"{plat}:{plat}", mounts)
            self.assertIn(f"ghcr.io/nicolaguglielmi/lights-out:{self.version}", argv)
            notice_lines = [ln for ln in res.stderr.splitlines() if "socket" in ln.lower()]
            self.assertEqual(len(notice_lines), 1, f"{args}: {res.stderr!r}")

    def test_gates_engine_missing_socket_fails_cleanly_on_podman_and_docker(self) -> None:
        bindir_p, log_p = self._make_fake_engine("podman")
        res_p = self._run(["gates", "--engine"], bindir_p, extra_env={"FAKE_REMOTE_EXISTS": "false", "FAKE_REMOTE_SOCK": "/missing.sock"})
        self.assertNotEqual(res_p.returncode, 0)
        self.assertEqual(res_p.stdout, "")
        self.assertEqual(len(res_p.stderr.splitlines()), 1)
        self.assertIn("socket", res_p.stderr.lower())
        self.assertFalse(any(c and c[0] == "run" for c in _read_calls(log_p)))

        bindir_d, log_d = self._make_fake_engine("docker")
        sock_path = self.tmp / "docker.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        self.addCleanup(srv.close)

        res_d_ok = self._run(["gates", "--engine"], bindir_d, extra_env={"CONTAINER_ENGINE": "docker", "DOCKER_HOST": f"unix://{sock_path}"})
        self.assertEqual(res_d_ok.returncode, 0, res_d_ok.stderr)
        argv = next(c for c in _read_calls(log_d) if c and c[0] == "run")
        self.assertIn("--group-add", argv)
        self.assertIn("host.containers.internal:host-gateway", argv)


if __name__ == "__main__":
    unittest.main()
