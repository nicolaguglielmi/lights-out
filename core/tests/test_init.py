"""Tests for core/init.py, written from docs/adr/0042 §1, §6 and "Resolved while
planning" 4 and 8, never from the implementation.

The runtime matchers answer one question (Resolved 8): does a declared
runtime (`requires-python`, `engines.node`) admit the workbench's version, so
that the draft says `TOOLCHAIN_IMAGE=workbench`? PEP 440 specifiers and npm
ranges are the two languages; the workbench is Python 3.13 and Node 24
(workbench/Containerfile), the versions these tests pass in. A range init does
not understand is never admitted: a guess would put a repository on a
toolchain it did not declare.

The renderer is tested on a hand-built inference, not on a repository: every
key of the draft sits under a "# Provenance," comment (§6), and a committed
contract's plane keys are kept (Resolved 4).

Not asserted: a range that names a minor of the workbench's Node (`~24.1`,
`>=24.5`). The matcher is given a major only, and the workbench's own minor
(24.21.0) decides those; the ADR does not say which the matcher answers.
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

import fnmatch

from core.init import (JAVASCRIPT_TEST_PATHS, PYTHON_TEST_PATHS, Undraftable, infer_python, main as init_main,
                       node_admits, python_admits, render_workload_env, run_driver)
from core.tests.gitrepo import Repo

WORKBENCH_PYTHON = (3, 13)
WORKBENCH_NODE = 24

KEYS = {
    "CANARY_VERSION",
    "MUTATION_BASELINE",
    "WORKLOAD_KILL_SWITCH",
    "WORKLOAD_SAMPLE_PATH",
    "WORKLOAD_LOCKFILES",
    "WORKLOAD_MUTATION_SCOPE",
    "WORKLOAD_TEST_ROOT",
    "WORKLOAD_TEST_PATHS",
    "TOOLCHAIN_IMAGE",
    "TEST_CMD",
    "SAST_CMD",
    "MUTATION_CMD",
}


class PythonAdmitsTest(unittest.TestCase):
    def check(self, spec, expected):
        self.assertIs(
            python_admits(spec, WORKBENCH_PYTHON),
            expected,
            f"requires-python = {spec!r} against Python 3.13",
        )

    def test_admitted(self):
        for spec in (
            "==3.13.*",
            ">=3.11",
            ">=3.11,<3.14",
            ">=3.13",
            "<=3.13",
            "~=3.12",
            "~=3.13",
            "~=3.11",
            "!=3.12.*",
            ">3.12",
            "",
        ):
            with self.subTest(spec=spec):
                self.check(spec, True)

    def test_not_admitted(self):
        for spec in (
            "==3.11.*",
            "==3.12.*",
            "<3.13",
            ">=3.11,<3.13",
            ">=3.14",
            ">3.13",
            "!=3.13.*",
            "~=3.11.0",
        ):
            with self.subTest(spec=spec):
                self.check(spec, False)

    def test_spaces_around_clauses_do_not_change_the_answer(self):
        self.check(">=3.11, <3.13", False)
        self.check(">=3.11, <3.14", True)

    def test_the_answer_depends_on_the_version_asked(self):
        self.assertTrue(python_admits("==3.12.*", (3, 12)))
        self.assertFalse(python_admits("==3.12.*", (3, 13)))
        self.assertTrue(python_admits(">=3.11,<3.13", (3, 12)))
        self.assertFalse(python_admits(">=3.11,<3.13", (3, 13)))


class NodeAdmitsTest(unittest.TestCase):
    def check(self, range_text, expected):
        self.assertIs(
            node_admits(range_text, WORKBENCH_NODE),
            expected,
            f"engines.node = {range_text!r} against Node 24",
        )

    def test_admitted(self):
        for range_text in (
            ">=22",
            ">=20",
            ">=18 <25",
            "^24",
            "24.x",
            "24",
            "*",
            "",
            "^20 || ^24",
            "22 - 24",
            ">20",
            "<=24",
        ):
            with self.subTest(range_text=range_text):
                self.check(range_text, True)

    def test_not_admitted(self):
        for range_text in (
            "^20",
            "^22",
            "22",
            "18 - 22",
            ">=25",
            "<24",
            ">=18 <24",
            "^18 || ^20",
        ):
            with self.subTest(range_text=range_text):
                self.check(range_text, False)

    def test_an_unparseable_range_is_not_admitted(self):
        self.check("latest", False)

    def test_the_answer_depends_on_the_major_asked(self):
        self.assertTrue(node_admits("^20", 20))
        self.assertFalse(node_admits("^20", 24))
        self.assertTrue(node_admits("18 - 22", 22))
        self.assertFalse(node_admits("18 - 22", 24))


def inference():
    """What an inference of a repository holds, for the keys the draft reads."""
    return {
        "lockfiles": "uv.lock",
        "lockfiles_evidence": "uv.lock at the root",
        "scope": "demo/",
        "scope_evidence": "demo/__init__.py exists",
        "test_root": "",
        "test_root_evidence": "nothing to strip",
        "test_paths": "tests/test_*.py",
        "test_paths_evidence": "pytest's default discovery",
        "toolchain": "workbench",
        "toolchain_evidence": "requires-python admits the workbench",
        "test_cmd": "uv run --locked pytest tests -q",
        "test_cmd_evidence": "tests/ exists",
        "sast_cmd": "sh .lights-out/runners/sast.sh demo",
        "sast_cmd_evidence": "bandit over the package",
        "mutation_cmd": 'sh .lights-out/runners/mutation.sh demo "uv run --locked pytest tests -q -x"',
        "mutation_cmd_evidence": "cosmic-ray over the package",
    }


def parse(text):
    """(key -> value, key -> the comment block directly above it)."""
    values = {}
    blocks = {}
    block = []
    for line in text.split("\n"):
        if line.startswith("#"):
            block.append(line)
        elif not line.strip():
            block = []
        else:
            key, sep, value = line.partition("=")
            assert sep, f"not KEY=VALUE: {line!r}"
            assert key not in values, f"{key} appears twice"
            values[key] = value
            blocks[key] = block
            block = []
    return values, blocks


class TestPathsTest(unittest.TestCase):
    """docs/adr/0053 §1: the drafted WORKLOAD_TEST_PATHS is the stack's default
    discovery, `*` crossing `/`."""

    def matches(self, spec, path):
        return any(fnmatch.fnmatchcase(path, g) for g in spec.split(":") if g)

    def test_python_names_what_pytest_collects_and_its_fixtures(self):
        for path in ("test_app.py", "tests/test_app.py", "pkg/tests/test_x.py", "tests/app_test.py",
                     "conftest.py", "tests/conftest.py"):
            self.assertTrue(self.matches(PYTHON_TEST_PATHS, path), path)

    def test_python_does_not_name_code_that_only_contains_test(self):
        for path in ("demo/latest_x.py", "demo/app.py", "demo/contest.py", "tests/mutation/run.sh"):
            self.assertFalse(self.matches(PYTHON_TEST_PATHS, path), path)

    def test_javascript_names_what_node_test_runs(self):
        for path in ("test/calc.test.js", "test/helpers.js", "src/a.test.mjs", "src/a-test.js",
                     "src/a_test.cjs", "pkg/test/x.js"):
            self.assertTrue(self.matches(JAVASCRIPT_TEST_PATHS, path), path)

    def test_javascript_does_not_name_the_sources(self):
        for path in ("src/calc.js", "lib/index.mjs", "src/latest.js"):
            self.assertFalse(self.matches(JAVASCRIPT_TEST_PATHS, path), path)


class RenderWorkloadEnvTest(unittest.TestCase):
    def render(self, committed):
        return render_workload_env(inference(), committed, "2026-11-03", "abc1234")

    def test_exactly_the_twelve_keys_once_each(self):
        for committed in ({}, {"CANARY_VERSION": "1"}):
            with self.subTest(committed=committed):
                values, _ = parse(self.render(committed))
                self.assertEqual(set(values), KEYS)

    def test_every_key_has_a_provenance_line_directly_above(self):
        for committed in ({}, {"CANARY_VERSION": "1"}):
            with self.subTest(committed=committed):
                _, blocks = parse(self.render(committed))
                for key in KEYS:
                    self.assertTrue(
                        any(line.startswith("# Provenance,") for line in blocks[key]),
                        f"{key} has no '# Provenance,' line in the comment block above it: {blocks[key]}",
                    )

    def test_no_committed_contract_leaves_the_plane_keys_and_the_baseline_empty(self):
        values, _ = parse(self.render({}))
        for key in ("CANARY_VERSION", "WORKLOAD_KILL_SWITCH", "WORKLOAD_SAMPLE_PATH", "MUTATION_BASELINE"):
            self.assertEqual(values[key], "", key)

    def test_the_inferred_values_are_the_inferences(self):
        values, _ = parse(self.render({}))
        d = inference()
        self.assertEqual(values["WORKLOAD_LOCKFILES"], d["lockfiles"])
        self.assertEqual(values["WORKLOAD_MUTATION_SCOPE"], d["scope"])
        self.assertEqual(values["WORKLOAD_TEST_ROOT"], d["test_root"])
        self.assertEqual(values["WORKLOAD_TEST_PATHS"], d["test_paths"])
        self.assertEqual(values["TOOLCHAIN_IMAGE"], d["toolchain"])
        self.assertEqual(values["TEST_CMD"], d["test_cmd"])
        self.assertEqual(values["SAST_CMD"], d["sast_cmd"])
        self.assertEqual(values["MUTATION_CMD"], d["mutation_cmd"])

    def test_the_evidence_is_in_the_comment_above_its_key(self):
        _, blocks = parse(self.render({}))
        d = inference()
        for key, evidence in (
            ("WORKLOAD_LOCKFILES", d["lockfiles_evidence"]),
            ("WORKLOAD_MUTATION_SCOPE", d["scope_evidence"]),
            ("TOOLCHAIN_IMAGE", d["toolchain_evidence"]),
            ("TEST_CMD", d["test_cmd_evidence"]),
        ):
            self.assertIn(evidence, "\n".join(blocks[key]), key)

    def test_a_committed_plane_key_is_kept_and_says_so(self):
        values, blocks = parse(self.render({"CANARY_VERSION": "1"}))
        self.assertEqual(values["CANARY_VERSION"], "1")
        self.assertTrue(
            blocks["CANARY_VERSION"][-1].startswith("# Provenance, kept"),
            f"the line above CANARY_VERSION: {blocks['CANARY_VERSION']}",
        )

    def test_a_committed_contract_does_not_carry_its_other_keys_into_the_draft(self):
        committed = {
            "CANARY_VERSION": "1",
            "MUTATION_BASELINE": "0.9",
            "WORKLOAD_LOCKFILES": "old.lock",
            "TEST_CMD": "echo old",
        }
        values, _ = parse(self.render(committed))
        self.assertEqual(values["MUTATION_BASELINE"], "")
        self.assertEqual(values["WORKLOAD_LOCKFILES"], "uv.lock")
        self.assertEqual(values["TEST_CMD"], inference()["test_cmd"])

    def test_the_other_plane_keys_are_kept_too(self):
        committed = {"CANARY_VERSION": "1", "WORKLOAD_KILL_SWITCH": "ks", "WORKLOAD_SAMPLE_PATH": "/p"}
        values, _ = parse(self.render(committed))
        self.assertEqual(values["WORKLOAD_KILL_SWITCH"], "ks")
        self.assertEqual(values["WORKLOAD_SAMPLE_PATH"], "/p")

    def test_no_value_carries_a_dollar_or_an_inline_comment(self):
        # make's include would expand the one and cut at the other (Resolved 2).
        text = self.render({"CANARY_VERSION": "1"})
        for line in text.split("\n"):
            if line and not line.startswith("#"):
                self.assertNotIn("$", line)
                self.assertNotIn("#", line)

    def test_the_file_ends_with_one_newline(self):
        text = self.render({})
        self.assertTrue(text.endswith("\n"))
        self.assertFalse(text.endswith("\n\n"))


class InferPythonTestRunnerTest(unittest.TestCase):
    def _make_repo(self, lock_body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = tmp.name
        os.makedirs(os.path.join(root, "demo"))
        os.makedirs(os.path.join(root, "tests"))
        with open(os.path.join(root, "demo", "__init__.py"), "w", encoding="utf-8") as fh:
            fh.write("")
        with open(os.path.join(root, "pyproject.toml"), "w", encoding="utf-8") as fh:
            fh.write('[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.13"\ndependencies = []\n')
        with open(os.path.join(root, "uv.lock"), "w", encoding="utf-8") as fh:
            fh.write(lock_body)
        return root

    def test_uv_lock_with_pytest_uses_pytest(self):
        repo = self._make_repo(
            'version = 1\n\n[[package]]\nname = "demo"\n\n[[package]]\nname = "pytest"\n'
        )
        d = infer_python(repo)
        self.assertEqual(d["test_cmd"], "uv run --locked pytest tests -q -p no:cacheprovider")

    def test_uv_lock_without_pytest_uses_unittest_discovery(self):
        repo = self._make_repo('version = 1\n\n[[package]]\nname = "demo"\n')
        d = infer_python(repo)
        self.assertEqual(d["test_cmd"], "uv run --locked python3 -m unittest discover -s tests -q")
        self.assertIn("python3 -m unittest discover -s tests -q -f", d["mutation_cmd"])


class InitApplyAndIncrementalCliTest(unittest.TestCase):
    def _make_git_python_repo(self):
        repo = Repo(self)
        repo.commit(
            {
                "pyproject.toml": (
                    '[project]\nname = "demo"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.13"\ndependencies = []\n'
                ),
                "uv.lock": 'version = 1\n\n[[package]]\nname = "demo"\n\n[[package]]\nname = "pytest"\n',
                "demo/__init__.py": "def one():\n    return 1\n",
                "demo/a.py": "def a():\n    return 2\n",
                "demo/b.py": "def b():\n    return 3\n",
                "tests/test_demo.py": "def test_ok():\n    assert True\n",
            },
            "initial",
        )
        repo.commit({"demo/b.py": "def b():\n    return 30\n    # extra\n"}, "touch b")
        return repo

    def _run_main(self, argv):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = init_main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_apply_to_without_out_or_repo_defaults_and_commits_on_branch(self):
        repo = self._make_git_python_repo()
        stacks = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "stacks")
        rc, out, err = self._run_main(
            [
                "--apply-to",
                repo.dir,
                "--stacks",
                stacks,
                "--branch",
                "init/contract",
                "--commit",
                "--baseline-mode",
                "incremental",
                "--baseline-max-files",
                "2",
            ]
        )
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(out.strip(), "STACK=python")
        self.assertIn("incremental:", err)
        self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "init/contract")

        env_path = os.path.join(repo.dir, ".lights-out", "workload.env")
        with open(env_path, encoding="utf-8") as fh:
            env_text = fh.read()
        values, blocks = parse(env_text)
        self.assertEqual(set(values), KEYS)
        self.assertEqual(values["MUTATION_BASELINE"], "")
        block_text = "\n".join(blocks["MUTATION_BASELINE"])
        self.assertIn("# Provenance, measurement:", block_text)
        self.assertIn("incremental: top 2 recently changed files in demo/", block_text)
        self.assertIn("MUTATION_LINES=demo/b.py=1-3", block_text)

    def test_apply_to_refuses_overwrite_without_force_and_allows_with_force(self):
        repo = self._make_git_python_repo()
        stacks = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "stacks")
        rc1, out1, err1 = self._run_main(["--apply-to", repo.dir, "--stacks", stacks])
        self.assertEqual(rc1, 0, out1 + err1)

        rc2, out2, _ = self._run_main(["--apply-to", repo.dir, "--stacks", stacks])
        self.assertEqual(rc2, 1)
        self.assertIn("workload.env", out2)

        rc3, out3, err3 = self._run_main(["--apply-to", repo.dir, "--stacks", stacks, "--force", "--commit"])
        self.assertEqual(rc3, 0, out3 + err3)
        self.assertEqual(out3.strip(), "STACK=python")

    def test_baseline_max_files_validation_and_apply_flag_guards(self):
        repo = self._make_git_python_repo()
        stacks = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "stacks")
        with tempfile.TemporaryDirectory() as out_dir:
            rc, out, _ = self._run_main(
                [
                    "--repo",
                    repo.dir,
                    "--out",
                    out_dir,
                    "--stacks",
                    stacks,
                    "--baseline-mode",
                    "incremental",
                    "--baseline-max-files",
                    "0",
                ]
            )
            self.assertEqual(rc, 1)
            self.assertTrue(out.strip())

        for flag in (["--branch", "x"], ["--commit"], ["--force"]):
            with self.subTest(flag=flag):
                proc = subprocess.run(
                    ["python3", "core/init.py", "--repo", repo.dir, "--out", repo.dir + "/.tmp", *flag],
                    cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 2)

    def test_run_driver_plain_vs_apply_incremental_and_commit(self):
        repo = self._make_git_python_repo()
        # Leave unrelated dirty worktree file to prove scoped commit ignores it
        with open(os.path.join(repo.dir, "demo", "a.py"), "w", encoding="utf-8") as fh:
            fh.write("def a():\n    return 99\n")

        captured_env = []
        real_run = subprocess.run

        def fake_run(argv, *args, **kwargs):
            if argv[:2] == ["git", "-C"]:
                return real_run(argv, *args, **kwargs)
            if len(argv) >= 5 and argv[-2:] == ["core.gates.toolchain", "preflight"]:
                return SimpleNamespace(returncode=0, stdout="workbench\n", stderr="")
            if argv[:2] == ["sh", "core/history-findings.sh"]:
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            if argv[:2] == ["sh", "core/mutation-baseline.sh"]:
                captured_env.append(dict(kwargs.get("env") or {}))
                return SimpleNamespace(returncode=0, stdout='{"total": 4, "killed": 3, "score": 0.75}\n', stderr="")
            if argv[:2] == ["sh", "ci/workloads.sh"]:
                return SimpleNamespace(returncode=0, stdout="GO: workloads\n", stderr="")
            raise AssertionError(f"unexpected subprocess.run: {argv}")

        env = {
            "WORKLOAD": repo.dir,
            "WORKLOAD_CONTRACT": os.path.join(repo.dir, ".lights-out"),
            "WORKLOAD_ATTACHED": "1",
        }
        with mock.patch("core.init.subprocess.run", side_effect=fake_run), redirect_stderr(io.StringIO()):
            with self.assertRaises(Undraftable):
                run_driver(["--branch", "lo/x"], env)

            summary = run_driver(
                ["--apply", "--branch", "lo/init", "--commit", "--baseline-mode", "incremental", "--baseline-max-files", "1"],
                env,
            )

        self.assertIn("applied to", summary)
        self.assertIn("branch=lo/init", summary)
        self.assertIn("baseline 0.75 (3/4 mutants killed", summary)
        self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "lo/init")
        self.assertEqual(len(captured_env), 1)
        self.assertEqual(captured_env[0].get("MUTATION_LINES"), "demo/b.py=1-3")

        wl_text = open(os.path.join(repo.dir, ".lights-out", "workload.env"), encoding="utf-8").read()
        self.assertIn("MUTATION_BASELINE=0.75", wl_text)
        self.assertIn("MUTATION_LINES=demo/b.py=1-3", wl_text)
        committed = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
        self.assertTrue(committed and all(p.startswith(".lights-out/") for p in committed), committed)
        self.assertIn("M demo/a.py", repo.git("status", "--porcelain"))


if __name__ == "__main__":
    unittest.main()
