"""Spec-derived unit tests for core/genesis.py (docs/adr/0118).

Strictly stdlib-only, vendor-free, app-free, and model-free.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import adr_gov, genesis, init, init_apply, spec
from core.tests.gitrepo import ENV as GIT_ENV
from core.tests.gitrepo import Repo

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_DIR = "." + "lights" + "-out"


def _clean_git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        env=GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _parse_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k, sep, v = line.partition("=")
        if sep:
            values[k] = v
    return values


def _perimeter_globs(ticket_text: str) -> list[str]:
    globs: list[str] = []
    inside = False
    for line in ticket_text.splitlines():
        if line.startswith("## Perimeter"):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and line.startswith("- "):
            globs.append(line[2:].strip().strip("`"))
    return globs


class SafetyAndValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-unit-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_refuses_empty_brief_bad_stack_port_tickets_and_name(self) -> None:
        target = self.tmp / "new-svc"
        for bad_brief in ("", "   \n\t "):
            with self.subTest(bad_brief=bad_brief), self.assertRaises((genesis.GenesisError, ValueError)):
                genesis.create_greenfield_app(target, bad_brief)

        for bad_stack in ("", "ruby", "elixir", "PY"):
            with self.subTest(bad_stack=bad_stack), self.assertRaises((genesis.GenesisError, ValueError)):
                genesis.create_greenfield_app(target, "Build an orders ledger service", stack=bad_stack)

        for bad_port in (0, 80, 1023, 65536, -1, True, False, "8000"):
            with self.subTest(bad_port=bad_port), self.assertRaises((genesis.GenesisError, ValueError)):
                genesis.create_greenfield_app(target, "Build an orders ledger service", port=bad_port)  # type: ignore[arg-type]

        for bad_count in (0, -2, 11, True, False):
            with self.subTest(bad_count=bad_count), self.assertRaises((genesis.GenesisError, ValueError)):
                genesis.create_greenfield_app(target, "Build an orders ledger service", tickets_count=bad_count)

        for bad_name in ("", "   ", "../escape", "bad$name", "bad#name", "123numeric", "has space"):
            with self.subTest(bad_name=bad_name), self.assertRaises((genesis.GenesisError, ValueError)):
                genesis.create_greenfield_app(target, "Build an orders ledger service", name=bad_name)

        self.assertFalse(target.exists())

    def test_accepts_boundary_ports_and_creates_new_dir_with_main_branch(self) -> None:
        for port in (1024, 65535):
            with self.subTest(port=port):
                target = self.tmp / f"port-{port}"
                res = genesis.create_greenfield_app(
                    target,
                    "Build a status tracker service",
                    port=port,
                    commit=False,
                )
                self.assertEqual(res["port"], port)
                self.assertEqual(_clean_git(target, "symbolic-ref", "--short", "HEAD"), "main")

    def test_blank_non_git_dir_initializes_main_and_non_empty_non_git_refuses(self) -> None:
        blank = self.tmp / "blank"
        blank.mkdir()
        res = genesis.create_greenfield_app(blank, "Build an inventory ledger service")
        self.assertEqual(res["stack"], "python")
        self.assertEqual(_clean_git(blank, "rev-parse", "--abbrev-ref", "HEAD"), "main")

        non_git = self.tmp / "non-git-with-file"
        non_git.mkdir()
        (non_git / "README.md").write_text("# existing\n", encoding="utf-8")
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(non_git, "Build an inventory ledger service")
        self.assertFalse((non_git / ".git").exists())
        self.assertFalse((non_git / CONTRACT_DIR).exists())

    def test_allow_existing_empty_false_refuses_existing_directory(self) -> None:
        blank = self.tmp / "existing-blank"
        blank.mkdir()
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(
                blank,
                "Build a catalog service",
                allow_existing_empty=False,
            )
        self.assertEqual(os.listdir(blank), [])

    def test_refuses_subdir_inside_outer_git_repo_without_own_git(self) -> None:
        outer = Repo(self)
        outer.commit({"README.md": "# outer\n"}, "seed")
        sub = Path(outer.dir) / "nested"
        sub.mkdir()
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(sub, "Build a billing ledger service")
        self.assertEqual(os.listdir(sub), [])

    def test_allows_git_repo_with_readme_license_gitignore_and_preserves_seed_files(self) -> None:
        repo = Repo(self)
        repo.commit(
            {
                "README.md": "# Custom Seed Repo\n\nKeep me.\n",
                "LICENSE": "Apache-2.0 Seed\n",
                ".gitignore": "*.tmp\n",
            },
            "seed files",
        )
        res = genesis.create_greenfield_app(repo.dir, "Build an invoice ledger service")
        self.assertEqual(res["stack"], "python")
        self.assertIn("Keep me.", (Path(repo.dir) / "README.md").read_text(encoding="utf-8"))
        self.assertEqual((Path(repo.dir) / "LICENSE").read_text(encoding="utf-8"), "Apache-2.0 Seed\n")
        self.assertIn("*.tmp", (Path(repo.dir) / ".gitignore").read_text(encoding="utf-8"))

    def test_refuses_repos_with_source_files_subdirectories_or_non_blank_history(self) -> None:
        with_source = Repo(self)
        with_source.commit({"main.py": "print('hi')\n"}, "source")
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(with_source.dir, "Build a ledger service")

        with_env = Repo(self)
        with_env.commit({"README.md": "# ok\n"}, "seed")
        (Path(with_env.dir) / ".env").write_text("SECRET=1\n", encoding="utf-8")
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(with_env.dir, "Build a ledger service")

        with_deleted_history = Repo(self)
        with_deleted_history.commit({"legacy.py": "x = 1\n"}, "add legacy")
        with_deleted_history.commit({"legacy.py": None, "README.md": "# clean now\n"}, "delete legacy")
        with self.assertRaises(genesis.GenesisError):
            genesis.create_greenfield_app(with_deleted_history.dir, "Build a ledger service")


class SecretScrubbingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-scrub-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_scrubs_secrets_from_brief_adr_tickets_and_readme(self) -> None:
        tok_gh = "".join(["gh", "p_", "A1b2C3d4E5f6G7h8I9j0K1l2"])
        tok_aws = "".join(["AK", "IA", "01234567", "89ABCDEF"])
        tok_sk = "".join(["sk_", "live_", "abcd1234efgh5678ijkl"])
        kv_secret = "".join(["API_", "KEY=supersecretvalue999"])
        auth_hdr = "Authorization: Bearer " + "topsecretbearertoken1234"
        url_cred = "https://alice:hunter2secret@example.invalid/hook"
        pem_hdr = "".join(["-----BEGIN RSA ", "PRIVATE KEY-----"])
        raw_brief = (
            f"Build an audit trail service with {tok_gh}, {tok_aws}, {tok_sk}, "
            f"{kv_secret}, {auth_hdr}, {url_cred}, and {pem_hdr}."
        )

        target = self.tmp / "audit-svc"
        res = genesis.create_greenfield_app(target, raw_brief, commit=True)

        for secret_piece in (tok_gh, tok_aws, tok_sk, "supersecretvalue999", "topsecretbearertoken1234", "hunter2secret", pem_hdr):
            self.assertNotIn(secret_piece, res["brief"])
        self.assertIn("<REDACTED>", res["brief"])

        for rel in (
            "README.md",
            "docs/adr/0001-initial-architecture.md",
            "tickets/0001.md",
            "tickets/0002.md",
            "tickets/0003.md",
        ):
            text = (target / rel).read_text(encoding="utf-8")
            for secret_piece in (tok_gh, tok_aws, tok_sk, "supersecretvalue999", "topsecretbearertoken1234", "hunter2secret", pem_hdr):
                self.assertNotIn(secret_piece, text, f"{rel} leaked {secret_piece}")

        # Verify every generated file satisfies init_apply secret check cleanly.
        for path in target.rglob("*"):
            if path.is_file() and ".git" not in path.parts:
                rel = path.relative_to(target).as_posix()
                init_apply._check_secret_content(rel, path.read_bytes())


class AdrAndTicketsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-adr-spec-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_adr_0001_validates_even_with_threshold_numbers_in_brief(self) -> None:
        target = self.tmp / "cache-svc"
        res = genesis.create_greenfield_app(
            target,
            "Build a cache registry service with max 250 items and 15 second timeout",
            port=8080,
            today="2026-11-24",
        )
        adr_path = target / res["adr_path"]
        self.assertTrue(adr_path.is_file())
        adr_text = adr_path.read_text(encoding="utf-8")
        for heading in ("## Context", "## Decision", "## Alternatives", "## Consequences", "## Provenance"):
            self.assertIn(heading, adr_text)
        self.assertIn("2026-11-24", adr_text)
        self.assertEqual(adr_gov.validate_adr(adr_text), [])

    def test_backlog_tickets_pass_spec_check_even_with_vague_words_in_brief(self) -> None:
        target = self.tmp / "vague-brief-svc"
        vague_brief = (
            "Build a fast user-friendly orders tracker that works correctly, "
            "handles failures gracefully, and behaves as expected."
        )
        res = genesis.create_greenfield_app(target, vague_brief, tickets_count=5, name="orders-tracker")
        self.assertEqual(len(res["tickets"]), 5)
        for idx, rel in enumerate(res["tickets"], 1):
            t_path = target / rel
            self.assertTrue(t_path.is_file())
            text = t_path.read_text(encoding="utf-8")
            self.assertIsNone(spec.check(text), f"{rel} failed spec.check: {spec.check(text)}")
            fields = spec.fields(text)
            self.assertEqual(fields["feature"], "orders-tracker")
            self.assertIn(fields["priority"], ("P0", "P1", "P2"))
            if idx == 1:
                self.assertEqual(fields["priority"], "P0")
            globs = _perimeter_globs(text)
            self.assertTrue(globs, f"{rel} has empty perimeter")
            # At least one generated source or test file must match the ticket perimeter.
            all_files = [p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file() and ".git" not in p.parts]
            self.assertTrue(
                any(fnmatch.fnmatchcase(f, g) for f in all_files for g in globs),
                f"{rel} perimeter {globs} matched none of {all_files}",
            )


class AllStacksScaffoldAndContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-stacks-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_all_six_stacks_pass_init_infer_and_contract_layout(self) -> None:
        for stack in ("python", "javascript", "typescript", "go", "rust", "java"):
            with self.subTest(stack=stack):
                target = self.tmp / f"svc-{stack}"
                res = genesis.create_greenfield_app(
                    target,
                    f"Build a bookings ledger service on {stack}",
                    stack=stack,
                    name=f"bookings-{stack}",
                    port=8085,
                )
                self.assertEqual(res["stack"], stack)
                inferred = init.infer(str(target))
                self.assertEqual(inferred["stack"], stack)

                cf = target / "Containerfile"
                self.assertTrue(cf.is_file())
                cf_text = cf.read_text(encoding="utf-8")
                self.assertIn("EXPOSE 8085", cf_text)
                self.assertIn("HEALTHCHECK", cf_text)
                self.assertIn("/healthz", cf_text)

                contract = target / CONTRACT_DIR
                for rel in (
                    "workload.env",
                    "worker.md",
                    "policy.env",
                    "compose.yaml",
                    "smoke.sh",
                    "load.sh",
                    "flags/flags.json",
                ):
                    self.assertTrue((contract / rel).is_file(), f"{stack} missing {rel}")
                self.assertTrue((contract / "runners").is_dir())

                env_vals = _parse_env(contract / "workload.env")
                self.assertEqual(env_vals.get("WORKLOAD_HEALTH_PROBE"), "http:/healthz")
                self.assertEqual(env_vals.get("WORKLOAD_PORT"), "8085")
                wenv_text = (contract / "workload.env").read_text(encoding="utf-8")
                self.assertIn("# Provenance, measurement: incremental baseline scope", wenv_text)
                self.assertIn("MUTATION_LINES=", wenv_text)


class CommitAndCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-cli-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_commit_true_produces_clean_two_commit_history_and_commit_false_skips_commits(self) -> None:
        committed = self.tmp / "committed-svc"
        res = genesis.create_greenfield_app(
            committed,
            "Build a ledger entries service",
            stack="python",
            commit=True,
        )
        self.assertIsNotNone(res["commits"]["base"])
        self.assertIsNotNone(res["commits"]["feature"])
        self.assertEqual(_clean_git(committed, "rev-list", "--count", "HEAD"), "2")
        self.assertEqual(_clean_git(committed, "status", "--porcelain"), "")

        changed = _clean_git(committed, "diff", "--name-only", "HEAD~1", "HEAD").splitlines()
        self.assertTrue(changed)
        t1_globs = _perimeter_globs((committed / "tickets/0001.md").read_text(encoding="utf-8"))
        for path in changed:
            self.assertTrue(
                any(fnmatch.fnmatchcase(path, g) for g in t1_globs),
                f"{path} outside ticket 0001 perimeter {t1_globs}",
            )

        uncommitted = self.tmp / "uncommitted-svc"
        res_no = genesis.create_greenfield_app(
            uncommitted,
            "Build a ledger entries service",
            stack="python",
            commit=False,
        )
        self.assertIsNone(res_no["commits"]["base"])
        self.assertIsNone(res_no["commits"]["feature"])
        self.assertNotEqual(_clean_git(uncommitted, "status", "--porcelain"), "")

    def test_cli_brief_file_no_commit_and_json_output(self) -> None:
        brief_file = self.tmp / "brief.txt"
        brief_file.write_text("Build a task queue status service\n", encoding="utf-8")
        target = self.tmp / "queue-svc"

        proc = subprocess.run(
            [
                "python3",
                str(ROOT / "core" / "genesis.py"),
                "--repo",
                str(target),
                "--brief-file",
                str(brief_file),
                "--stack",
                "python",
                "--name",
                "task-queue",
                "--port",
                "8088",
                "--tickets-count",
                "2",
                "--no-commit",
                "--json",
            ],
            cwd=str(self.tmp),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "task-queue")
        self.assertEqual(payload["port"], 8088)
        self.assertEqual(len(payload["tickets"]), 2)
        self.assertIsNone(payload["commits"]["base"])

        bad = subprocess.run(
            ["python3", str(ROOT / "core" / "genesis.py"), "--repo", str(target), "--brief", "x", "--port", "80", "--json"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(bad.returncode, 1)
        bad_doc = json.loads(bad.stdout)
        self.assertFalse(bad_doc["ok"])


if __name__ == "__main__":
    unittest.main()
