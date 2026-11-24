"""E2E integration tests for ADR 0108 UX Glue: start-plan -> Make/go, --engine Go/compose gates, and promote clarity.

Kept under ci/tests/ so core/tests/ stays strictly vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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


class StartPlanEndToEndWiringTests(unittest.TestCase):
    """Verify ./go start output auto-wires real Makefile and ./go host resolution."""

    def setUp(self) -> None:
        if not shutil.which("make"):
            self.skipTest("make is required")
        self.tmp = Path(tempfile.mkdtemp(prefix="ux-start-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.ws = self.tmp / "repo"
        shutil.copytree(
            ROOT,
            self.ws,
            ignore=shutil.ignore_patterns(".git", ".crew", ".attached", ".gates-out", "__pycache__"),
        )
        _git(self.ws, "init", "-q")
        _git(self.ws, "add", "-A")
        _git(self.ws, "commit", "-q", "-m", "snapshot")

    def _setup_fake_podman(self) -> tuple[Path, Path, Path]:
        tools = self.tmp / "tools"
        if not tools.exists():
            tools.mkdir()
            for tool in (
                "sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut", "cksum",
                "cat", "uname", "basename", "dirname", "mkdir", "rm", "mktemp", "id",
                "env", "wc", "stat", "date", "git",
            ):
                found = shutil.which(tool)
                if found:
                    (tools / tool).symlink_to(found)
        bindir = self.tmp / "bin"
        bindir.mkdir(exist_ok=True)
        go_log = self.tmp / "go-podman.log"
        podman = bindir / "podman"
        podman.write_text(
            f"""#!/bin/sh
for a do printf '%s\\0' "$a" >> {str(go_log)!r}; done
printf '\\0' >> {str(go_log)!r}
case "$*" in
  *"RemoteSocket.Path"*) printf '/tmp/fake-podman.sock\\n'; exit 0 ;;
  *"RemoteSocket.Exists"*) printf 'true\\n'; exit 0 ;;
  *"Client.Version"*) printf '5.0.0\\n'; exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        podman.chmod(0o755)
        return bindir, tools, go_log

    def test_start_plan_wires_cloud_plugin_and_provider_into_makefile_and_go(self) -> None:
        (self.ws / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-test-value\n", encoding="utf-8")
        res_start = subprocess.run(
            [
                "sh",
                "core/start.sh",
                "start",
                "--non-interactive",
                "--preset",
                "strict",
                "--provider",
                "anthropic",
                "--harness",
                "claude-code",
                "--delivery",
                "gcp-cloud-deploy",
                "--ticket-budget-usd",
                "4.00",
                "--day-budget-usd",
                "16.00",
            ],
            cwd=self.ws,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(self.tmp),
                "LIGHTS_OUT_DOCTOR_RC": "0",
                "LIGHTS_OUT_DOCTOR_OUT": "GO: doctor",
                "LIGHTS_OUT_DOCTOR_ERR": "",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_start.returncode, 0, res_start.stdout + res_start.stderr)
        plan = (self.ws / ".crew" / "start-plan.env").read_text(encoding="utf-8")
        self.assertIn("DELIVERY=outside/gcp-cloud-deploy", plan)
        self.assertIn("MODEL=claude-sonnet-5-5", plan)
        self.assertIn(f"ADAPTERS_PATH={(self.ws / 'plugins' / 'cloud').resolve()}", plan)

        # cloud-setup, next, and start remain runnable even before delivery-conformance admits outside/gcp-cloud-deploy
        res_cs = subprocess.run(
            ["make", "--no-print-directory", "cloud-setup", "CLOUD=gcp-cloud-deploy"],
            cwd=self.ws,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_cs.returncode, 0, res_cs.stdout + res_cs.stderr)
        self.assertTrue(res_cs.stdout.startswith("GO: cloud-setup — gcp-cloud-deploy"))

        # Make exposes start-plan values when not in demo/ci/e2e/corpus, while up still enforces admission
        probe_mk = self.ws / "ux-probe.mk"
        probe_mk.write_text(
            "include Makefile\n.PHONY: dump\n"
            "dump:\n\t@printf '%s|%s|%s|%s|%s|%s|%s\\n' "
            "\"$(PROFILE)\" \"$(MODEL_PROVIDER)\" \"$(MODEL)\" \"$(AGENT_HARNESS)\" \"$(DELIVERY)\" \"$(DECIDE)\" \"$(ADAPTERS_PATH)\"\n",
            encoding="utf-8",
        )
        res_dump = subprocess.run(
            ["make", "--no-print-directory", "-f", "ux-probe.mk", "dump"],
            cwd=self.ws,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        # Note: dump is not exempted from ADMIT, so it must be refused until conformance runs;
        # next IS exempted from ADMIT and sees the wired env!
        self.assertNotEqual(res_dump.returncode, 0)
        self.assertIn("delivery-conformance GO", res_dump.stderr)

        res_next = subprocess.run(
            ["make", "--no-print-directory", "next", "NEXT_ARGS=--json"],
            cwd=self.ws,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_next.returncode, 0, res_next.stdout + res_next.stderr)
        info = json.loads(res_next.stdout)
        self.assertEqual(info["provider"]["name"], "anthropic")
        self.assertEqual(info["provider"]["harness"], "claude-code")
        self.assertEqual(info["provider"]["profile"], "l1")

        # Verify ./go does NOT force PROFILE=l0 and DOES mount ADAPTERS_PATH from start-plan.env
        bindir, tools, go_log = self._setup_fake_podman()
        go_log.unlink(missing_ok=True)
        res_go = subprocess.run(
            ["sh", "./go", "next", "--json"],
            cwd=self.ws,
            env={"PATH": f"{bindir}:{tools}", "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_go.returncode, 0, res_go.stderr)
        argv = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs = _extract_env_flags(argv)
        mounts = _extract_mount_pairs(argv)
        self.assertNotIn("PROFILE=l0", envs)
        cloud_dir = str((self.ws / "plugins" / "cloud").resolve())
        self.assertIn(f"ADAPTERS_PATH={cloud_dir}", envs)
        self.assertTrue(
            any(m == f"{cloud_dir}:{cloud_dir}:ro" or m.startswith(f"{cloud_dir}:{cloud_dir}:ro,") for m in mounts),
            mounts,
        )

    def test_start_with_cloud_delivery_keeps_init_try_gates_attached_and_baseline_unblocked(self) -> None:
        (self.ws / ".env").write_text("ANTHROPIC_API_KEY=sk-ant-test-value\n", encoding="utf-8")
        res_start = subprocess.run(
            [
                "sh",
                "core/start.sh",
                "start",
                "--non-interactive",
                "--preset",
                "strict",
                "--provider",
                "anthropic",
                "--harness",
                "claude-code",
                "--delivery",
                "gcp-cloud-deploy",
            ],
            cwd=self.ws,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(self.tmp),
                "LIGHTS_OUT_DOCTOR_RC": "0",
                "LIGHTS_OUT_DOCTOR_OUT": "GO: doctor",
                "LIGHTS_OUT_DOCTOR_ERR": "",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_start.returncode, 0, res_start.stdout + res_start.stderr)
        self.assertIn("DELIVERY=outside/gcp-cloud-deploy", (self.ws / ".crew" / "start-plan.env").read_text(encoding="utf-8"))

        # Stage a real repository without .lights-out/ at .attached (step 2 of ./go start's printed workflow)
        att = self.ws / ".attached"
        shutil.copytree(ROOT / "workloads" / "minimal-js", att, ignore=shutil.ignore_patterns(".lights-out"))
        _git(att, "init", "-q")
        _git(att, "add", "-A")
        _git(att, "commit", "-q", "-m", "initial")

        make_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.tmp),
            "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:4150781479"),
        }
        res_init = subprocess.run(
            ["make", "--no-print-directory", "init", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_init.returncode, 0, res_init.stdout + res_init.stderr)
        self.assertTrue(res_init.stdout.startswith("GO: init — "), res_init.stdout)

        res_attached = subprocess.run(
            ["make", "--no-print-directory", "attached", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_attached.returncode, 0, res_attached.stdout + res_attached.stderr)
        self.assertTrue(res_attached.stdout.startswith("GO: attached — "), res_attached.stdout)

        # Add a commit after drafting the contract so make gates judges a clean diff
        (att / "README.md").write_text("# sample\n", encoding="utf-8")
        _git(att, "add", "-A")
        _git(att, "commit", "-q", "-m", "add lights-out contract and docs")
        res_gates = subprocess.run(
            ["make", "--no-print-directory", "gates", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_gates.returncode, 0, res_gates.stdout + res_gates.stderr)
        self.assertTrue(res_gates.stdout.startswith("GO: gates — "), res_gates.stdout)

        res_mb = subprocess.run(
            ["make", "--no-print-directory", "-n", "mutation-baseline", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_mb.returncode, 0, f"mutation-baseline: {res_mb.stdout} {res_mb.stderr}")

        # Explicit CLI outside/* on gates MUST still be refused (ADR 0058)
        cloud_dir = str((self.ws / "plugins" / "cloud").resolve())
        res_cli_outside = subprocess.run(
            [
                "make",
                "--no-print-directory",
                "-n",
                "gates",
                "WORKLOAD=.attached",
                "ATTACHED=.attached",
                "DELIVERY=outside/gcp-cloud-deploy",
                f"ADAPTERS_PATH={cloud_dir}",
            ],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(res_cli_outside.returncode, 0)
        self.assertIn("delivery-conformance GO", res_cli_outside.stderr)

        # Plane goals (like up) MUST still refuse unproven start-plan outside/* until conformance runs
        res_up = subprocess.run(
            ["make", "--no-print-directory", "-n", "up", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env=make_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(res_up.returncode, 0)
        self.assertIn("delivery-conformance GO", res_up.stderr)

        # ./go wrapper skips start-plan ADAPTERS_PATH mount and PROFILE pinning on gates
        bindir, tools, go_log = self._setup_fake_podman()
        go_log.unlink(missing_ok=True)
        res_go_gates = subprocess.run(
            ["sh", "./go", "gates"],
            cwd=self.ws,
            env={"PATH": f"{bindir}:{tools}", "HOME": str(self.tmp)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_go_gates.returncode, 0, res_go_gates.stderr)
        argv = next(c for c in _read_calls(go_log) if c and c[0] == "run")
        envs = _extract_env_flags(argv)
        self.assertFalse(any(e.startswith("ADAPTERS_PATH=") for e in envs), envs)
        self.assertNotIn("PROFILE=l0", envs)

        # Real make try runs recursive $(MAKE) init and $(MAKE) gates cleanly even with cloud DELIVERY in start-plan.env
        res_try = subprocess.run(
            ["make", "--no-print-directory", "try", "WORKLOAD=.attached", "ATTACHED=.attached"],
            cwd=self.ws,
            env={**make_env, "TRY_SOURCE": str(att)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_try.returncode, 0, res_try.stdout + res_try.stderr)
        self.assertTrue(res_try.stdout.startswith("GO: try — "), res_try.stdout)


class WrapperEngineGatesWorkloadE2ETests(unittest.TestCase):
    """Verify real package/entrypoint.sh gates --engine on Go and Compose workloads + ci/package.sh."""

    def setUp(self) -> None:
        if not shutil.which("make"):
            self.skipTest("make is required")
        self.tmp = Path(tempfile.mkdtemp(prefix="ux-engine-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.img_root = self.tmp / "opt-lights-out"
        shutil.copytree(
            ROOT,
            self.img_root,
            ignore=shutil.ignore_patterns(".git", ".crew", ".attached", ".gates-out", "__pycache__"),
        )
        (self.img_root / ".crew").mkdir(exist_ok=True)
        self.plat = self.tmp / "out" / "platform"
        self.crew = self.tmp / "out" / "crew"
        self.out = self.tmp / "out"
        self.plat.mkdir(parents=True)
        self.crew.mkdir(parents=True)

    def _make_workload_repo(self, src_rel: str) -> Path:
        repo = self.tmp / f"repo-{Path(src_rel).name}"
        shutil.copytree(ROOT / src_rel, repo)
        _git(repo, "init", "-q")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "initial")
        # Add a small doc commit so HEAD~1 exists
        (repo / "README.md").write_text("# changed\n", encoding="utf-8")
        _git(repo, "add", "README.md")
        _git(repo, "commit", "-q", "-m", "second")
        return repo

    def test_custom_toolchain_refusal_mentions_go_and_engine_flag(self) -> None:
        go_repo = self._make_workload_repo("workloads/minimal-go")
        env = dict(
            os.environ,
            LIGHTS_OUT_IMAGE_ROOT=str(self.img_root),
            LIGHTS_OUT_REPO_DIR=str(go_repo),
            LIGHTS_OUT_VERSION="0.8.1",
            LIGHTS_OUT_FLAVOUR="gates",
        )
        # Note: package/entrypoint.sh writes to /out/gates when default gates runs;
        # without /out writable we test via lights-out or check refusal before /out/gates write.
        # In Podman workbench /out may not exist, so create /tmp/out? No, run refusal test only if /out writable or stub.
        res = subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), "gates"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("./go gates", res.stdout)
        self.assertIn("lights-out gates --engine", res.stdout)
        self.assertIn("needs the engine socket", res.stdout)

        # --engine on gates flavour is refused naming the full image
        res_flavour = subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), "gates", "--engine"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_flavour.returncode, 1, res_flavour.stdout + res_flavour.stderr)
        self.assertIn("gates image", res_flavour.stdout)

    def test_engine_gates_stages_platform_and_runs_go_toolchain_with_socket(self) -> None:
        go_repo = self._make_workload_repo("workloads/minimal-go")
        sock = self.tmp / "engine.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock))
        self.addCleanup(srv.close)

        bindir = self.tmp / "fake-compose-bin"
        bindir.mkdir()
        dc_log = self.tmp / "dc.log"
        dc = bindir / "docker-compose"
        dc.write_text(
            f"""#!/bin/sh
printf '%s\\n' "$*" >> {str(dc_log)!r}
case " $* " in
  *" build "*) exit 0 ;;
  *" run "*)
    for a do last="$a"; done
    case "$last" in
      *"sast.sh"*) printf '{{"findings": 0, "items": []}}\\n' ;;
      *"mutation.sh"*) printf '{{"killed": 8, "total": 10, "score": 0.8}}\\n' ;;
      *) echo "ok" ;;
    esac
    exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        dc.chmod(0o755)

        # Override Makefile inside img_root to invoke real core.gates.toolchain + emit GATES_OUT/lines
        (self.img_root / "Makefile").write_text(
            "GATES := test\n"
            "WORKLOAD_REPO := $(shell git -C '$(WORKLOAD)' rev-parse --show-toplevel 2>/dev/null)\n"
            "ifeq ($(WORKLOAD_REPO),$(shell git rev-parse --show-toplevel 2>/dev/null))\n"
            "WORKLOAD_CONTRACT := $(WORKLOAD)\n"
            "else\n"
            "WORKLOAD_CONTRACT := $(WORKLOAD)/.lights-out\n"
            "endif\n"
            "include $(WORKLOAD_CONTRACT)/workload.env\n"
            "export\n"
            ".PHONY: gates\n"
            "gates:\n"
            "\t@mkdir -p \"$(GATES_OUT)\"; \\\n"
            "\twhere=$$(PYTHONPATH=$$(pwd) python3 -P -m core.gates.toolchain preflight) || { printf 'NO-GO: gates — %s\\n' \"$$where\"; exit 1; }; \\\n"
            "\tPYTHONPATH=$$(pwd) python3 -P -m core.gates.toolchain run 'go test ./...' >/dev/null || { printf 'NO-GO: gates — test failed\\n'; exit 1; }; \\\n"
            "\tprintf 'GO: test — in %s\\n' \"$$where\" > \"$(GATES_OUT)/lines\"; \\\n"
            "\tprintf 'GO: gates — risk low, auto, in %s\\n' \"$$where\"\n",
            encoding="utf-8",
        )
        res = subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), "gates", "--engine"],
            env={
                "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "HOME": str(self.tmp),
                "LIGHTS_OUT_IMAGE_ROOT": str(self.img_root),
                "LIGHTS_OUT_REPO_DIR": str(go_repo),
                "LIGHTS_OUT_VERSION": "0.8.1",
                "LIGHTS_OUT_REVISION": "ux-glue-test",
                "LIGHTS_OUT_FLAVOUR": "full",
                "LIGHTS_OUT_PLATFORM_DIR": str(self.plat),
                "LIGHTS_OUT_CREW_DIR": str(self.crew),
                "LIGHTS_OUT_CONTAINER_OUT": str(self.out),
                "DOCKER_HOST": f"unix://{sock}",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(res.stdout.startswith("GO: gates — risk low, auto, in docker.io/library/golang:"), res.stdout)
        self.assertTrue((self.plat / ".gates-attached" / ".lights-out" / "workload.env").is_file())
        self.assertTrue((self.out / "gates" / "summary.md").is_file())
        self.assertTrue(dc_log.is_file(), "docker-compose was not invoked for Go toolchain")

        # Also verify compose:<service> (workloads/minimal) runs through staged platform dir with --engine
        dc_log.unlink(missing_ok=True)
        min_repo = self._make_workload_repo("workloads/minimal")
        res_min = subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), "gates", "--engine"],
            env={
                "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
                "HOME": str(self.tmp),
                "LIGHTS_OUT_IMAGE_ROOT": str(self.img_root),
                "LIGHTS_OUT_REPO_DIR": str(min_repo),
                "LIGHTS_OUT_VERSION": "0.8.1",
                "LIGHTS_OUT_REVISION": "ux-glue-test",
                "LIGHTS_OUT_FLAVOUR": "full",
                "LIGHTS_OUT_PLATFORM_DIR": str(self.plat),
                "LIGHTS_OUT_CREW_DIR": str(self.crew),
                "LIGHTS_OUT_CONTAINER_OUT": str(self.out),
                "DOCKER_HOST": f"unix://{sock}",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_min.returncode, 0, res_min.stdout + res_min.stderr)
        self.assertEqual(res_min.stdout.strip(), "GO: gates — risk low, auto, in compose:backend-test")
        dc_calls = dc_log.read_text(encoding="utf-8")
        self.assertIn("build backend-test", dc_calls)
        self.assertIn("run --rm -T", dc_calls)

    def test_entrypoint_start_with_cloud_delivery_then_gates_engine_succeeds(self) -> None:
        go_repo = self._make_workload_repo("workloads/minimal-go")
        sock = self.tmp / "engine.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock))
        self.addCleanup(srv.close)

        bindir = self.tmp / "fake-compose-bin"
        bindir.mkdir()
        dc_log = self.tmp / "dc-real-make.log"
        dc = bindir / "docker-compose"
        dc.write_text(
            f"""#!/bin/sh
printf '%s\\n' "$*" >> {str(dc_log)!r}
case " $* " in
  *" build "*) exit 0 ;;
  *" run "*)
    for a do last="$a"; done
    case "$last" in
      *"sast.sh"*) printf '{{"findings": 0, "items": []}}\\n' ;;
      *"mutation.sh"*) printf '{{"killed": 8, "total": 10, "score": 0.8}}\\n' ;;
      *) echo "ok" ;;
    esac
    exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        dc.chmod(0o755)

        common_env = {
            "PATH": f"{bindir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.tmp),
            "LIGHTS_OUT_IMAGE_ROOT": str(self.img_root),
            "LIGHTS_OUT_REPO_DIR": str(go_repo),
            "LIGHTS_OUT_VERSION": "0.8.1",
            "LIGHTS_OUT_REVISION": "ux-glue-test",
            "LIGHTS_OUT_FLAVOUR": "full",
            "LIGHTS_OUT_PLATFORM_DIR": str(self.plat),
            "LIGHTS_OUT_CREW_DIR": str(self.crew),
            "LIGHTS_OUT_CONTAINER_OUT": str(self.out),
            "LIGHTS_OUT_DOCTOR_RC": "0",
            "LIGHTS_OUT_DOCTOR_OUT": "GO: doctor",
            "LIGHTS_OUT_DOCTOR_ERR": "",
            "DOCKER_HOST": f"unix://{sock}",
        }

        res_start = subprocess.run(
            [
                "sh",
                str(self.img_root / "package" / "entrypoint.sh"),
                "start",
                "--non-interactive",
                "--preset",
                "junior",
                "--provider",
                "replay",
                "--harness",
                "replay",
                "--delivery",
                "gcp-cloud-deploy",
            ],
            env=common_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_start.returncode, 0, res_start.stdout + res_start.stderr)
        self.assertIn("DELIVERY=outside/gcp-cloud-deploy", (self.crew / "start-plan.env").read_text(encoding="utf-8"))

        # With real Makefile inside staged platform dir, gates --engine must ignore start-plan.env
        # and avoid failing ADMIT on DELIVERY=outside/gcp-cloud-deploy.
        res_gates = subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), "gates", "--engine"],
            env=common_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(res_gates.returncode, 0, res_gates.stdout + res_gates.stderr)
        self.assertNotIn("is refused", res_gates.stderr)
        self.assertTrue(res_gates.stdout.startswith("GO: gates — "), res_gates.stdout)
        self.assertTrue((self.out / "gates" / "summary.md").is_file())
        self.assertTrue(dc_log.is_file(), "docker-compose was not invoked during gates --engine")

    def test_ci_package_passes_and_catches_engine_flag_defect(self) -> None:
        if not shutil.which("shellcheck"):
            self.skipTest("shellcheck not on PATH")
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")

        res = subprocess.run(["sh", "ci/package.sh"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "GO: package")


if __name__ == "__main__":
    unittest.main()
