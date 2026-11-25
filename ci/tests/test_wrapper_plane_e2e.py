"""E2E and CI package tests for M67 — Plane & Full E2E Workflow from `lights-out` (ADR 0105).

Kept under ci/tests/ so core/tests/ stays strictly free of workload paths
(`.lights-out/`, `workloads/`), vendor names (`github`, `gitlab`, `envoy`),
and API key variable names (`*_API_KEY`).
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


class WrapperSecretsAndStartE2ETests(unittest.TestCase):
    """Verify secret forwarding by name only and start/next wrapper integration."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wrapper-e2e-")).resolve()
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

        self.repo = self.tmp / "workload-repo"
        shutil.copytree(ROOT / "workloads" / "minimal", self.repo)
        self._git(self.repo, "init", "-q")
        self._git(self.repo, "add", "-A")
        self._git(self.repo, "commit", "-q", "-m", "initial minimal")

        self.out = self.tmp / "out"
        self.bindir = self.tmp / "bin"
        self.bindir.mkdir()
        self.log = self.tmp / "podman.log"
        podman = self.bindir / "podman"
        podman.write_text(
            f"""#!/bin/sh
for a do printf '%s\\0' "$a" >> {str(self.log)!r}; done
printf '\\0' >> {str(self.log)!r}
case "$*" in
  *"RemoteSocket.Path"*) printf '/tmp/fake-podman.sock\\n'; exit 0 ;;
  *"RemoteSocket.Exists"*) printf 'true\\n'; exit 0 ;;
  *"Client.Version"*) printf '5.0.0\\n'; exit 0 ;;
esac
exit 0
""",
            encoding="utf-8",
        )
        podman.chmod(podman.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@invalid",
             "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True,
        )

    def test_all_allowed_secrets_forwarded_by_name_never_value(self):
        secrets = {
            "GEMINI_API_KEY": "secret-gem-111",
            "ANTHROPIC_API_KEY": "secret-ant-222",
            "OPENAI_API_KEY": "secret-oai-333",
            "ZAI_API_KEY": "secret-zai-444",
            "GITHUB_TOKEN": "secret-gh-555",
            "GITLAB_TOKEN": "secret-gl-666",
            "METRICS_AUTH_HEADER": "Bearer secret-met-777",
        }
        env = {
            "PATH": f"{self.bindir}:{self.tools}",
            "HOME": str(self.tmp),
            "LIGHTS_OUT_OUT": str(self.out),
            **secrets,
        }
        res = subprocess.run(
            ["sh", str(ROOT / "lights-out"), "assign", "TICKET=0001"],
            cwd=self.repo, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = next(c for c in _read_calls(self.log) if c and c[0] == "run")
        envs = _extract_env_flags(argv)
        joined = "\n".join(argv)
        for key, secret in secrets.items():
            self.assertIn(key, envs, f"{key} not forwarded in {envs}")
            self.assertNotIn(secret, joined, f"secret value for {key} leaked onto argv")

    def test_start_forwards_doctor_state_and_cli_flags(self):
        env = {
            "PATH": f"{self.bindir}:{self.tools}",
            "HOME": str(self.tmp),
            "LIGHTS_OUT_OUT": str(self.out),
        }
        res = subprocess.run(
            [
                "sh", str(ROOT / "lights-out"), "start",
                "--non-interactive", "--preset", "junior",
                "--provider", "replay", "--harness", "replay", "--delivery", "envoy-canary",
            ],
            cwd=self.repo, env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        argv = next(c for c in _read_calls(self.log) if c and c[0] == "run")
        envs = _extract_env_flags(argv)
        self.assertIn("LIGHTS_OUT_DOCTOR_RC", envs)
        self.assertIn("LIGHTS_OUT_DOCTOR_OUT", envs)
        self.assertIn("LIGHTS_OUT_DOCTOR_ERR", envs)
        self.assertIn("--non-interactive", argv)
        self.assertIn("envoy-canary", argv)


class EntrypointWorkloadE2ETests(unittest.TestCase):
    """Run real package/entrypoint.sh against a staged repository tree and minimal workload."""

    def setUp(self):
        if not shutil.which("make"):
            self.fail("make is required for entrypoint tests")
        self.tmp = Path(tempfile.mkdtemp(prefix="entrypoint-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

        self.img_root = self.tmp / "opt-lights-out"
        shutil.copytree(
            ROOT,
            self.img_root,
            ignore=shutil.ignore_patterns(".git", ".crew", ".attached", ".gates-out", ".claude", "__pycache__"),
        )
        (self.img_root / ".crew").mkdir(exist_ok=True)

        self.repo = self.tmp / "user-repo"
        shutil.copytree(ROOT / "workloads" / "minimal", self.repo)
        self._git(self.repo, "init", "-q")
        self._git(self.repo, "add", "-A")
        self._git(self.repo, "commit", "-q", "-m", "initial")

        self.plat = self.tmp / "out" / "platform"
        self.crew = self.tmp / "out" / "crew"
        self.plat.mkdir(parents=True)
        self.crew.mkdir(parents=True)

    def _git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@invalid",
             "-c", "commit.gpgsign=false", *args],
            check=True, capture_output=True, text=True,
        )

    def _run_entrypoint(
        self, args: list[str], *, plane: bool = False, extra_env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        # Real entrypoint expects /repo mount and /opt/lights-out/.crew for state-only;
        # we provide a symlinked /repo-equivalent? No, entrypoint reads /repo unless
        # LIGHTS_OUT_REPO_DIR is used, wait — let's test inside a synthetic root or pass
        # LIGHTS_OUT_REPO_DIR when testing on host without container root!
        env = dict(os.environ)
        env.update({
            "LIGHTS_OUT_IMAGE_ROOT": str(self.img_root),
            "LIGHTS_OUT_REPO_DIR": str(self.repo),
            "LIGHTS_OUT_VERSION": "0.8.1",
            "LIGHTS_OUT_REVISION": "e2e-rev-1",
            "LIGHTS_OUT_FLAVOUR": "full",
        })
        if plane:
            env["LIGHTS_OUT_PLATFORM_DIR"] = str(self.plat)
            env["LIGHTS_OUT_CREW_DIR"] = str(self.crew)
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["sh", str(self.img_root / "package" / "entrypoint.sh"), *args],
            env=env, capture_output=True, text=True, check=False,
        )

    def test_state_only_cloud_setup_next_dora_and_spend_e2e(self):
        # Link img_root/.crew -> self.crew simulating -v $out/crew:/opt/lights-out/.crew
        shutil.rmtree(self.img_root / ".crew")
        (self.img_root / ".crew").symlink_to(self.crew)

        res_cs = self._run_entrypoint(["cloud-setup", "CLOUD=gcp-cloud-deploy"])
        self.assertEqual(res_cs.returncode, 0, res_cs.stdout + res_cs.stderr)
        self.assertTrue(res_cs.stdout.startswith("GO: cloud-setup — gcp-cloud-deploy"), res_cs.stdout)
        self.assertTrue((self.crew / "cloud" / "gcp-cloud-deploy" / "infra" / "main.tf").is_file())

        res_cs_bad = self._run_entrypoint(["cloud-setup", "CLOUD=unknown-cloud"])
        self.assertEqual(res_cs_bad.returncode, 1)
        self.assertTrue(res_cs_bad.stdout.startswith("NO-GO: cloud-setup — "), res_cs_bad.stdout)

        res_next = self._run_entrypoint(["next", "--json"])
        self.assertEqual(res_next.returncode, 0, res_next.stdout + res_next.stderr)
        doc = json.loads(res_next.stdout)
        self.assertIn("stages", doc)
        self.assertEqual(doc["total_stages"], 6)

        res_spend = self._run_entrypoint(["spend"])
        self.assertEqual(res_spend.returncode, 0, res_spend.stdout + res_spend.stderr)
        self.assertTrue(res_spend.stdout.startswith("GO: spend — "), res_spend.stdout)

        res_dora = self._run_entrypoint(["dora", "WINDOW_DAYS=7"])
        self.assertEqual(res_dora.returncode, 0, res_dora.stdout + res_dora.stderr)
        self.assertTrue(res_dora.stdout.startswith("GO: dora — "), res_dora.stdout)
        self.assertTrue((self.crew / "dora.json").is_file())

    def test_plane_start_populates_mirrored_platform_and_attaches_repo(self):
        res_start = self._run_entrypoint(
            [
                "start",
                "PRESET=junior",
                "PROVIDER=replay",
                "AGENT_HARNESS=replay",
                "DELIVERY=envoy-canary",
                "SPEND_BUDGET_TICKET_USD=3.5",
                "SPEND_BUDGET_DAY_USD=12.0",
            ],
            plane=True,
            extra_env={"LIGHTS_OUT_DOCTOR_RC": "0", "LIGHTS_OUT_DOCTOR_OUT": "GO: doctor", "LIGHTS_OUT_DOCTOR_ERR": ""},
        )
        self.assertEqual(res_start.returncode, 0, res_start.stdout + res_start.stderr)
        self.assertTrue(res_start.stdout.startswith("GO: start — "), res_start.stdout)
        self.assertTrue((self.plat / ".attached" / ".lights-out" / "workload.env").is_file())
        self.assertTrue((self.plat / ".crew").is_symlink())
        self.assertEqual((self.plat / ".crew").resolve(), self.crew.resolve())
        plan = (self.crew / "start-plan.env").read_text(encoding="utf-8")
        self.assertIn("SUGGESTED_TICKET_BUDGET_USD=3.5", plan)
        self.assertIn("SUGGESTED_DAY_BUDGET_USD=12.0", plan)
        # /repo stayed untouched
        status = subprocess.run(["git", "-C", str(self.repo), "status", "--porcelain"], capture_output=True, text=True, check=True)
        self.assertEqual(status.stdout, "")

    def test_up_missing_compose_exits_one_with_nogo_line(self):
        (self.repo / ".lights-out" / "compose.yaml").unlink()
        self._git(self.repo, "add", "-A")
        self._git(self.repo, "commit", "-q", "-m", "drop compose")

        res = self._run_entrypoint(["up"], plane=True)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertTrue(res.stdout.startswith("NO-GO: up — "), res.stdout)

    def test_promote_reason_decisions_file_and_uncommitted_tickets_preserved(self):
        # Verify REASON= -> GO_FOR_LAUNCH_REASON, DECISIONS=@<file> -> @<repo_dir>/<file>,
        # and plane commands preserve uncommitted tickets in .attached/.lights-out/tickets/.
        (self.repo / "dec.json").write_text('["confirm"]\n', encoding="utf-8")
        self._git(self.repo, "add", "dec.json")
        self._git(self.repo, "commit", "-q", "-m", "add decisions")

        # Override Makefile in img_root to echo env/args cleanly for promote, intake-confirm, assign
        (self.img_root / "Makefile").write_text(
            ".PHONY: promote intake intake-confirm assign console\n"
            "promote:\n\t@printf 'GO: promote reason=%s\\n' \"$(GO_FOR_LAUNCH_REASON)\"\n"
            "intake:\n\t@mkdir -p .attached/.lights-out/tickets && printf 'q\\n' > .attached/.lights-out/tickets/0099.intake.md && printf 'GO: intake\\n'\n"
            "intake-confirm:\n\t@printf 'GO: intake-confirm dec=%s\\n' \"$(DECISIONS)\"\n"
            "assign:\n\t@test -f .attached/.lights-out/tickets/0099.intake.md && printf 'GO: assign kept-ticket\\n'\n"
            "console:\n\t@printf 'GO: console port=%s\\n' \"$(CONSOLE_PORT)\"\n",
            encoding="utf-8",
        )

        res_p = self._run_entrypoint(["promote", "GO_FOR_LAUNCH=NO-GO", "REASON=metrics regression"], plane=True)
        self.assertEqual(res_p.returncode, 0, res_p.stdout + res_p.stderr)
        self.assertEqual(res_p.stdout.strip(), "GO: promote reason=metrics regression")

        res_i = self._run_entrypoint(["intake", "TEXT=hello"], plane=True)
        self.assertEqual(res_i.returncode, 0, res_i.stdout + res_i.stderr)

        res_ic = self._run_entrypoint(["intake-confirm", "TICKET=0099", "DECISIONS=@dec.json"], plane=True)
        self.assertEqual(res_ic.returncode, 0, res_ic.stdout + res_ic.stderr)
        self.assertEqual(res_ic.stdout.strip(), f"GO: intake-confirm dec=@{self.repo}/dec.json")

        res_a = self._run_entrypoint(["assign", "TICKET=0099"], plane=True)
        self.assertEqual(res_a.returncode, 0, res_a.stdout + res_a.stderr)
        self.assertEqual(res_a.stdout.strip(), "GO: assign kept-ticket")

        res_c = self._run_entrypoint(["console"], plane=True, extra_env={"CONSOLE_PORT": "9098"})
        self.assertEqual(res_c.returncode, 0, res_c.stdout + res_c.stderr)
        self.assertEqual(res_c.stdout.strip(), "GO: console port=9098")


class CiPackageBehavioralTests(unittest.TestCase):
    """Verify ci/package.sh passes in workbench and catches socket-boundary defects."""

    def test_ci_package_passes_and_catches_socket_boundary_defects(self):
        if not shutil.which("shellcheck"):
            self.fail("shellcheck not on PATH")
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.fail("PyYAML not installed")

        res = subprocess.run(["sh", "ci/package.sh"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "GO: package")

        # Plant a socket leak onto `gates` in a temporary repo copy and prove ci/package.sh fails NO-GO
        tmp = Path(tempfile.mkdtemp(prefix="pkg-defect-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, True)
        copy_root = tmp / "repo"
        shutil.copytree(
            ROOT, copy_root,
            ignore=shutil.ignore_patterns(".git", ".crew", ".attached", ".gates-out", ".claude", "__pycache__"),
        )
        lo = copy_root / "lights-out"
        text = lo.read_text(encoding="utf-8")
        mutated = text.replace(
            'set -- "$@" -v "$here:/repo:ro" -v "$out:/out"',
            'set -- "$@" -v "$here:/repo:ro" -v "$out:/out" -v /tmp/leak.sock:/var/run/docker.sock',
            1,
        )
        self.assertNotEqual(mutated, text)
        lo.write_text(mutated, encoding="utf-8")
        bad = subprocess.run(["sh", "ci/package.sh"], cwd=copy_root, capture_output=True, text=True, check=False)
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("NO-GO: package —", bad.stdout)


if __name__ == "__main__":
    unittest.main()
