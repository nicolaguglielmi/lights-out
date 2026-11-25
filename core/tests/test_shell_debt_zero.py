# SPDX-License-Identifier: Apache-2.0
"""Tests verifying zero core/ shell-boundary debt and behavior of migrated Python modules."""

from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import re
import subprocess  # nosec B404 - test stubs return synthetic CompletedProcess objects
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import (
    adr_gov,
    cmd_util,
    crew_cmd,
    egress_cmd,
    launchpad_cmd,
    mutation_baseline,
    postmortem_cmd,
    promote_cmd,
    step_ci,
    step_release,
    step_review,
    step_rework,
)

ROOT = Path(__file__).resolve().parents[2]
HOST_SCRIPTS = {
    "core/doctor.sh",
    "core/attach.sh",
    "core/preflight.sh",
    "core/progress.sh",
}
RETIRED_WRAPPERS = (
    "core/crew.sh",
    "core/egress.sh",
    "core/launchpad.sh",
    "core/mutation-baseline.sh",
    "core/postmortem.sh",
    "core/promote.sh",
    "core/steps/ci.sh",
    "core/steps/release.sh",
    "core/steps/review.sh",
    "core/steps/rework.sh",
)


def _code_lines(path: Path) -> list[str]:
    return [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _cp(rc: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["stub"], returncode=rc, stdout=stdout, stderr=stderr)


class ShellBoundaryZeroTests(unittest.TestCase):
    """Repository-wide invariants after retiring all 10 debt scripts."""

    def test_shell_boundary_debt_is_empty(self) -> None:
        text = (ROOT / "ci" / "shell-boundary.sh").read_text(encoding="utf-8")
        match = re.search(r"^debt=(['\"])(.*?)\1", text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "ci/shell-boundary.sh missing debt= assignment")
        assert match is not None
        self.assertEqual(match.group(2).strip(), "", f"expected empty debt, got {match.group(2)!r}")

    def test_all_non_host_core_sh_under_limit_and_retired_wrappers_under_25(self) -> None:
        for path in sorted((ROOT / "core").rglob("*.sh")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in HOST_SCRIPTS:
                continue
            code = _code_lines(path)
            self.assertLessEqual(len(code), 30, f"{rel} has {len(code)} code lines > 30")
        for rel in RETIRED_WRAPPERS:
            code = _code_lines(ROOT / rel)
            self.assertLessEqual(len(code), 25, f"{rel} wrapper has {len(code)} code lines > 25")

    def test_adrs_0121_and_0131_pass_adr_gov(self) -> None:
        for adr in (
            ROOT / "docs/adr/0121-closed-deterministic-self-healing-loop.md",
            ROOT / "docs/adr/0131-retire-core-shell-boundary-debt.md",
        ):
            text = adr.read_text(encoding="utf-8")
            self.assertEqual(adr_gov.validate_adr(text), [], f"{adr.name} failed adr_gov")

    def test_new_core_modules_have_no_self_core_imports(self) -> None:
        modules = (
            "cmd_util", "crew_cmd", "egress_cmd", "launchpad_cmd", "mutation_baseline",
            "postmortem_cmd", "promote_cmd", "step_ci", "step_release", "step_review", "step_rework",
        )
        bad_import = re.compile(r"^\s*(from core\.|import core([^\w]|$))", re.MULTILINE)
        for mod in modules:
            importlib.import_module(f"core.{mod}")
            src = (ROOT / "core" / f"{mod}.py").read_text(encoding="utf-8")
            self.assertIsNone(bad_import.search(src), f"core/{mod}.py imports core directly")


class CmdUtilTests(unittest.TestCase):
    """Shared contract output, env parsing, and StringIO-safe command execution."""

    def test_nogo_writes_stdout_stderr_and_nogo_details(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            details_file = Path(tmp) / "gate.details"
            out = io.StringIO()
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"NOGO_DETAILS": str(details_file)}, clear=False):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as ctx:
                        cmd_util.nogo("sample", "line1\nline2", "detail a", "detail b")
            self.assertEqual(ctx.exception.code, 1)
            self.assertEqual(out.getvalue(), "NO-GO: sample — line1 line2\n")
            self.assertEqual(err.getvalue(), "detail a\ndetail b\n")
            self.assertEqual(details_file.read_text(encoding="utf-8"), "detail a\ndetail b\n")

    def test_parse_env_file_handles_comments_quotes_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "test.env"
            env_path.write_text(
                "# comment\nFOO=bar\nexport BAR='quoted val'\nBAZ=\"hello world\"\nEMPTY=\n",
                encoding="utf-8",
            )
            parsed = cmd_util.parse_env_file(env_path)
            self.assertEqual(
                parsed, {"FOO": "bar", "BAR": "quoted val", "BAZ": "hello world", "EMPTY": ""}
            )

    def test_run_cmd_forwards_streams_when_redirected_to_stringio(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            res = cmd_util.run_cmd(["python3", "-c", "import sys; print('hi'); print('yo', file=sys.stderr)"])
        self.assertEqual(res.returncode, 0)
        self.assertEqual(out.getvalue(), "hi\n")
        self.assertEqual(err.getvalue(), "yo\n")

    def test_run_tee_captures_stdout_and_tees_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "sub" / "run.log"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc, stdout_text = cmd_util.run_tee(
                    [
                        "python3", "-c",
                        "import sys; sys.stdout.write('GO: launchpad\\n'); sys.stderr.write('slo: p99=10ms\\n')",
                    ],
                    log,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(stdout_text, "GO: launchpad\n")
            self.assertEqual(err.getvalue(), "slo: p99=10ms\n")
            self.assertEqual(log.read_text(encoding="utf-8"), "slo: p99=10ms\n")


class EgressAndMutationBaselineTests(unittest.TestCase):
    """Behavior of core/egress_cmd.py and core/mutation_baseline.py."""

    def test_egress_resolve_hosts_model_url_and_contract_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = Path(tmp) / "contract"
            contract.mkdir()
            (contract / "crew-egress.txt").write_text("# comment\npkg.example.org:443\n", encoding="utf-8")
            hosts = egress_cmd.resolve_hosts(
                "ignored.example:443",
                {"MODEL_BASE_URL": "https://api.example.net/v1", "WORKLOAD_CONTRACT": str(contract)},
            )
            self.assertEqual(hosts, ["api.example.net:443", "pkg.example.org:443"])

    def test_egress_rejects_missing_port_and_succeeds_when_ready_matches(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                egress_cmd.resolve_hosts("bad.host", {})
        self.assertIn("has no port", err.getvalue())

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                (Path(".crew") / "egress").mkdir(parents=True)
                calls: list[list[str]] = []

                def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    calls.append(list(argv))
                    if "up" in argv and len(calls) == 1:
                        return _cp(1)
                    if "up" in argv:
                        return _cp(0)
                    (Path(".crew") / "egress" / "ready").write_text("abc123def456\n", encoding="utf-8")
                    return _cp(0, stdout="abc123def4567890\n")

                with mock.patch.object(cmd_util, "run_cmd", side_effect=fake_run):
                    rc = egress_cmd.main(
                        ["proxy.example.com:8443"], env={}, max_wait=2, poll_interval=0.0
                    )
                self.assertEqual(rc, 0)
                self.assertEqual(
                    Path(".crew/egress/allow").read_text(encoding="utf-8"),
                    "proxy.example.com:8443\n",
                )
            finally:
                os.chdir(old_cwd)

    def test_mutation_baseline_accepts_zero_killed_and_rejects_zero_total(self) -> None:
        env = {
            "WORKLOAD": ".",
            "MUTATION_CMD": "run-mutants",
            "WORKLOAD_MUTATION_SCOPE": "src/",
        }

        def fake_sh_ok(script: str, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if "--preflight" in args:
                return _cp(0, stdout="container")
            return _cp(0, stdout='noise line\n{"total": 5, "killed": 0, "score": 0.0}\n')

        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh_ok):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = mutation_baseline.main([], env=env)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), '{"total": 5, "killed": 0, "score": 0.0}\n')
        self.assertIn("0/5 killed, score=0.0", err.getvalue())

        def fake_sh_zero(_script: str, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if "--preflight" in args:
                return _cp(0, stdout="container")
            return _cp(0, stdout='{"total": 0, "killed": 0, "score": 0}\n')

        err = io.StringIO()
        with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh_zero):
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit) as ctx:
                    mutation_baseline.main([], env=env)
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("0 mutants in src/", err.getvalue())


class LaunchpadPromotePostmortemTests(unittest.TestCase):
    """Behavior of launchpad_cmd, promote_cmd, and postmortem_cmd."""

    def test_launchpad_happy_path_and_slo_rollback(self) -> None:
        env = {
            "CANARY_VERSION": "v1.2.3",
            "CANARY_TAG": "sha-abc",
            "DELIVERY": "mockdel",
            "METRICS": "mockmet",
            "REGISTRY": "mockreg",
            "FLAGS": "mockflags",
            "STAGE_SECONDS": "0",
        }
        calls: list[tuple[str, tuple[str, ...]]] = []

        def fake_sh(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            s = str(script)
            calls.append((s, args))
            if s.endswith("status.sh"):
                return _cp(0, stdout='{"stable": "v1.0.0"}')
            if s.endswith("evaluate.sh"):
                return _cp(0, stdout="false")
            if s.endswith("verify.sh"):
                return _cp(0, stdout="img@sha256:111")
            if s.endswith("sbom.sh"):
                return _cp(0, stdout="sha256:sbom")
            if s.endswith("verify/slo.sh"):
                return _cp(0, stdout="GO: slo", stderr="slo: err=0%\n")
            return _cp(0)

        def fake_py(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((str(script), args))
            if args and args[0] == "stage":
                return _cp(0, stdout='[{"pct": 5}]')
            return _cp(0)

        out = io.StringIO()
        err = io.StringIO()
        with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
             mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = launchpad_cmd.main([], env=env, sleep_fn=lambda _s: None)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "GO: launchpad\n")
        self.assertIn(("core/releases.py", ("record", "held", "25", '[{"pct": 5}]', "img@sha256:111", "sha256:sbom", "")), calls)

        def fail_slo_sh(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if str(script).endswith("verify/slo.sh"):
                return _cp(1, stdout="NO-GO: slo — p99 too high", stderr="slo: p99=900ms\n")
            return fake_sh(script, *args)

        out2 = io.StringIO()
        with mock.patch.object(cmd_util, "run_sh", side_effect=fail_slo_sh), \
             mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
             contextlib.redirect_stdout(out2), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                launchpad_cmd.main([], env=env, sleep_fn=lambda _s: None)
        self.assertEqual(ctx.exception.code, 1)
        self.assertEqual(out2.getvalue(), "NO-GO: launchpad — 5%: p99 too high → rolled back\n")

    def test_promote_replay_go_and_sha_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                Path("roles/release").mkdir(parents=True)
                Path("roles/release/role.env").write_text("HARNESS=adk\nTHINKING=low\nTIMEOUT_S=900\n", encoding="utf-8")
                rec_path = Path(".crew/release.json")
                rec_path.parent.mkdir(parents=True)
                rec_path.write_text(
                    json.dumps({"verdict": "held", "version": "v2", "stages": [{"pct": 5}, {"pct": 25}]}),
                    encoding="utf-8",
                )
                env = {
                    "DELIVERY": "mockdel",
                    "AGENT_HARNESS": "replay",
                    "GO_FOR_LAUNCH": "GO",
                }

                def fake_sh(script: object, *_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if str(script).endswith("status.sh"):
                        return _cp(0, stdout='{"canary": "v2", "weight": 25}')
                    return _cp(0)

                def fake_py(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if str(script).endswith("releases.py") and args[0] == "brief":
                        return _cp(0, stdout="Brief for v2 across 5% and 25%.")
                    return _cp(0)

                out = io.StringIO()
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = promote_cmd.main([], env=env)
                self.assertEqual(rc, 0)
                self.assertEqual(out.getvalue(), "GO: go-for-launch\n")
                self.assertIn("core/promote.sh", Path(".crew/promote/brief.author").read_text(encoding="utf-8"))

                out_bad = io.StringIO()
                env_bad = {**env, "GO_FOR_LAUNCH_BRIEF_SHA256": "0" * 64}
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     contextlib.redirect_stdout(out_bad), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        promote_cmd.main([], env=env_bad)
                self.assertIn("brief on disk is not the one answered", out_bad.getvalue())
            finally:
                os.chdir(old)

    def test_postmortem_files_tickets_and_renders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                Path("roles/ops-triage").mkdir(parents=True)
                Path("roles/ops-triage/role.env").write_text("HARNESS=adk\nTHINKING=low\nTIMEOUT_S=900\n", encoding="utf-8")
                Path("adapters/adk").mkdir(parents=True)
                Path("adapters/adk/run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                inc = Path("inc.json")
                inc.write_text(
                    json.dumps({"name": "inc-1", "release": {"version": "v1", "verdict": "rolled-back"}}),
                    encoding="utf-8",
                )

                def fake_sh(script: object, *_args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("run.sh"):
                        out_dir = Path(kwargs["env"]["OUT"])  # type: ignore[index]
                        (out_dir / "calls.json").write_text('[{"name": "file_postmortem"}]', encoding="utf-8")
                        (out_dir / "run.json").write_text("{}", encoding="utf-8")
                        return _cp(0)
                    if s.endswith("put.sh"):
                        return _cp(0, stdout="0099\n")
                    return _cp(0)

                def fake_py(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if str(script).endswith("postmortem.py") and args[0] == "check":
                        Path(args[3]).write_text(
                            json.dumps({"actions": [{"n": 1, "title": "Fix bug", "problem": "Broke", "acceptance": "Fixed"}]}),
                            encoding="utf-8",
                        )
                    return _cp(0)

                out = io.StringIO()
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = postmortem_cmd.main([], env={"INCIDENT": str(inc), "AGENT_HARNESS": "adk", "TRACKER": "mocktr"})
                self.assertEqual(rc, 0)
                self.assertEqual(out.getvalue(), "GO: postmortem — 1 action item(s): tickets 0099\n")
            finally:
                os.chdir(old)


class CrewAndStepsTests(unittest.TestCase):
    """Behavior of crew_cmd, step_ci, step_release, step_review, and step_rework."""

    def test_crew_writes_pr_opened_env_and_strips_empty_crew_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                Path("roles/dev-worker").mkdir(parents=True)
                Path("roles/dev-worker/prompt.md").write_text("Prompt\n", encoding="utf-8")
                Path("roles/dev-worker/role.env").write_text("THINKING=high\nMAX_TURNS=60\nTIMEOUT_S=900\n", encoding="utf-8")
                Path("contract").mkdir()
                Path("contract/worker.md").write_text("Brief\n", encoding="utf-8")

                seen_harness_env: dict[str, str] = {}

                def fake_sh(script: object, *_args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("get.sh"):
                        return _cp(0, stdout="Ticket body\n")
                    if s.endswith("clone-url.sh"):
                        return _cp(0, stdout="file:///repo\n")
                    if s.endswith("run.sh"):
                        seen_harness_env.update(kwargs["env"])  # type: ignore[arg-type]
                        return _cp(0)
                    if s.endswith("open-pr.sh"):
                        return _cp(0, stdout="42 http://host/pr/42\n")
                    return _cp(0)

                def fake_py(script: object, *_args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if str(script).endswith("agents.py"):
                        return _cp(0, stdout="")
                    if str(script).endswith("spend.py"):
                        return _cp(0, stdout="2600000\n")
                    return _cp(0)

                def fake_git(_repo: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if "rev-parse" in args:
                        return _cp(0, stdout="deadbeef\n")
                    if "diff" in args:
                        return _cp(0, stdout="core/foo.py\n")
                    return _cp(0)

                out = io.StringIO()
                env = {
                    "TICKET": "0001",
                    "AGENT_HARNESS": "replay",
                    "CODE_HOST": "mockhost",
                    "TRACKER": "mocktr",
                    "WORKLOAD": ".",
                    "WORKLOAD_CONTRACT": "contract",
                    "CREW_MCP": "stale-from-parent",
                }
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     mock.patch.object(cmd_util, "git_cmd", side_effect=fake_git), \
                     mock.patch.object(cmd_util, "run_cmd", return_value=_cp(0)), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = crew_cmd.main([], env=env)
                self.assertEqual(rc, 0)
                self.assertEqual(out.getvalue(), "GO: crew\n")
                self.assertNotIn("CREW_MCP", seen_harness_env)
                self.assertEqual(
                    Path(".crew/0001/pr-opened.env").read_text(encoding="utf-8"),
                    "PR_NUMBER=42\nPR_URL=http://host/pr/42\nBASE_SHA=deadbeef\nHEAD_SHA=deadbeef\n",
                )
            finally:
                os.chdir(old)

    def test_step_ci_perimeter_failure_emits_structured_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                posted: list[tuple[str, ...]] = []

                def fake_sh(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("clone-url.sh"):
                        return _cp(0, stdout="file:///repo\n")
                    if s.endswith("get.sh"):
                        return _cp(0, stdout="# Ticket\n")
                    if s.endswith("perimeter.sh"):
                        return _cp(1, stdout="outside.txt: not in perimeter\n")
                    if s.endswith("status.sh"):
                        posted.append(args)
                        return _cp(0)
                    return _cp(0)

                def fake_git(_repo: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if "rev-parse" in args:
                        return _cp(0, stdout="head123\n")
                    if "merge-base" in args:
                        return _cp(0, stdout="base123\n")
                    if "diff" in args:
                        return _cp(0, stdout="outside.txt\n")
                    return _cp(0)

                out = io.StringIO()
                env = {
                    "TICKET": "0001", "PR_NUMBER": "7", "BASE_SHA": "base123", "HEAD_SHA": "head123",
                    "CODE_HOST": "mockhost", "TRACKER": "mocktr", "WORKLOAD": ".", "WORKLOAD_CONTRACT": "c",
                }
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "git_cmd", side_effect=fake_git), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_ci.main([], env=env)
                self.assertEqual(rc, 0)
                doc = json.loads(out.getvalue())
                self.assertEqual(doc["state"], "ci-failed")
                self.assertEqual(doc["ci"]["failed"], "perimeter")
                self.assertIsNone(doc["ci"]["seconds"])
                self.assertEqual(posted[0][2], "gates")
            finally:
                os.chdir(old)

    def test_step_release_parses_tee_log_and_release_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                doc_file = Path("doc.json")
                doc_file.write_text(json.dumps({"merge": {"sha": "abc123"}}), encoding="utf-8")
                rec_file = Path(".crew/release.json")

                def fake_tee(_argv: object, log_path: object, **_kwargs: object) -> tuple[int, str]:
                    Path(log_path).write_text("info\nslo: err=0%\nslo: p99=12ms\n", encoding="utf-8")  # type: ignore[arg-type]
                    rec_file.parent.mkdir(parents=True, exist_ok=True)
                    rec_file.write_text(json.dumps({"image": "img@1", "sbom": "sha256:s", "dast": "clean"}), encoding="utf-8")
                    return 0, "GO: launchpad\n"

                def fake_git(_repo: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    if "--show-prefix" in args:
                        return _cp(0, stdout="")
                    return _cp(0, stdout="deadbeef\n")

                out = io.StringIO()
                env = {
                    "TICKET": "0001", "CANARY_VERSION": "v1", "DOC_FILE": str(doc_file),
                    "CODE_HOST": "mockhost", "WORKLOAD": ".",
                }
                with mock.patch.object(cmd_util, "run_sh", return_value=_cp(0, stdout="file:///r\n")), \
                     mock.patch.object(cmd_util, "run_cmd", return_value=_cp(0)), \
                     mock.patch.object(cmd_util, "git_cmd", side_effect=fake_git), \
                     mock.patch.object(cmd_util, "run_tee", side_effect=fake_tee), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_release.main([], env=env)
                self.assertEqual(rc, 0)
                doc = json.loads(out.getvalue())
                self.assertEqual(doc["state"], "held")
                self.assertEqual(doc["release"]["numbers"], ["slo: err=0%", "slo: p99=12ms"])
                self.assertEqual(doc["release"]["sbom"], "sha256:s")
            finally:
                os.chdir(old)

    def test_step_review_parse_verdict_and_replay_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                final = Path("final.md")
                final.write_text("CHANGES-REQUESTED\n\n\nMissing check:\n  detail line\n", encoding="utf-8")
                verdict, state, status, reason = step_review.parse_verdict(final, harness="replay")
                self.assertEqual((verdict, state, status), ("changes-requested", "ci-failed", "failure"))
                self.assertEqual(reason, "Missing check:\n  detail line")

                Path("roles/test-reviewer").mkdir(parents=True)
                Path("roles/test-reviewer/prompt.md").write_text("Review\n", encoding="utf-8")
                Path("roles/test-reviewer/role.env").write_text("THINKING=high\nMAX_TURNS=60\nTIMEOUT_S=900\n", encoding="utf-8")

                def fake_sh(script: object, *_args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("clone-url.sh"):
                        return _cp(0, stdout="file:///repo\n")
                    if s.endswith("get.sh"):
                        return _cp(0, stdout="Ticket\n")
                    if s.endswith("run.sh"):
                        out_dir = Path(kwargs["env"]["OUT"])  # type: ignore[index]
                        (out_dir / "final.md").write_text("APPROVED\n\nLooks solid.\n", encoding="utf-8")
                        (out_dir / "run.json").write_text('{"recorded": {"input_sha256": "different"}}', encoding="utf-8")
                        return _cp(0)
                    return _cp(0)

                def fake_py(script: object, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                    s = str(script)
                    if s.endswith("agents.py"):
                        out_arg = args[args.index("--out") + 1]
                        Path(out_arg).write_text("review input", encoding="utf-8")
                        return _cp(0, stdout='{"role": "test-reviewer"}')
                    if s.endswith("spend.py"):
                        return _cp(0, stdout="1500000\n")
                    if s.endswith("tower.py"):
                        self.assertEqual(args[0], "task")
                        self.assertNotIn("--patch", args)
                        return _cp(0, stdout='{"cost_usd": 0.0}')
                    return _cp(0)

                out = io.StringIO()
                env = {
                    "TICKET": "0001", "PR_NUMBER": "1", "BASE_SHA": "b1", "HEAD_SHA": "h1",
                    "CODE_HOST": "mockhost", "TRACKER": "mocktr", "AGENT_HARNESS": "replay",
                }
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", side_effect=fake_py), \
                     mock.patch.object(cmd_util, "git_cmd", return_value=_cp(0, stdout="h1\n")), \
                     mock.patch.object(cmd_util, "run_cmd", return_value=_cp(0)), \
                     contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_review.main([], env=env)
                self.assertEqual(rc, 0)
                res = json.loads(out.getvalue())
                self.assertEqual(res["state"], "ci-passed")
                self.assertFalse(res["review"]["input_matches_recorded"])
            finally:
                os.chdir(old)

    def test_step_rework_escalates_and_relaunches_crew_with_rework_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                Path("policy").mkdir()
                Path("policy/rework.env").write_text("REWORK_MAX_ATTEMPTS=4\n", encoding="utf-8")
                doc_cap = Path("cap.json")
                doc_cap.write_text(
                    json.dumps({
                        "attempt": 4,
                        "pr": {"head_sha": "h1"},
                        "ci": {"verdict": "NO-GO", "line": "NO-GO: test", "details": ["x"]},
                    }),
                    encoding="utf-8",
                )
                out_cap = io.StringIO()
                with contextlib.redirect_stdout(out_cap), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_rework.main([], env={"TICKET": "0001", "DOC_FILE": str(doc_cap), "SPEND_STOP": "cap hit"})
                self.assertEqual(rc, 0)
                cap_json = json.loads(out_cap.getvalue())
                self.assertEqual(cap_json["state"], "needs-human")
                self.assertEqual(cap_json["budget"], {"line": "cap hit"})

                doc_main = Path("main.json")
                doc_main.write_text(
                    json.dumps({
                        "attempt": 1,
                        "pr": {"head_sha": "h1"},
                        "mainline": {"verdict": "NO-GO", "judge": "main", "line": "NO-GO: mainline", "details": []},
                        "ci": {"lines": ["l1"], "seconds": {"test": 1}},
                    }),
                    encoding="utf-8",
                )
                Path(".crew/0001/out").mkdir(parents=True)
                Path(".crew/0001/pr-opened.env").write_text(
                    "PR_NUMBER=5\nPR_URL=http://x/5\nBASE_SHA=b2\nHEAD_SHA=h2\n", encoding="utf-8"
                )
                captured_crew_env: dict[str, str] = {}

                def fake_sh(script: object, *_args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
                    if str(script) == "core/crew.sh":
                        captured_crew_env.update(kwargs["env"])  # type: ignore[arg-type]
                        self.assertIn("starts", Path(kwargs["env"]["REWORK_FILE"]).read_text(encoding="utf-8"))  # type: ignore[index]
                    return _cp(0)

                out_re = io.StringIO()
                with mock.patch.object(cmd_util, "run_sh", side_effect=fake_sh), \
                     mock.patch.object(cmd_util, "run_py", return_value=_cp(0, stdout='{"patch": []}')), \
                     contextlib.redirect_stdout(out_re), contextlib.redirect_stderr(io.StringIO()):
                    rc = step_rework.main([], env={"TICKET": "0001", "DOC_FILE": str(doc_main)})
                self.assertEqual(rc, 0)
                self.assertEqual(captured_crew_env["REWORK_FRESH"], "1")
                self.assertEqual(json.loads(out_re.getvalue())["attempt"], 2)
            finally:
                os.chdir(old)


if __name__ == "__main__":
    unittest.main()
