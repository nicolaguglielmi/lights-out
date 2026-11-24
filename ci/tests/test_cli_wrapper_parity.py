"""Parity between core/cli.py (the grammar's single source of truth), the
`lights-out` wrapper and package/entrypoint.sh (docs/adr/0112 §6).

Written from the ADR before the wrapper and entrypoint changes. The wrapper
runs against fake `podman` shims (no engine, no network); the entrypoint runs
in a synthetic image root whose Makefile only echoes the target and the
variables it received. A drift between the three surfaces fails here.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core import cli
from core.tests.test_wrapper_plane import (
    WrapperPlaneBase,
    _extract_env_flags,
    _extract_mount_pairs,
    _read_calls,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE_PREFIX = "ghcr.io/nicolaguglielmi/lights-out"

READONLY = ("init", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety",
            "triage", "postmortem")
STATE = ("next", "dora", "spend", "cloud-setup", "map", "network", "scoreboard",
         "auto-incident", "incident", "alert-webhook", "top")
PLANE = ("start", "intake", "answer", "intake-confirm", "assign", "up", "down", "smoke", "release",
         "promote", "console", "evidence", "provenance", "try", "watch", "request", "resume", "audit",
         "production-readiness")
GATE_SHAPED = ("init", "try", "gates", "test-proof", "test-integrity", "adr-check", "migration-safety",
               "doctor", "map", "production-readiness")
NEEDS = {"alert-webhook": ["--alert-file", "a.json"]}


def image_args(argv: list[str]) -> list[str]:
    """What follows the image in a recorded `run` call: the flat command and its arguments."""
    for i, item in enumerate(argv):
        if item.startswith(IMAGE_PREFIX):
            return argv[i + 1:]
    raise AssertionError(f"no image in {argv}")


def image_commands() -> list[cli.Command]:
    return [c for c in cli.COMMANDS if c.surface == "image" and c.flat != "doctor"]


class WrapperParityBase(WrapperPlaneBase):
    def setUp(self):
        super().setUp()
        self.bindir, self.log = self._make_fake_engine("podman")
        self.sock = str(self.tmp / "podman-remote.sock")

    def run_w(self, args, **kw):
        extra = {"FAKE_REMOTE_SOCK": self.sock, "FAKE_REMOTE_EXISTS": "true", **kw.pop("extra_env", {})}
        return self._run_wrapper(args, bindir=self.bindir, extra_env=extra, **kw)

    def last_run(self) -> list[str]:
        runs = [c for c in _read_calls(self.log) if c and c[0] == "run"]
        self.assertEqual(len(runs), 1, runs)
        return runs[0]

    def fresh(self):
        self.log.unlink(missing_ok=True)


class WrapperGrammarParityTests(WrapperParityBase):
    def test_every_verb_reaches_the_image_as_its_flat_command(self):
        for command in image_commands():
            self.fresh()
            res = self.run_w([command.domain, command.verb, *NEEDS.get(command.flat, [])])
            self.assertEqual(res.returncode, 0, f"{command.domain} {command.verb}: {res.stderr}")
            args = image_args(self.last_run())
            self.assertEqual(args[0], command.flat, f"{command.domain} {command.verb}")

    def test_hierarchical_and_flat_spellings_run_the_same_container(self):
        for command in image_commands():
            extra = NEEDS.get(command.flat, [])
            self.fresh()
            self.run_w([command.domain, command.verb, *extra])
            hier = image_args(self.last_run())
            self.fresh()
            flat_extra = ["ALERT_FILE=a.json"] if command.flat == "alert-webhook" else []
            self.run_w([command.flat, *flat_extra])
            self.assertEqual(hier, image_args(self.last_run()), command.flat)

    def test_flag_translation_matches_core_cli(self):
        cases = (
            ["ticket", "assign", "--ticket", "0001"],
            ["ticket", "assign", "--ticket=0001"],
            ["ticket", "answer", "--ticket", "7", "--answer", "a b"],
            ["repo", "gates", "--base", "a", "--head", "b"],
            ["ops", "watch", "--once", "--poll-s", "5"],
            ["ticket", "request", "--ticket", "1", "--request-by", "ann"],
            ["ticket", "network", "--ticket", "1", "--attempt", "2"],
            ["ops", "incident", "--name", "n", "--from", "a", "--to", "b", "--logs", "l"],
            ["ops", "auto-incident", "--release-record", "r.json"],
            ["repo", "map", "--map-file", "m.json"],
            ["config", "cloud-setup", "--cloud", "x", "--slo-mode", "otel"],
            ["config", "start", "--non-interactive", "--preset", "junior", "--provider=replay"],
            ["config", "next", "--mode", "expert"],
            ["ops", "triage", "--alert", "a.json", "--profile", "l1"],
            ["release", "promote", "--go-for-launch", "GO", "--reason", "ok"],
            ["release", "deploy", "--canary-version", "2", "--canary-tag", "t"],
            ["ticket", "assign", "TICKET=3", "--json"],
            ["repo", "adr-check", "--base", "a", "--head", "b"],
            ["repo", "migration-safety", "--base", "a", "--head", "b", "--ticket-file", "t.md"],
            ["ops", "top", "--once", "--logbook-file", "l.json", "--view", "canary", "--width", "100", "--height", "30"],
            ["ops", "top", "--json"],
            ["repo", "init", "--apply", "--branch", "lo/init", "--commit", "--force",
             "--baseline-mode", "incremental", "--baseline-max-files", "5"],
            ["release", "provenance", "--version", "0.2.0", "--ticket", "0001", "--provenance-dir", ".crew/prov"],
        )
        for argv in cases:
            plan = cli.parse(argv)
            self.fresh()
            res = self.run_w(argv)
            self.assertEqual(res.returncode, 0, f"{argv}: {res.stderr}")
            got = image_args(self.last_run())
            want = [plan.command.flat, *plan.tail, *(["--" + plan.json_mode] if plan.json_mode else [])]
            self.assertEqual(got, want, argv)

    def test_extension_keys_are_per_command(self):
        for command in cli.COMMANDS:
            if command.surface != "image" or command.flat == "doctor":
                continue
            for key in command.keys:
                if key in cli.GLOBAL_KEYS:
                    continue
                self.fresh()
                extra = [] if command.flat == "alert-webhook" and key == "ALERT_FILE" else NEEDS.get(command.flat, [])
                res = self.run_w([command.flat, f"{key}=v", *extra])
                self.assertEqual(res.returncode, 0, f"{command.flat} {key}: {res.stderr}")
                self.assertIn(f"{key}=v", image_args(self.last_run()))
        self.fresh()
        res = self.run_w(["assign", "REQUEST_BY=x"])
        self.assertEqual((res.returncode, res.stdout), (2, ""))
        self.assertFalse(self.log.exists())

    def test_doctor_has_the_same_output_in_both_spellings(self):
        flat = self.run_w(["doctor"])
        hier = self.run_w(["repo", "doctor"])
        self.assertEqual((flat.returncode, flat.stdout), (hier.returncode, hier.stdout))
        self.assertTrue(flat.stdout.startswith("GO: doctor"), flat.stdout)

    def test_release_is_flat_without_a_verb(self):
        self.fresh()
        self.run_w(["release", "CANARY_VERSION=2"])
        self.assertEqual(image_args(self.last_run()), ["release", "CANARY_VERSION=2"])
        self.fresh()
        self.run_w(["release", "up"])
        self.assertEqual(image_args(self.last_run()), ["up"])
        self.fresh()
        self.run_w(["release", "deploy"])
        self.assertEqual(image_args(self.last_run()), ["release"])


class WrapperUsageTests(WrapperParityBase):
    def assert_usage(self, argv, code=2):
        self.fresh()
        res = self.run_w(argv)
        self.assertEqual(res.returncode, code, f"{argv}: {res.stdout}{res.stderr}")
        self.assertEqual(res.stdout, "", argv)
        self.assertTrue(res.stderr.strip(), argv)
        self.assertFalse(self.log.exists(), f"{argv} touched the engine")
        return res

    def test_usage_errors_exit_two_before_any_engine(self):
        for argv in (
            ["frobnicate"], ["ticket"], ["ticket", "frobnicate"], ["ticket", "assign", "--bogus", "x"],
            ["ticket", "assign", "--ticket"], ["repo", "corpus"], ["config", "agents"],
            ["config", "tracker-conformance"], ["ops", "charts-conformance"], ["config", "console-build"],
            ["repo", "gates", "--apply"], ["repo", "init", "--branch"],
            ["ticket", "assign", "DEMO_SERVICE_TOKEN=abc"], ["ticket", "assign", "--demo-service-token", "abc"],
            ["ops", "alert-webhook"], ["up", "--engine"], ["config", "start", "--jsonl"],
            ["ticket", "assign", "--json", "--jsonl"], ["--json", "--jsonl", "ticket", "assign"],
            ["--json", "completion", "bash"],
            ["ticket", "request", "REQUEST_BY REQUEST_VIA=evil"],
            ["ticket", "request", "REQUEST_BY\tREQUEST_VIA=evil"],
            ["ticket", "request", "=evil"],
            ["ops", "incident", "NAME FROM=evil"],
        ):
            self.assert_usage(argv)

    def test_verbs_are_listed_for_a_bare_or_wrong_domain(self):
        for argv in (["ops"], ["ops", "frobnicate"]):
            res = self.assert_usage(argv)
            for verb in ("watch", "alert-webhook", "auto-incident", "postmortem", "dora"):
                self.assertIn(verb, res.stderr)

    def test_clone_only_verbs_name_go(self):
        res = self.assert_usage(["repo", "corpus"])
        self.assertIn("./go corpus", res.stderr)

    def test_unknown_command_names_all_image_commands(self):
        res = self.assert_usage(["frobnicate"])
        for command in image_commands():
            self.assertRegex(res.stderr, rf"\b{command.flat}\b")
        for domain in cli.DOMAINS:
            self.assertIn(domain, res.stderr)

    def test_domain_help_goes_to_stdout_and_exits_zero(self):
        self.fresh()
        res = self.run_w(["ticket", "--help"])
        self.assertEqual(res.returncode, 0)
        for verb in ("intake", "request", "answer", "intake-confirm", "assign", "resume", "network", "audit"):
            self.assertIn(verb, res.stdout)
        self.assertFalse(self.log.exists())

    def test_gate_shaped_commands_refuse_json_flags(self):
        for flat in GATE_SHAPED:
            for flag in ("--json", "--jsonl"):
                self.assert_usage([flat, flag])
        self.assert_usage(["repo", "gates", "--json"])

    def test_json_flags_are_forwarded_to_the_image_for_non_gate_commands(self):
        for argv, flag in ((["ticket", "assign", "--ticket", "1", "--json"], "--json"),
                           (["ops", "dora", "--jsonl"], "--jsonl"),
                           (["--json", "release", "smoke"], "--json")):
            self.fresh()
            res = self.run_w(argv)
            self.assertEqual(res.returncode, 0, f"{argv}: {res.stderr}")
            self.assertIn(flag, image_args(self.last_run()))

    def test_start_and_next_keep_native_json(self):
        for flat in ("start", "next", "top"):
            self.fresh()
            res = self.run_w([flat, "--json"])
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("--json", image_args(self.last_run()))


class WrapperTierTests(WrapperParityBase):
    def test_every_core_image_command_has_a_tier(self):
        self.assertEqual({c.flat for c in image_commands()}, set(READONLY + STATE + PLANE) - {"console"})

    def test_tiers_and_socket_boundaries(self):
        plat, crew = str(self.out / "platform"), str(self.out / "crew")
        for flat in (*READONLY, *STATE, *PLANE):
            self.fresh()
            res = self.run_w([flat, *NEEDS.get(flat, [])])
            self.assertEqual(res.returncode, 0, f"{flat}: {res.stderr}")
            argv = self.last_run()
            mounts, envs = _extract_mount_pairs(argv), _extract_env_flags(argv)
            has_sock = any(m.endswith(":/var/run/docker.sock") for m in mounts)
            if flat in PLANE:
                self.assertTrue(has_sock, flat)
                self.assertIn(f"{plat}:{plat}", mounts, flat)
                self.assertEqual(len([ln for ln in res.stderr.splitlines() if "socket" in ln.lower()]), 1, flat)
            else:
                self.assertFalse(has_sock, flat)
                self.assertFalse(any(e.startswith("DOCKER_HOST") for e in envs), flat)
            if flat in STATE:
                self.assertIn(f"{crew}:/opt/lights-out/.crew", mounts, flat)
            if flat in READONLY:
                self.assertIn(f"{self.out}:/out", mounts, flat)

    def test_new_gates_opt_into_engine_socket_like_gates(self):
        for flat in ("adr-check", "migration-safety"):
            self.fresh()
            res = self.run_w([flat, "--engine"])
            self.assertEqual(res.returncode, 0, f"{flat}: {res.stderr}")
            argv = self.last_run()
            self.assertIn(f"ghcr.io/nicolaguglielmi/lights-out:{self.version}", argv)
            self.assertTrue(any(m.endswith(":/var/run/docker.sock") for m in _extract_mount_pairs(argv)), flat)
            self.assertIn("LIGHTS_OUT_ENGINE_GATES=1", _extract_env_flags(argv))
        self.fresh()
        bad = self.run_w(["ops", "top", "--engine"])
        self.assertEqual((bad.returncode, bad.stdout), (2, ""))
        self.assertFalse(self.log.exists())

    def test_incident_with_a_ticket_needs_the_plane(self):
        self.fresh()
        self.run_w(["ops", "incident", "--name", "n", "--from", "a", "--to", "b"])
        self.assertFalse(any(m.endswith(":/var/run/docker.sock") for m in _extract_mount_pairs(self.last_run())))
        self.fresh()
        self.run_w(["ops", "incident", "--name", "n", "--from", "a", "--to", "b", "--ticket", "1"])
        self.assertTrue(any(m.endswith(":/var/run/docker.sock") for m in _extract_mount_pairs(self.last_run())))

    def test_try_passes_the_host_repository_path_for_the_draft_name(self):
        self.fresh()
        self.run_w(["repo", "try"])
        self.assertIn(f"TRY_SOURCE={self.repo}", _extract_env_flags(self.last_run()))

    def test_new_commands_run_from_the_repository_root(self):
        sub = self.repo / "sub"
        sub.mkdir()
        for flat in ("map", "resume", "scoreboard"):
            self.fresh()
            self.run_w([flat], cwd=sub)
            self.assertIn(f"{self.repo}:/repo:ro", _extract_mount_pairs(self.last_run()), flat)

    def test_secrets_stay_by_name_for_new_commands(self):
        secret = "canary-secret-value-90210"
        for flat in ("resume", "watch", "audit", "provenance"):
            self.fresh()
            self.run_w([flat], extra_env={"METRICS_AUTH_HEADER": secret})
            argv = self.last_run()
            self.assertIn("METRICS_AUTH_HEADER", _extract_env_flags(argv))
            self.assertNotIn(secret, "\n".join(argv))

    def test_init_readonly_vs_apply_rw_parity(self):
        self.fresh()
        res_ro = self.run_w(["repo", "init"])
        self.assertEqual(res_ro.returncode, 0, res_ro.stderr)
        mounts_ro = _extract_mount_pairs(self.last_run())
        self.assertIn(f"{self.repo}:/repo:ro", mounts_ro)
        self.assertNotIn(f"{self.repo}:/repo:rw", mounts_ro)
        self.assertNotIn("read-write", res_ro.stderr)

        self.fresh()
        res_rw = self.run_w(["repo", "init", "--apply", "--commit", "--baseline-mode", "incremental"])
        self.assertEqual(res_rw.returncode, 0, res_rw.stderr)
        mounts_rw = _extract_mount_pairs(self.last_run())
        self.assertIn(f"{self.repo}:/repo:rw", mounts_rw)
        self.assertNotIn(f"{self.repo}:/repo:ro", mounts_rw)
        self.assertEqual(len([ln for ln in res_rw.stderr.splitlines() if "read-write" in ln]), 1, res_rw.stderr)


class CompletionParityTests(WrapperParityBase):
    def test_wrapper_completion_is_byte_identical_to_core_cli_and_needs_no_engine(self):
        for shell in ("bash", "zsh", "fish"):
            res = self._run_wrapper(["completion", shell])
            self.assertEqual(res.returncode, 0, f"{shell}: {res.stderr}")
            self.assertEqual(res.stdout, cli.completion(shell), shell)
            self.assertEqual(res.stderr, "", shell)
        self.assertFalse(self.log.exists())

    def test_domain_spelling_is_the_same(self):
        res = self.run_w(["completion", "bash"])
        self.assertEqual(res.stdout, cli.completion("bash"))

    def test_bad_shell_is_usage(self):
        for argv in (["completion"], ["completion", "tcsh"], ["completion", "bash", "extra"]):
            res = self._run_wrapper(argv)
            self.assertEqual((res.returncode, res.stdout), (2, ""), argv)

    def test_bash_script_parses(self):
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash not available")
        script = self.tmp / "comp.bash"
        script.write_text(cli.completion("bash"), encoding="utf-8")
        res = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True, check=False)
        self.assertEqual(res.returncode, 0, res.stderr)


class EntrypointParityTests(unittest.TestCase):
    """package/entrypoint.sh in a synthetic image root: same grammar, same keys, same envelope."""

    def setUp(self):
        if not shutil.which("make"):
            self.skipTest("make is required")
        self.tmp = Path(tempfile.mkdtemp(prefix="entry-parity-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.img = self.tmp / "img"
        for d in ("package", "ci", "core"):
            (self.img / d).mkdir(parents=True)
        (self.img / ".crew").mkdir()
        shutil.copy2(ROOT / "package" / "entrypoint.sh", self.img / "package" / "entrypoint.sh")
        shutil.copy2(ROOT / "ci" / "lib.sh", self.img / "ci" / "lib.sh")
        shutil.copy2(ROOT / "core" / "cli.py", self.img / "core" / "cli.py")
        (self.img / "core" / "await-head.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (self.img / "core" / "attach.sh").write_text(
            "#!/bin/sh\nmkdir -p \"$2\"\nprintf 'attached\\n' > \"$2/marker\"\n", encoding="utf-8")
        (self.img / "Makefile").write_text(
            "GATES := risk\n"
            "init:\n"
            "\t@printf 'GO: init — W=%s CLI=%s\\n' \"$(WORKLOAD)\" \"$(LIGHTS_OUT_CLI_ARGS)\"\n"
            "provenance:\n"
            "\t@printf 'GO: provenance — PD=%s V=%s\\n' \"$(PROVENANCE_DIR)\" \"$(VERSION)\"\n"
            ".DEFAULT:\n"
            "\t@printf '%s\\n' \"$(if $(LINE),$(LINE),GO: $@ — T=$(TICKET) N=$(NAME) F=$(FROM) O=$(ONCE) P=$(POLL_S) "
            "R=$(RELEASE_RECORD) M=$(MAP_FILE) A=$(ALERT_FILE) B=$(REQUEST_BY) K=$(ATTEMPT) S=$(SLO_MODE))\"\n"
            "\t@$(if $(FAIL),exit 1,:)\n",
            encoding="utf-8",
        )
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        for tool in ("sh", "awk", "sed", "grep", "tr", "sort", "head", "tail", "cut", "cksum", "cat", "uname",
                     "basename", "dirname", "mkdir", "rm", "mktemp", "id", "env", "wc", "stat", "tar", "ln",
                     "cp", "git", "make", "printf"):
            found = shutil.which(tool)
            if found:
                (self.tools / tool).symlink_to(found)
        (self.tools / "python3").symlink_to(sys.executable)
        self.plat, self.crew = self.tmp / "platform", self.tmp / "crew"
        self.plat.mkdir()
        self.crew.mkdir()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def run_e(self, args, flavour="full", plane=True, extra=None):
        env = {
            "PATH": str(self.tools), "HOME": str(self.tmp), "LIGHTS_OUT_IMAGE_ROOT": str(self.img),
            "LIGHTS_OUT_REPO_DIR": str(self.repo), "LIGHTS_OUT_FLAVOUR": flavour, "LIGHTS_OUT_VERSION": "9.9.9",
            "LIGHTS_OUT_REVISION": "r1",
        }
        if plane:
            env.update(LIGHTS_OUT_PLATFORM_DIR=str(self.plat), LIGHTS_OUT_CREW_DIR=str(self.crew))
        env.update(extra or {})
        return subprocess.run(["sh", str(self.img / "package" / "entrypoint.sh"), *args], env=env,
                              capture_output=True, text=True, check=False)

    def test_hierarchical_flags_become_make_variables(self):
        res = self.run_e(["ops", "incident", "--name", "n1", "--from", "a"], plane=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(res.stdout.strip(), "GO: incident — T= N=n1 F=a O= P= R= M= A= B= K= S=")
        res = self.run_e(["config", "scoreboard"], plane=False)
        self.assertTrue(res.stdout.startswith("GO: scoreboard"), res.stdout)

    def test_new_state_commands_run_without_attach_or_contract(self):
        for argv, want in (
            (["ops", "auto-incident", "--release-record", "r.json"], "R=r.json"),
            (["ops", "alert-webhook", "--alert-file", "a.json"], f"A={self.repo}/a.json"),
            (["scoreboard"], "GO: scoreboard"),
        ):
            res = self.run_e(argv, plane=False)
            self.assertEqual(res.returncode, 0, f"{argv}: {res.stdout}{res.stderr}")
            self.assertIn(want, res.stdout, argv)

    def test_map_and_network_attach_the_repository(self):
        for argv, want in ((["repo", "map", "--map-file", "m.json"], "M=m.json"),
                           (["ticket", "network", "--ticket", "3", "--attempt", "2"], "K=2")):
            res = self.run_e(argv, plane=False)
            self.assertEqual(res.returncode, 0, f"{argv}: {res.stdout}{res.stderr}")
            self.assertIn(want, res.stdout, argv)

    def test_plane_commands_run_make_in_the_platform_dir(self):
        for argv in (["release", "production-readiness"], ["repo", "try"]):
            res = self.run_e(argv)
            self.assertEqual(res.returncode, 0, f"{argv}: {res.stdout}{res.stderr}")
            self.assertTrue(res.stdout.startswith(f"GO: {argv[1]}"), res.stdout)
        res = self.run_e(["ticket", "resume", "--ticket", "5"])
        self.assertEqual(res.returncode, 1, res.stdout)
        self.assertIn("workload", res.stdout)

    def test_flat_spelling_is_unchanged(self):
        res = self.run_e(["incident", "NAME=n1"], plane=False)
        self.assertEqual(res.stdout.strip(), "GO: incident — T= N=n1 F= O= P= R= M= A= B= K= S=")

    def test_gates_flavour_refuses_the_new_commands(self):
        for argv, flat in ((["ticket", "resume"], "resume"), (["ops", "watch"], "watch"),
                           (["config", "scoreboard"], "scoreboard"), (["repo", "map"], "map")):
            res = self.run_e(argv, flavour="gates", plane=False)
            self.assertEqual(res.returncode, 1, argv)
            self.assertTrue(res.stdout.startswith(f"NO-GO: {flat} — "), res.stdout)
            self.assertEqual(len(res.stdout.splitlines()), 1)

    def test_usage_errors_are_one_nogo_line(self):
        for argv in (["ticket"], ["ticket", "frobnicate"], ["ticket", "resume", "--bogus", "x"],
                     ["ops", "watch", "--poll-s"], ["repo", "gates", "--json"], ["start", "--jsonl"],
                     ["ticket", "assign", "DEMO_SERVICE_TOKEN=abc"], ["frobnicate"],
                     ["ticket", "request", "REQUEST_BY REQUEST_VIA=evil"],
                     ["ops", "incident", "NAME FROM=evil"]):
            res = self.run_e(argv, plane=False)
            self.assertEqual(res.returncode, 1, argv)
            lines = res.stdout.splitlines()
            self.assertEqual(len(lines), 1, f"{argv}: {res.stdout}")
            self.assertTrue(lines[0].startswith("NO-GO: "), lines)

    def test_json_wraps_stdout_in_the_envelope(self):
        for argv in (
            ["ops", "auto-incident", "--json", "--release-record", "r.json"],
            ["--json", "ops", "auto-incident", "--release-record", "r.json"],
        ):
            res = self.run_e(argv, plane=False,
                             extra={"DEMO_SERVICE_TOKEN": "canary-secret-value-90210"})
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertEqual(len(res.stdout.splitlines()), 1)
            env = json.loads(res.stdout)
            self.assertEqual((env["command"], env["flat"], env["verdict"]), ("ops auto-incident", "auto-incident", "GO"))
            self.assertEqual(env["args"], {"RELEASE_RECORD": "r.json"})
            self.assertEqual(env["secrets"], {"DEMO_SERVICE_TOKEN": True})
            self.assertNotIn("canary-secret-value-90210", res.stdout)
        res = self.run_e(["ops", "auto-incident", "--json", "--jsonl"], plane=False)
        self.assertEqual(res.returncode, 1)
        self.assertIn("mutually exclusive", res.stdout)

    def test_json_exit_code_follows_the_taxonomy(self):
        for line, code, verdict in (("NO-GO: scoreboard — plain", 1, "NO-GO"),
                                    ("NO-GO: scoreboard — ticket 1 needs-human", 3, "WAITING"),
                                    ("NO-GO: scoreboard — over budget", 4, "BUDGET")):
            res = self.run_e(["config", "scoreboard", "--json"], plane=False,
                             extra={"LINE": line, "FAIL": "1"})
            self.assertEqual(res.returncode, code, f"{line}: {res.stdout}{res.stderr}")
            self.assertEqual(json.loads(res.stdout)["verdict"], verdict)

    def test_jsonl_emits_events_then_a_verdict(self):
        res = self.run_e(["ops", "auto-incident", "--jsonl"], plane=False)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        events = [json.loads(ln) for ln in res.stdout.splitlines()]
        self.assertEqual(events[-1]["event"], "verdict")
        self.assertEqual(events[0]["event"], "stdout")

    def test_start_and_next_json_stay_native(self):
        for argv, prefix in ((["config", "next", "--json"], "GO: next"), (["ops", "top", "--json"], "GO: top")):
            res = self.run_e(argv, plane=False)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertTrue(res.stdout.startswith(prefix), res.stdout)
        for gate in ("adr-check", "migration-safety"):
            res = self.run_e(["repo", gate, "--json"], plane=False)
            self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
            self.assertTrue(res.stdout.startswith("NO-GO: "), res.stdout)

    def test_entrypoint_init_apply_and_provenance_parity(self):
        subprocess.run(["git", "-C", str(self.repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@invalid",
                        "commit", "--allow-empty", "-q", "-m", "seed"], check=True)
        (self.img / "core" / "attach.sh").write_text(
            "#!/bin/sh\nrm -rf \"$2\"\ngit clone -q \"$1\" \"$2\"\n",
            encoding="utf-8",
        )
        init_log = self.tmp / "init-calls.log"
        (self.img / "Makefile").write_text(
            "GATES := risk\n"
            "init:\n"
            f"\t@printf 'W=%s A=%s CLI=%s\\n' \"$(WORKLOAD)\" \"$(ATTACHED)\" \"$(LIGHTS_OUT_CLI_ARGS)\" >> {init_log}\n"
            "\t@printf 'GO: init — W=%s CLI=%s\\n' \"$(WORKLOAD)\" \"$(LIGHTS_OUT_CLI_ARGS)\"\n"
            "provenance:\n"
            "\t@printf 'GO: provenance — PD=%s V=%s\\n' \"$(PROVENANCE_DIR)\" \"$(VERSION)\"\n",
            encoding="utf-8",
        )

        res_plain = self.run_e(["repo", "init"], plane=False, extra={"LIGHTS_OUT_CONTAINER_OUT": str(self.tmp / "out1")})
        self.assertEqual(res_plain.returncode, 0, res_plain.stdout + res_plain.stderr)
        self.assertIn("GO: init — W=the repository", res_plain.stdout)

        res_apply = self.run_e(
            ["repo", "init", "--apply", "--branch", "lo/init", "--commit", "--baseline-mode", "incremental",
             "--baseline-max-files", "4"],
            plane=False,
            extra={"LIGHTS_OUT_CONTAINER_OUT": str(self.tmp / "out2")},
        )
        self.assertEqual(res_apply.returncode, 0, res_apply.stdout + res_apply.stderr)
        calls = init_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("W=/tmp/lights-out/attached A=/tmp/lights-out/attached CLI=", calls[0])
        self.assertIn(f"W={self.repo} A={self.repo}", calls[1])
        self.assertIn("--apply\x1f--branch\x1flo/init\x1f--commit\x1f--baseline-mode\x1fincremental\x1f--baseline-max-files\x1f4", calls[1])

        res_prov = self.run_e(["release", "provenance", "--provenance-dir", ".crew/prov", "--version", "0.8.1", "--json"])
        self.assertEqual(res_prov.returncode, 0, res_prov.stdout + res_prov.stderr)
        env = json.loads(res_prov.stdout)
        self.assertEqual((env["command"], env["flat"], env["verdict"]), ("release provenance", "provenance", "GO"))
        self.assertIn("PD=.crew/prov V=0.8.1", env["line"])


if __name__ == "__main__":
    unittest.main()
