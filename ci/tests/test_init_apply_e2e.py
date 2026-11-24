"""End-to-end integration tests for zero-copy `init --apply-to` and incremental baseline (ADR 0113)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import init_apply
from core.tests.gitrepo import Repo

ROOT = Path(__file__).resolve().parents[2]
STACKS = ROOT / "stacks"


def _make_python_project(repo: Repo, *, with_container: bool = True) -> None:
    files = {
        "pyproject.toml": (
            '[project]\nname = "legacy-svc"\nversion = "0.1.0"\n'
            'requires-python = ">=3.13"\ndependencies = []\n'
        ),
        "uv.lock": 'version = 1\n\n[[package]]\nname = "legacy-svc"\n\n[[package]]\nname = "pytest"\n',
        "legacy_svc/__init__.py": "def ping():\n    return 'pong'\n",
        "legacy_svc/mod_a.py": "def a(x):\n    return x + 1\n",
        "legacy_svc/mod_b.py": "def b(x):\n    return x + 2\n",
        "legacy_svc/mod_c.py": "def c(x):\n    return x + 3\n",
        "tests/test_legacy.py": "def test_ok():\n    assert True\n",
    }
    if with_container:
        files["Containerfile"] = (
            "FROM python:3.13-slim\n"
            "EXPOSE 8000\n"
            "HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1\n"
        )
    repo.commit(files, "initial legacy repo")
    repo.commit({"legacy_svc/mod_c.py": "def c(x):\n    return x + 30\nline2 = 2\n"}, "recent c")
    repo.commit({"legacy_svc/mod_a.py": "def a(x):\n    return x + 10\nline2 = 2\nline3 = 3\n"}, "recent a")


class InitApplyE2ETest(unittest.TestCase):
    def test_cli_apply_to_branch_commit_incremental_and_ci_workloads(self) -> None:
        repo = Repo(self)
        _make_python_project(repo, with_container=True)

        # Add unrelated dirty and staged changes before running init --apply-to.
        dirty = Path(repo.dir) / "legacy_svc" / "mod_b.py"
        dirty.write_text("def b(x):\n    return x + 99\n", encoding="utf-8")
        repo.git("add", "legacy_svc/mod_b.py")
        (Path(repo.dir) / "notes.tmp").write_text("untracked\n", encoding="utf-8")

        proc = subprocess.run(
            [
                "python3",
                "core/init.py",
                "--apply-to",
                repo.dir,
                "--stacks",
                str(STACKS),
                "--branch",
                "chore/lights-out-init",
                "--commit",
                "--baseline-mode",
                "incremental",
                "--baseline-max-files",
                "2",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.strip(), "STACK=python")
        self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "chore/lights-out-init")

        contract = Path(repo.dir) / ".lights-out"
        self.assertTrue((contract / "workload.env").is_file())
        self.assertTrue((contract / "compose.yaml").is_file())
        self.assertTrue((contract / "runners" / "mutation.sh").is_file())

        env_text = (contract / "workload.env").read_text(encoding="utf-8")
        self.assertIn("# Provenance, measurement:", env_text)
        self.assertIn("incremental: top 2 recently changed files in legacy_svc/", env_text)
        self.assertIn("MUTATION_LINES=legacy_svc/mod_a.py=1-4 legacy_svc/mod_c.py=1-3", env_text)

        # Latest commit must contain ONLY .lights-out/* files.
        committed = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").splitlines()
        self.assertTrue(committed)
        self.assertTrue(all(p.startswith(".lights-out/") for p in committed), committed)

        # Unrelated staged and untracked files remain untouched.
        status_lines = set(repo.git("status", "--porcelain").splitlines())
        self.assertIn("M  legacy_svc/mod_b.py", status_lines)
        self.assertIn("?? notes.tmp", status_lines)

        # Setting baseline preserves compatibility with ci/workloads.sh.
        set_bl = subprocess.run(
            [
                "python3",
                "core/init.py",
                "--set-baseline",
                str(contract / "workload.env"),
                "0.85",
                "measured incrementally over legacy_svc/mod_a.py=1-4 legacy_svc/mod_c.py=1-3",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(set_bl.returncode, 0, set_bl.stdout + set_bl.stderr)

        if shutil.which("docker-compose"):
            wproc = subprocess.run(
                ["sh", "ci/workloads.sh", repo.dir],
                cwd=ROOT,
                env={
                    **os.environ,
                    "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:test"),
                    "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE", "localhost/lights-out-workbench:test"),
                    "WORKLOAD_CONTRACT": str(contract),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(wproc.returncode, 0, wproc.stdout + wproc.stderr)
            self.assertEqual(wproc.stdout.strip(), "GO: workloads")

        # Re-running without --force refuses overwrite cleanly.
        refuse = subprocess.run(
            [
                "python3",
                "core/init.py",
                "--apply-to",
                repo.dir,
                "--stacks",
                str(STACKS),
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(refuse.returncode, 1)
        self.assertIn("workload.env", refuse.stdout)

    def test_incremental_mutation_lines_parse_cleanly_in_stack_runners(self) -> None:
        repo = Repo(self)
        _make_python_project(repo, with_container=False)
        lines, _ = init_apply.select_incremental_scope(repo.dir, "legacy_svc/", max_files=2)
        self.assertEqual(lines, "legacy_svc/mod_a.py=1-4 legacy_svc/mod_c.py=1-3")

        # Verify cosmic-ray line-filter parsing in stacks/python/runners/mutation.sh convention.
        module = "legacy_svc"
        prefix = module + "/"
        groups: dict[str, list[str]] = {}
        for group in lines.split():
            path, _, ranges = group.partition("=")
            self.assertTrue(path and ranges, group)
            key = path[len(prefix) :] if path.startswith(prefix) else path
            groups.setdefault(key, []).extend(r for r in ranges.split(",") if r)
        self.assertEqual(groups, {"mod_a.py": ["1-4"], "mod_c.py": ["1-3"]})

    def test_commit_ticket_artifact_e2e_on_contract_ticket(self) -> None:
        repo = Repo(self)
        _make_python_project(repo, with_container=False)
        (Path(repo.dir) / "legacy_svc" / "__init__.py").write_text("dirty = True\n", encoding="utf-8")

        ticket = Path(repo.dir) / ".lights-out" / "tickets" / "0042.md"
        ticket.parent.mkdir(parents=True, exist_ok=True)
        ticket.write_text("# Ticket 0042\n\n- Origin: local/0042\n\nFix bug.\n", encoding="utf-8")

        sha = init_apply.commit_ticket_artifact(repo.dir, ".lights-out/tickets/0042.md", "ticket: add 0042")
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        committed = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        self.assertEqual(committed, [".lights-out/tickets/0042.md"])
        self.assertIn("M legacy_svc/__init__.py", repo.git("status", "--porcelain"))

    def test_apply_draft_accepts_real_contracts_from_all_six_stacks(self) -> None:
        from core import corpus

        selected = [
            s
            for s in corpus.default_shapes()
            if s.name
            in (
                "python-uv-http",
                "javascript-npm-lib",
                "typescript-npm-lib",
                "go-http",
                "rust-crate-lib",
                "java-maven-lib",
                "stateful-python-service",
            )
        ]
        self.assertEqual(len(selected), 7)

        def on_draft(_shape, repo_dir: Path, contract_dir: Path, _info: dict) -> str | None:
            target = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, target, True)
            r = Repo(self)
            r.commit({"README.md": "# seed\n"}, "seed")
            summary = init_apply.apply_draft(contract_dir, r.dir, ".lights-out", branch="init/apply", commit=True)
            if not summary["applied_files"] or not summary["commit_sha"]:
                return f"empty apply summary for {repo_dir}: {summary}"
            return None

        passed, total, details = corpus.run(
            contract_rel=".contract",
            stacks_dir=STACKS,
            report_path=None,
            verify_light=False,
            on_draft=on_draft,
            shapes=selected,
        )
        self.assertEqual(passed, total, details)

    def test_legacy_commit_sha_flag_remains_compatible_with_init_sh(self) -> None:
        repo = Repo(self)
        _make_python_project(repo, with_container=False)
        with tempfile.TemporaryDirectory() as out_dir:
            proc = subprocess.run(
                [
                    "python3",
                    "core/init.py",
                    "--repo",
                    repo.dir,
                    "--out",
                    out_dir,
                    "--stacks",
                    str(STACKS),
                    "--commit",
                    "deadbeef",
                    "--today",
                    "2026-11-24",
                ],
                cwd=ROOT,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.strip(), "STACK=python")
            env_text = (Path(out_dir) / "workload.env").read_text(encoding="utf-8")
            self.assertIn("on 2026-11-24 at deadbeef", env_text)


if __name__ == "__main__":
    unittest.main()
