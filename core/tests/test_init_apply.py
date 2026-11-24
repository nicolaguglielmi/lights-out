"""Spec-derived tests for core/init_apply.py (docs/adr/0113), written before implementation.

Strictly app-free, vendor-free, and model-free: passes dynamic contract_rel
parameters and uses generic synthetic package layouts only.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import init_apply
from core.tests.gitrepo import ENV as GIT_ENV
from core.tests.gitrepo import Repo


def _write(path: Path, text: str, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _make_sample_draft(draft_dir: Path) -> None:
    _write(
        draft_dir / "workload.env",
        "# Provenance, measurement: not yet measured\n"
        "MUTATION_BASELINE=\n"
        "WORKLOAD_KILL_SWITCH=\n"
        "TOOLCHAIN_IMAGE=workbench\n",
    )
    _write(draft_dir / "worker.md", "# Brief\n\n## Scope\n\npkg/\n\n## Tests\n\npytest -q\n")
    _write(draft_dir / "policy.env", "# No overrides\n")
    _write(draft_dir / "runners" / "sast.sh", "#!/bin/sh\nexit 0\n", executable=True)
    _write(
        draft_dir / "smoke.sh",
        '#!/bin/sh\n# Optional auth header (e.g. SMOKE_AUTH_HEADER="Authorization: Bearer <token>"), never embedded.\n'
        'exit 0\n',
        executable=True,
    )


class ApplyDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.draft = self.root / "draft"
        _make_sample_draft(self.draft)

    def test_copies_draft_atomically_preserving_modes_without_commit(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "# demo\n"}, "init")

        summary = init_apply.apply_draft(self.draft, repo.dir, ".contract")
        self.assertEqual(
            summary["applied_files"],
            [
                ".contract/policy.env",
                ".contract/runners/sast.sh",
                ".contract/smoke.sh",
                ".contract/worker.md",
                ".contract/workload.env",
            ],
        )
        self.assertEqual(summary["branch"], "main")
        self.assertIsNone(summary["commit_sha"])
        self.assertEqual(summary["overwritten"], [])

        sast = Path(repo.dir) / ".contract" / "runners" / "sast.sh"
        self.assertTrue(sast.is_file())
        self.assertTrue(sast.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(repo.git("status", "--porcelain", "--", ".contract").splitlines().__len__(), 1)

    def test_works_on_non_git_dir_when_branch_and_commit_false(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        summary = init_apply.apply_draft(self.draft, plain, ".contract")
        self.assertIsNone(summary["branch"])
        self.assertIsNone(summary["commit_sha"])
        self.assertEqual(summary["overwritten"], [])
        self.assertTrue((plain / ".contract" / "workload.env").is_file())

    def test_refuses_non_git_dir_when_branch_or_commit_requested(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        with self.assertRaises(init_apply.ApplyError):
            init_apply.apply_draft(self.draft, plain, ".contract", branch="setup")
        with self.assertRaises(init_apply.ApplyError):
            init_apply.apply_draft(self.draft, plain, ".contract", commit=True)
        self.assertFalse((plain / ".contract").exists())

    def test_refuses_existing_or_tracked_files_unless_force_without_partial_changes(self) -> None:
        repo = Repo(self)
        repo.commit({".contract/workload.env": "existing\n", ".contract/tickets/0001.md": "# t1\n"}, "existing contract")

        with self.assertRaises(init_apply.ApplyError) as ctx:
            init_apply.apply_draft(self.draft, repo.dir, ".contract", branch="setup/new", commit=True)
        self.assertIn("workload.env", str(ctx.exception))
        # Refusal must leave current branch and files untouched.
        self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
        self.assertFalse((Path(repo.dir) / ".contract" / "worker.md").exists())

        # Untracked existing file on disk is also refused without force.
        _write(Path(repo.dir) / ".contract" / "policy.env", "# untracked\n")
        with self.assertRaises(init_apply.ApplyError):
            init_apply.apply_draft(self.draft, repo.dir, ".contract")

        # With force=True, overlapping files are overwritten and unrelated files preserved.
        summary = init_apply.apply_draft(
            self.draft, repo.dir, ".contract", branch="setup/new", commit=True, force=True
        )
        self.assertEqual(summary["branch"], "setup/new")
        self.assertIsNotNone(summary["commit_sha"])
        self.assertEqual(summary["overwritten"], [".contract/policy.env", ".contract/workload.env"])
        self.assertTrue((Path(repo.dir) / ".contract" / "tickets" / "0001.md").is_file())

    def test_refuses_modified_tracked_files_without_force(self) -> None:
        repo = Repo(self)
        repo.commit({".contract/workload.env": "v1\n"}, "v1")
        _write(Path(repo.dir) / ".contract" / "workload.env", "v1-dirty\n")

        with self.assertRaises(init_apply.ApplyError):
            init_apply.apply_draft(self.draft, repo.dir, ".contract")
        self.assertEqual((Path(repo.dir) / ".contract" / "workload.env").read_text(encoding="utf-8"), "v1-dirty\n")

    def test_refuses_secrets_before_touching_target_repo(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "ok\n"}, "init")

        bad_cases = [
            (".env", "FOO=bar\n"),
            (".env.local", "FOO=bar\n"),
            ("signing.pem", "not-empty\n"),
            ("id_rsa", "key-bytes\n"),
            ("notes.txt", "".join(["-----BEGIN RSA ", "PRIVATE KEY-----\nMIIE...\n"])),
            ("token.txt", "".join(["token_val = ", "ghp_", "A" * 24, "\n"])),
            ("aws.txt", "".join(["access = ", "AK", "IA", "01234567", "89ABCDEF\n"])),
            ("auth.env", "".join(["DB_PASS", "WORD=supersecretvalue\n"])),
            ("cfg.env", "".join(["SERVICE_", 'TOKEN="secret-value-12345"\n'])),
        ]
        for rel, content in bad_cases:
            with self.subTest(rel=rel):
                bad_draft = self.root / f"bad_{rel.replace('/', '_').replace('.', '_')}"
                shutil.copytree(self.draft, bad_draft)
                _write(bad_draft / rel, content)
                with self.assertRaises(init_apply.ApplyError):
                    init_apply.apply_draft(bad_draft, repo.dir, ".contract", branch="setup/bad", commit=True)
                self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "main")
                self.assertFalse((Path(repo.dir) / ".contract").exists())

    def test_scoped_commit_leaves_unrelated_dirty_and_staged_files_untouched(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "v1\n", "tracked.txt": "orig\n"}, "init")

        # Unrelated pre-staged change + unrelated unstaged modification + unrelated untracked file.
        _write(Path(repo.dir) / "README.md", "v2-staged\n")
        repo.git("add", "README.md")
        _write(Path(repo.dir) / "tracked.txt", "dirty-worktree\n")
        _write(Path(repo.dir) / "scratch.tmp", "untracked\n")

        summary = init_apply.apply_draft(
            self.draft, repo.dir, ".contract", branch="setup/contract", commit=True
        )
        self.assertEqual(summary["branch"], "setup/contract")
        sha = summary["commit_sha"]
        self.assertRegex(sha, r"^[0-9a-f]{40}$")

        committed = set(repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines())
        self.assertEqual(committed, set(summary["applied_files"]))
        self.assertNotIn("README.md", committed)
        self.assertNotIn("tracked.txt", committed)
        self.assertNotIn("scratch.tmp", committed)

        status_lines = set(repo.git("status", "--porcelain").splitlines())
        self.assertIn("M  README.md", status_lines)
        self.assertIn(" M tracked.txt", status_lines)
        self.assertIn("?? scratch.tmp", status_lines)

    def test_switches_to_existing_branch_and_rejects_invalid_paths_or_branches(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "v1\n"}, "init")
        repo.git("branch", "existing-setup")

        summary = init_apply.apply_draft(self.draft, repo.dir, ".contract", branch="existing-setup", commit=True)
        self.assertEqual(summary["branch"], "existing-setup")
        self.assertEqual(repo.git("rev-parse", "--abbrev-ref", "HEAD"), "existing-setup")

        for bad_rel in ("", ".", "/tmp/abs", "../escape", "a/../..", "bad$dir", "bad#dir"):
            with self.subTest(bad_rel=bad_rel), self.assertRaises(ValueError):
                init_apply.apply_draft(self.draft, repo.dir, bad_rel)

        for bad_branch in ("", "-bad", "a..b", "bad branch"):
            with self.subTest(bad_branch=bad_branch), self.assertRaises(ValueError):
                init_apply.apply_draft(self.draft, repo.dir, ".contract", branch=bad_branch, force=True)

    def test_rejects_symlink_escape_and_handles_unborn_head(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        repo_dir = self.root / "unborn"
        repo_dir.mkdir()
        subprocess.run(["git", "-C", str(repo_dir), "init", "-q", "-b", "main"], env=GIT_ENV, check=True)
        os.symlink(outside, repo_dir / ".link")
        with self.assertRaises(ValueError):
            init_apply.apply_draft(self.draft, repo_dir, ".link/sub")

        summary = init_apply.apply_draft(self.draft, repo_dir, ".contract", commit=True)
        self.assertRegex(summary["commit_sha"], r"^[0-9a-f]{40}$")
        self.assertEqual(summary["branch"], "main")


class SelectIncrementalScopeTest(unittest.TestCase):
    def test_validates_max_files_ge_one_and_scope(self) -> None:
        repo = Repo(self)
        repo.commit({"pkg/a.py": "def a():\n    return 1\n"}, "init")
        for bad in (0, -1, -10, False, True, "2"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                init_apply.select_incremental_scope(repo.dir, "pkg/", max_files=bad)
        for bad_scope in ("/abs", "../outside", "bad$scope"):
            with self.subTest(bad_scope=bad_scope), self.assertRaises(ValueError):
                init_apply.select_incremental_scope(repo.dir, bad_scope, max_files=5)

    def test_selects_most_recently_touched_source_files_via_git_log(self) -> None:
        repo = Repo(self)
        repo.commit(
            {
                "pkg/alpha.py": "x = 1\ny = 2\n",
                "pkg/beta.py": "x = 1\ny = 2\nz = 3\n",
                "pkg/gamma.py": "x = 1\n",
                "pkg/delta.py": "x = 1\ny = 2\nz = 3\nw = 4\n",
                "tests/test_pkg.py": "def test_ok():\n    assert True\n",
                "pkg/tests/test_inner.py": "def test_inner():\n    assert True\n",
                "pkg/conftest.py": "FIXTURE = 1\n",
                "uv.lock": "version = 1\n",
            },
            "initial",
        )
        repo.commit({"pkg/beta.py": "x = 10\ny = 20\nz = 30\n"}, "touch beta")
        repo.commit({"pkg/delta.py": "x = 1\ny = 2\nz = 3\nw = 4\nv = 5\n"}, "touch delta")
        repo.commit({"tests/test_pkg.py": "def test_ok():\n    assert 1 == 1\n"}, "touch test only")

        lines, prov = init_apply.select_incremental_scope(repo.dir, "pkg/", max_files=2)
        self.assertEqual(lines, "pkg/delta.py=1-5 pkg/beta.py=1-3")
        self.assertTrue(prov.startswith("incremental: top 2 recently changed files in pkg/"), prov)
        self.assertIn("pkg/delta.py", prov)
        self.assertIn("pkg/beta.py", prov)
        self.assertNotIn("test_", lines)
        self.assertNotIn("conftest.py", lines)
        self.assertNotIn("uv.lock", lines)

    def test_falls_back_to_lexical_source_paths_and_handles_multiple_languages(self) -> None:
        repo = Repo(self)
        # Single initial commit + uncommitted source file + non-source/test exclusions.
        repo.commit(
            {
                "src/z.ts": "export const z = 1;\n",
                "src/a.ts": "export const a = 1;\nexport const b = 2;\n",
                "src/types.d.ts": "export type T = string;\n",
                "src/a.test.ts": "test('a', () => {});\n",
                "src/b.spec.ts": "test('b', () => {});\n",
                "src/nested/m.ts": "export const m = 3;\n",
                "node_modules/dep/index.js": "module.exports = {};\n",
                "package-lock.json": "{}\n",
            },
            "seed",
        )
        # Deleted file in git history must not be emitted.
        repo.commit({"src/deleted.ts": "export const d = 1;\n"}, "add deleted")
        repo.commit({"src/deleted.ts": None}, "remove deleted")

        lines, prov = init_apply.select_incremental_scope(repo.dir, "src/", max_files=5)
        self.assertEqual(lines, "src/a.ts=1-2 src/nested/m.ts=1-1 src/z.ts=1-1")
        self.assertTrue(prov.startswith("incremental: top 3 recently changed files in src/"), prov)

        # Plain non-git directory falls back cleanly to lexical sorted order.
        with tempfile.TemporaryDirectory() as plain_dir:
            plain = Path(plain_dir) / "plain_go"
            _write(plain / "main.go", "package main\nfunc main() {}\n")
            _write(plain / "helper.go", "package main\n")
            _write(plain / "main_test.go", "package main\n")
            _write(plain / "vendor" / "v.go", "package v\n")
            go_lines, go_prov = init_apply.select_incremental_scope(plain, ".", max_files=1)
            self.assertEqual(go_lines, "helper.go=1-1")
            self.assertTrue(go_prov.startswith("incremental: top 1 recently changed files in ."), go_prov)

    def test_empty_scope_returns_empty_spec_with_provenance(self) -> None:
        repo = Repo(self)
        repo.commit({"docs/readme.md": "# docs\n"}, "docs")
        lines, prov = init_apply.select_incremental_scope(repo.dir, "src/", max_files=5)
        self.assertEqual(lines, "")
        self.assertIn("0", prov)


class CommitTicketArtifactTest(unittest.TestCase):
    def test_commits_only_target_artifact_leaving_dirty_and_staged_files_untouched(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "v1\n", "other.py": "x = 1\n"}, "init")

        _write(Path(repo.dir) / "README.md", "v2-staged\n")
        repo.git("add", "README.md")
        _write(Path(repo.dir) / "other.py", "x = 2\n")
        _write(Path(repo.dir) / "untracked.txt", "keep-me\n")
        _write(Path(repo.dir) / ".contract" / "tickets" / "0001.md", "# Ticket 0001\n\nBody.\n")

        sha = init_apply.commit_ticket_artifact(repo.dir, ".contract/tickets/0001.md", "ticket: record 0001")
        self.assertRegex(sha, r"^[0-9a-f]{40}$")
        committed = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
        self.assertEqual(committed, [".contract/tickets/0001.md"])

        status_lines = set(repo.git("status", "--porcelain").splitlines())
        self.assertIn("M  README.md", status_lines)
        self.assertIn(" M other.py", status_lines)
        self.assertIn("?? untracked.txt", status_lines)

    def test_rejects_outside_paths_missing_files_empty_message_and_secrets(self) -> None:
        repo = Repo(self)
        repo.commit({"README.md": "v1\n"}, "init")
        outside = Path(repo.dir).parent / "outside.md"
        _write(outside, "# outside\n")
        os.symlink(outside, Path(repo.dir) / "sym.md")

        for bad_path in ("/etc/passwd", str(outside), "../outside.md", "sym.md", "missing.md"):
            with self.subTest(bad_path=bad_path), self.assertRaises((ValueError, init_apply.ApplyError)):
                init_apply.commit_ticket_artifact(repo.dir, bad_path, "msg")

        _write(Path(repo.dir) / ".contract" / "tickets" / "0002.md", "# T2\n")
        with self.assertRaises(ValueError):
            init_apply.commit_ticket_artifact(repo.dir, ".contract/tickets/0002.md", "   ")

        _write(
            Path(repo.dir) / ".contract" / "tickets" / "0003.md",
            "".join(["-----BEGIN ", "PRIVATE KEY-----\nsecret\n"]),
        )
        with self.assertRaises(init_apply.ApplyError):
            init_apply.commit_ticket_artifact(repo.dir, ".contract/tickets/0003.md", "bad secret")
        self.assertIn("?? .contract/tickets/0003.md", repo.git("status", "--porcelain", "-uall"))


if __name__ == "__main__":
    unittest.main()
