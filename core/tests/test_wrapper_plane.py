"""Unit tests for M67 — Plane & Full E2E Workflow from the `lights-out` wrapper (ADR 0105).

Strictly vendor-free, app-free, and model-free (enforced by ci/vendor-free.sh,
ci/app-free.sh, and ci/model-free.sh): uses fake PATH `podman`/`docker` shims
and synthetic repositories with zero vendor names, zero bundled workload paths,
and zero model/key identifiers.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

POSIX_TOOLS = (
    "sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut", "cksum",
    "cat", "uname", "basename", "dirname", "mkdir", "rm", "mktemp", "id",
    "env", "wc", "stat", "tar", "ln", "cp", "git",
)

READONLY_CMDS = ("init", "gates", "test-proof", "test-integrity", "triage", "postmortem")
STATE_ONLY_CMDS = ("next", "dora", "spend", "cloud-setup")
PLANE_CMDS = (
    "start", "intake", "answer", "intake-confirm", "assign",
    "up", "down", "smoke", "release", "promote", "console", "evidence", "provenance",
)
ALL_SUBCOMMANDS = (
    "init", "gates", "test-proof", "test-integrity", "triage", "postmortem",
    "doctor", "version",
    "next", "dora", "spend", "cloud-setup",
    "start", "intake", "answer", "intake-confirm", "assign",
    "up", "down", "smoke", "release", "promote", "console", "evidence", "provenance",
)
EXPECTED_KEYS = sorted({
    "BASE", "HEAD", "JUDGED", "STAGED", "ALERT", "INCIDENT", "PROFILE",
    "MODEL_PROVIDER", "MODEL",
    "TICKET", "TEXT", "ANSWER", "DECISIONS", "VERSION", "CANARY_VERSION",
    "CANARY_TAG", "GO_FOR_LAUNCH", "REASON", "MODE", "PRESET", "CLOUD",
    "DELIVERY", "CODE_HOST", "AGENT_HARNESS", "PROVIDER", "ADAPTERS_PATH",
    "WINDOW_DAYS", "SPEND_BUDGET_TICKET_USD", "SPEND_BUDGET_DAY_USD",
})


def _parse_keys(path: Path) -> list[str]:
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#"):
            continue
        for m in re.finditer(r"([A-Z][A-Z0-9_]*)=/?\*", line):
            keys.add(m.group(1))
    return sorted(keys)


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


def _extract_port_flags(argv: list[str]) -> list[str]:
    ports: list[str] = []
    i = 0
    while i < len(argv):
        if argv[i] == "-p" and i + 1 < len(argv):
            ports.append(argv[i + 1])
            i += 2
        else:
            i += 1
    return ports


class WrapperPlaneBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wrapper-plane-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in POSIX_TOOLS:
            found = shutil.which(tool)
            if found:
                (self.tools / tool).symlink_to(found)

        self.repo = self.tmp / "sample-repo"
        self.repo.mkdir()
        self._git(self.repo, "init", "-q")
        (self.repo / "README.md").write_text("# sample\n", encoding="utf-8")
        self._git(self.repo, "add", "README.md")
        self._git(self.repo, "commit", "-q", "-m", "initial")

        self.out = self.tmp / "state-out"
        self.wrapper = ROOT / "lights-out"
        self.entrypoint = ROOT / "package" / "entrypoint.sh"
        with (ROOT / "pyproject.toml").open("rb") as fh:
            self.version = tomllib.load(fh)["project"]["version"]

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@invalid",
             "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True,
        )

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
  "info"*) exit 0 ;;
  "image inspect"*) exit 0 ;;
  "manifest inspect"*) exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return bindir, log

    def _run_wrapper(self, args: list[str], bindir: Path | None = None,
                     extra_env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        path = f"{bindir}:{self.tools}" if bindir else str(self.tools)
        env = {
            "PATH": path,
            "HOME": str(self.tmp),
            "LIGHTS_OUT_OUT": str(self.out),
        }
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["sh", str(self.wrapper), *args],
            cwd=cwd or self.repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


class WrapperContractAndAllowlistTests(WrapperPlaneBase):
    """ADR 0105 §1 & §4: 24 subcommands, synchronized 29-key allowlist, early refusals."""

    def test_unknown_subcommand_refused_before_engine_naming_all_24(self):
        bindir, log = self._make_fake_engine("podman")
        res = self._run_wrapper(["frobnicate"], bindir=bindir)
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(res.stdout, "")
        self.assertFalse(log.exists(), "unknown subcommand touched the engine")
        for sub in ALL_SUBCOMMANDS:
            self.assertRegex(res.stderr, rf"\b{re.escape(sub)}\b", f"stderr missed {sub!r}: {res.stderr}")

    def test_allowlist_matches_entrypoint_with_all_29_keys(self):
        w_keys = _parse_keys(self.wrapper)
        e_keys = _parse_keys(self.entrypoint)
        self.assertEqual(w_keys, EXPECTED_KEYS)
        self.assertEqual(e_keys, EXPECTED_KEYS)

    def test_unknown_keys_and_disallowed_flags_refused_before_engine(self):
        bindir, log = self._make_fake_engine("podman")
        for bad_args in (
            ["up", "NOPE=1"],
            ["next", "BASEX=1"],
            ["start", "base=x"],
            ["gates", "--json"],
            ["up", "--non-interactive"],
            ["next", "--bogus"],
            ["start", "--preset"],
        ):
            res = self._run_wrapper(bad_args, bindir=bindir)
            self.assertNotEqual(res.returncode, 0, f"expected refusal for {bad_args}")
            self.assertEqual(res.stdout, "", f"unexpected stdout for {bad_args}")
            self.assertFalse(log.exists(), f"{bad_args} touched the engine")

    def test_version_and_flag_version_never_touch_engine(self):
        bindir, log = self._make_fake_engine("podman")
        sub = self._run_wrapper(["version"], bindir=bindir)
        flg = self._run_wrapper(["--version"], bindir=bindir)
        self.assertEqual(sub.returncode, 0)
        self.assertEqual(flg.returncode, 0)
        self.assertEqual(flg.stdout, sub.stdout)
        self.assertIn(self.version, sub.stdout)
        self.assertFalse(log.exists())


class WrapperSocketBoundaryTests(WrapperPlaneBase):
    """ADR 0105 §1–§2: read-only & state-only are socket-free; plane commands opt in."""

    def test_readonly_and_gates_never_mount_socket_or_docker_host(self):
        bindir, log = self._make_fake_engine("podman")
        for cmd in READONLY_CMDS:
            log.unlink(missing_ok=True)
            extra = ["ALERT=a"] if cmd == "triage" else (["INCIDENT=i"] if cmd == "postmortem" else [])
            res = self._run_wrapper([cmd, *extra], bindir=bindir)
            self.assertEqual(res.returncode, 0, f"{cmd} failed: {res.stderr}")
            calls = _read_calls(log)
            run_calls = [c for c in calls if c and c[0] == "run"]
            self.assertEqual(len(run_calls), 1, f"{cmd} calls: {calls}")
            argv = run_calls[0]
            mounts = _extract_mount_pairs(argv)
            envs = _extract_env_flags(argv)
            self.assertFalse(any(".sock" in m for m in mounts), f"{cmd} mounted socket: {mounts}")
            self.assertFalse(any(e.startswith("DOCKER_HOST") for e in envs), f"{cmd} passed DOCKER_HOST: {envs}")
            self.assertFalse(any(e.startswith("LIGHTS_OUT_PLATFORM_DIR") for e in envs), f"{cmd} passed platform dir")
            self.assertIn(f"{self.repo}:/repo:ro", mounts)
            self.assertIn(f"{self.out}:/out", mounts)
            want_img = (
                f"ghcr.io/nicolaguglielmi/lights-out:{self.version}"
                if cmd in ("triage", "postmortem")
                else f"ghcr.io/nicolaguglielmi/lights-out-gates:{self.version}"
            )
            self.assertIn(want_img, argv)

    def test_state_only_commands_are_socket_free_and_mount_crew_dir(self):
        bindir, log = self._make_fake_engine("podman")
        for cmd in STATE_ONLY_CMDS:
            log.unlink(missing_ok=True)
            res = self._run_wrapper([cmd], bindir=bindir)
            self.assertEqual(res.returncode, 0, f"{cmd} failed: {res.stderr}")
            calls = _read_calls(log)
            run_calls = [c for c in calls if c and c[0] == "run"]
            self.assertEqual(len(run_calls), 1, f"{cmd} calls: {calls}")
            argv = run_calls[0]
            mounts = _extract_mount_pairs(argv)
            envs = _extract_env_flags(argv)
            self.assertFalse(any(".sock" in m for m in mounts), f"{cmd} mounted socket: {mounts}")
            self.assertFalse(any(e.startswith("DOCKER_HOST") for e in envs), f"{cmd} passed DOCKER_HOST: {envs}")
            self.assertFalse(any(e.startswith("LIGHTS_OUT_PLATFORM_DIR") for e in envs), f"{cmd} passed platform dir")
            self.assertIn(f"{self.repo}:/repo:ro", mounts)
            self.assertIn(f"{self.out / 'crew'}:/opt/lights-out/.crew", mounts)
            self.assertTrue((self.out / "crew").is_dir())
            self.assertIn(f"ghcr.io/nicolaguglielmi/lights-out:{self.version}", argv)

    def test_opt_in_plane_commands_mount_socket_mirrored_platform_and_notice(self):
        bindir, log = self._make_fake_engine("podman")
        fake_sock = str(self.tmp / "podman-remote.sock")
        for cmd in PLANE_CMDS:
            log.unlink(missing_ok=True)
            res = self._run_wrapper(
                [cmd],
                bindir=bindir,
                extra_env={"FAKE_REMOTE_SOCK": fake_sock, "FAKE_REMOTE_EXISTS": "true"},
            )
            self.assertEqual(res.returncode, 0, f"{cmd} failed: {res.stderr}")
            calls = _read_calls(log)
            run_calls = [c for c in calls if c and c[0] == "run"]
            self.assertEqual(len(run_calls), 1, f"{cmd} calls: {calls}")
            argv = run_calls[0]
            mounts = _extract_mount_pairs(argv)
            envs = _extract_env_flags(argv)
            plat = str(self.out / "platform")
            crew = str(self.out / "crew")
            self.assertIn(f"{self.repo}:/repo:ro", mounts)
            self.assertIn(f"{plat}:{plat}", mounts)
            self.assertIn(f"{crew}:{crew}", mounts)
            self.assertIn(f"{fake_sock}:/var/run/docker.sock", mounts)
            self.assertIn("DOCKER_HOST=unix:///var/run/docker.sock", envs)
            self.assertIn(f"LIGHTS_OUT_PLATFORM_DIR={plat}", envs)
            self.assertIn(f"LIGHTS_OUT_CREW_DIR={crew}", envs)
            self.assertIn("label=disable", argv)
            self.assertIn(f"ghcr.io/nicolaguglielmi/lights-out:{self.version}", argv)
            notice_lines = [ln for ln in res.stderr.splitlines() if "socket" in ln.lower()]
            self.assertEqual(len(notice_lines), 1, f"{cmd} stderr notice: {res.stderr!r}")

    def test_docker_socket_group_add_and_host_gateway(self):
        bindir, log = self._make_fake_engine("docker")
        sock_path = self.tmp / "docker.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        self.addCleanup(srv.close)

        res = self._run_wrapper(
            ["up"],
            bindir=bindir,
            extra_env={"CONTAINER_ENGINE": "docker", "DOCKER_HOST": f"unix://{sock_path}"},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        run_calls = [c for c in _read_calls(log) if c and c[0] == "run"]
        self.assertEqual(len(run_calls), 1)
        argv = run_calls[0]
        mounts = _extract_mount_pairs(argv)
        envs = _extract_env_flags(argv)
        self.assertIn(f"{sock_path}:/var/run/docker.sock", mounts)
        self.assertIn("DOCKER_HOST=unix:///var/run/docker.sock", envs)
        self.assertIn("--group-add", argv)
        self.assertIn("host.containers.internal:host-gateway", argv)
        self.assertIn("label=disable", argv)

    def test_missing_socket_fails_cleanly_with_one_stderr_line(self):
        bindir_p, log_p = self._make_fake_engine("podman")
        res_p = self._run_wrapper(
            ["up"],
            bindir=bindir_p,
            extra_env={"FAKE_REMOTE_EXISTS": "false", "FAKE_REMOTE_SOCK": "/missing/podman.sock"},
        )
        self.assertNotEqual(res_p.returncode, 0)
        self.assertEqual(res_p.stdout, "")
        self.assertEqual(len(res_p.stderr.splitlines()), 1, res_p.stderr)
        self.assertIn("socket", res_p.stderr.lower())
        self.assertFalse(any(c and c[0] == "run" for c in _read_calls(log_p)))

        bindir_d, log_d = self._make_fake_engine("docker")
        res_d = self._run_wrapper(
            ["release"],
            bindir=bindir_d,
            extra_env={"CONTAINER_ENGINE": "docker", "DOCKER_HOST": f"unix://{self.tmp / 'nope.sock'}"},
        )
        self.assertNotEqual(res_d.returncode, 0)
        self.assertEqual(res_d.stdout, "")
        self.assertEqual(len(res_d.stderr.splitlines()), 1, res_d.stderr)
        self.assertIn("socket", res_d.stderr.lower())
        self.assertFalse(any(c and c[0] == "run" for c in _read_calls(log_d)))

    def test_init_stays_ro_by_default_and_mounts_rw_only_with_apply(self):
        bindir, log = self._make_fake_engine("podman")
        fake_sock = str(self.tmp / "podman-remote.sock")

        res_ro = self._run_wrapper(["init"], bindir=bindir)
        self.assertEqual(res_ro.returncode, 0, res_ro.stderr)
        argv_ro = next(c for c in _read_calls(log) if c and c[0] == "run")
        mounts_ro = _extract_mount_pairs(argv_ro)
        self.assertIn(f"{self.repo}:/repo:ro", mounts_ro)
        self.assertNotIn(f"{self.repo}:/repo:rw", mounts_ro)
        self.assertFalse(any(".sock" in m for m in mounts_ro))
        self.assertNotIn("read-write", res_ro.stderr)

        log.unlink(missing_ok=True)
        res_rw = self._run_wrapper(
            ["init", "--apply", "--branch", "init/lo", "--commit", "--force", "--baseline-mode", "incremental"],
            bindir=bindir,
        )
        self.assertEqual(res_rw.returncode, 0, res_rw.stderr)
        argv_rw = next(c for c in _read_calls(log) if c and c[0] == "run")
        mounts_rw = _extract_mount_pairs(argv_rw)
        self.assertIn(f"{self.repo}:/repo:rw", mounts_rw)
        self.assertNotIn(f"{self.repo}:/repo:ro", mounts_rw)
        self.assertFalse(any(".sock" in m for m in mounts_rw))
        rw_notices = [ln for ln in res_rw.stderr.splitlines() if "read-write" in ln]
        self.assertEqual(len(rw_notices), 1, res_rw.stderr)

        log.unlink(missing_ok=True)
        res_eng = self._run_wrapper(
            ["init", "--engine", "--apply"],
            bindir=bindir,
            extra_env={"FAKE_REMOTE_SOCK": fake_sock, "FAKE_REMOTE_EXISTS": "true"},
        )
        self.assertEqual(res_eng.returncode, 0, res_eng.stderr)
        argv_eng = next(c for c in _read_calls(log) if c and c[0] == "run")
        mounts_eng = _extract_mount_pairs(argv_eng)
        self.assertIn(f"{self.repo}:/repo:rw", mounts_eng)
        self.assertIn(f"{fake_sock}:/var/run/docker.sock", mounts_eng)


class WrapperConsoleAdaptersAndSecretsTests(WrapperPlaneBase):
    """ADR 0105 §4: console port/env, ADAPTERS_PATH :ro, secret-by-name forwarding, no banned flags."""

    def test_console_publishes_port_and_forwards_console_env(self):
        bindir, log = self._make_fake_engine("podman")
        res = self._run_wrapper(
            ["console"],
            bindir=bindir,
            extra_env={
                "CONSOLE_PUBLISH": "0.0.0.0",
                "CONSOLE_PORT": "9098",
                "CONSOLE_AUTH": "proxy",
                "CONSOLE_IDENTITY_HEADER": "X-Auth-User",
                "CONSOLE_LAUNCHERS": "alice,bob",
                "CONSOLE_POLL_S": "5",
            },
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = next(c for c in _read_calls(log) if c and c[0] == "run")
        ports = _extract_port_flags(argv)
        envs = _extract_env_flags(argv)
        self.assertIn("0.0.0.0:9098:8098", ports)
        self.assertIn("CONSOLE_PORT=9098", envs)
        self.assertIn("CONSOLE_BIND=0.0.0.0", envs)
        self.assertIn("CONSOLE_PUBLISH=0.0.0.0", envs)
        for key in ("CONSOLE_AUTH", "CONSOLE_IDENTITY_HEADER", "CONSOLE_LAUNCHERS", "CONSOLE_POLL_S"):
            self.assertIn(key, envs)

    def test_adapters_path_mounted_ro_and_secrets_passed_by_name_only(self):
        bindir, log = self._make_fake_engine("podman")
        adapters_dir = self.tmp / "ext-adapters"
        adapters_dir.mkdir()
        secret_val = "super-secret-header-value-9911"
        res = self._run_wrapper(
            ["up", f"ADAPTERS_PATH={adapters_dir}"],
            bindir=bindir,
            extra_env={"METRICS_AUTH_HEADER": secret_val},
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = next(c for c in _read_calls(log) if c and c[0] == "run")
        mounts = _extract_mount_pairs(argv)
        envs = _extract_env_flags(argv)
        self.assertIn(f"{adapters_dir}:{adapters_dir}:ro", mounts)
        self.assertIn("METRICS_AUTH_HEADER", envs)
        joined = "\n".join(argv)
        self.assertNotIn(secret_val, joined)
        self.assertNotIn("--env-file", argv)
        for m in mounts:
            self.assertNotRegex(m, r"[:,][zZ]($|[^A-Za-z0-9_])")

        bad = self._run_wrapper(["up", "ADAPTERS_PATH=relative/path"], bindir=bindir)
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout, "")


class EntrypointStagingAndFlavourTests(WrapperPlaneBase):
    """ADR 0105 §1 & §3: entrypoint flavour guard, platform staging, .crew link, cloud-setup."""

    def _stage_synthetic_image_root(self) -> Path:
        img_root = self.tmp / "img-root"
        (img_root / "package").mkdir(parents=True)
        (img_root / "ci").mkdir()
        (img_root / "core").mkdir()
        (img_root / ".crew").mkdir()
        shutil.copy2(self.entrypoint, img_root / "package" / "entrypoint.sh")
        shutil.copy2(ROOT / "ci" / "lib.sh", img_root / "ci" / "lib.sh")
        (img_root / "Makefile").write_text(
            "GATES := risk\n"
            ".PHONY: cloud-setup down next\n"
            "cloud-setup:\n\t@printf 'GO: cloud-setup — staged %s\\n' \"$(CLOUD)\"\n"
            "down:\n\t@printf 'GO: down — cwd=%s\\n' \"$(CURDIR)\"\n"
            "next:\n\t@printf 'GO: next — cli=%s\\n' \"$(LIGHTS_OUT_CLI_ARGS)\"\n",
            encoding="utf-8",
        )
        (img_root / "core" / "await-head.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (img_root / "core" / "attach.sh").write_text(
            "#!/bin/sh\nmkdir -p \"$2\"\nprintf 'attached\\n' > \"$2/marker\"\n",
            encoding="utf-8",
        )
        return img_root

    def test_gates_flavour_refuses_all_state_and_plane_commands(self):
        img_root = self._stage_synthetic_image_root()
        for cmd in (*STATE_ONLY_CMDS, *PLANE_CMDS):
            env = {
                "PATH": str(self.tools),
                "LIGHTS_OUT_IMAGE_ROOT": str(img_root),
                "LIGHTS_OUT_FLAVOUR": "gates",
                "LIGHTS_OUT_VERSION": self.version,
            }
            res = subprocess.run(
                ["sh", str(img_root / "package" / "entrypoint.sh"), cmd],
                env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(res.returncode, 1, f"{cmd}: {res.stdout} {res.stderr}")
            self.assertTrue(res.stdout.startswith(f"NO-GO: {cmd} — "), res.stdout)
            self.assertIn("gates image", res.stdout)

    def test_platform_staging_revision_crew_symlink_and_cloud_setup(self):
        if not shutil.which("make"):
            self.skipTest("make not on PATH")
        img_root = self._stage_synthetic_image_root()
        tools_plus_make = self.tmp / "tools-make"
        tools_plus_make.mkdir()
        for p in self.tools.iterdir():
            (tools_plus_make / p.name).symlink_to(p.resolve())
        (tools_plus_make / "make").symlink_to(shutil.which("make"))

        plat = self.tmp / "platform"
        crew = self.tmp / "crew"
        plat.mkdir()
        crew.mkdir()
        (crew / "keep.txt").write_text("keep", encoding="utf-8")

        base_env = {
            "PATH": str(tools_plus_make),
            "HOME": str(self.tmp),
            "LIGHTS_OUT_IMAGE_ROOT": str(img_root),
            "LIGHTS_OUT_VERSION": self.version,
            "LIGHTS_OUT_REVISION": "rev-1",
            "LIGHTS_OUT_FLAVOUR": "full",
            "LIGHTS_OUT_PLATFORM_DIR": str(plat),
            "LIGHTS_OUT_CREW_DIR": str(crew),
        }

        res1 = subprocess.run(
            ["sh", str(img_root / "package" / "entrypoint.sh"), "down"],
            env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res1.returncode, 0, res1.stdout + res1.stderr)
        self.assertIn(f"GO: down — cwd={plat}", res1.stdout)
        self.assertTrue((plat / ".platform-revision").is_file())
        self.assertTrue((plat / ".crew").is_symlink())
        self.assertEqual((plat / ".crew").resolve(), crew.resolve())
        self.assertEqual((crew / "keep.txt").read_text(encoding="utf-8"), "keep")

        # Same revision skips re-staging (marker file inside plat survives)
        stale_canary = plat / "cache-marker"
        stale_canary.write_text("cached", encoding="utf-8")
        res2 = subprocess.run(
            ["sh", str(img_root / "package" / "entrypoint.sh"), "down"],
            env=base_env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res2.returncode, 0, res2.stdout + res2.stderr)
        self.assertTrue(stale_canary.exists(), "same revision re-copied platform unnecessarily")

        # Different revision refreshes platform while preserving .crew target
        env_rev2 = dict(base_env, LIGHTS_OUT_REVISION="rev-2")
        res3 = subprocess.run(
            ["sh", str(img_root / "package" / "entrypoint.sh"), "down"],
            env=env_rev2, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res3.returncode, 0, res3.stdout + res3.stderr)
        self.assertFalse(stale_canary.exists(), "new revision did not refresh platform")
        self.assertEqual((crew / "keep.txt").read_text(encoding="utf-8"), "keep")

        # State-only cloud-setup skips attach
        res_cs = subprocess.run(
            ["sh", str(img_root / "package" / "entrypoint.sh"), "cloud-setup", "CLOUD=demo"],
            env={
                "PATH": str(tools_plus_make),
                "HOME": str(self.tmp),
                "LIGHTS_OUT_IMAGE_ROOT": str(img_root),
                "LIGHTS_OUT_FLAVOUR": "full",
                "LIGHTS_OUT_VERSION": self.version,
            },
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(res_cs.returncode, 0, res_cs.stdout + res_cs.stderr)
        self.assertEqual(res_cs.stdout.strip(), "GO: cloud-setup — staged demo")


if __name__ == "__main__":
    unittest.main()
