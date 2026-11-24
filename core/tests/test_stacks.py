"""Specification tests for core/stacks.py (ADR 0102).

Strictly vendor-free and app-free: tests Go, TypeScript, and Python
poetry/requirements fallbacks, refusal messages, precedence, and provenance
on temporary repositories.
"""

from __future__ import annotations

import fnmatch
import json
import tempfile
import unittest
from pathlib import Path

from core import init, stacks


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


def _parse_env(text: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    values: dict[str, str] = {}
    blocks: dict[str, list[str]] = {}
    block: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            block.append(line)
        elif not line.strip():
            block = []
        else:
            key, sep, val = line.partition("=")
            assert sep, f"not KEY=VALUE: {line!r}"
            values[key] = val
            blocks[key] = block
            block = []
    return values, blocks


class GoInferenceTest(unittest.TestCase):
    def test_infer_go_with_pkg_and_sum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.24.2\n",
                    "go.sum": "",
                    "pkg/calc/calc.go": "package calc\nfunc Add(a, b int) int { return a + b }\n",
                    "pkg/calc/calc_test.go": "package calc\nimport \"testing\"\nfunc TestAdd(t *testing.T) {}\n",
                    "cmd/server/main.go": "package main\nfunc main() {}\n",
                },
            )
            d = stacks.infer_go(repo)
            self.assertEqual(d["stack"], "go")
            self.assertEqual(d["toolchain"], "docker.io/library/golang:1.24")
            self.assertIn("digest", d["toolchain_evidence"].lower())
            self.assertEqual(d["lockfiles"], "go.mod")
            self.assertEqual(d["scope"], "pkg/")
            self.assertEqual(d["test_root"], "")
            self.assertEqual(d["test_paths"], "*_test.go:*/*_test.go")
            self.assertEqual(d["test_cmd"], "go test ./...")
            self.assertIn("sast.sh pkg", d["sast_cmd"])
            self.assertIn('mutation.sh pkg "go test ./..."', d["mutation_cmd"])

    def test_go_scope_falls_back_internal_cmd_first_dir_dot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.23\n",
                    "go.sum": "",
                    "internal/svc/svc.go": "package svc\n",
                    "cmd/cli/main.go": "package main\n",
                },
            )
            self.assertEqual(stacks.infer_go(repo)["scope"], "internal/")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.23\n",
                    "go.sum": "",
                    "onlytests/x_test.go": "package onlytests\n",
                    "service/api.go": "package service\n",
                },
            )
            self.assertEqual(stacks.infer_go(repo)["scope"], "service/")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.23\n",
                    "go.sum": "",
                    "main.go": "package main\nfunc main() {}\n",
                },
            )
            d = stacks.infer_go(repo)
            self.assertEqual(d["scope"], ".")
            self.assertIn("sast.sh .", d["sast_cmd"])

    def test_go_missing_sum_raises_undraftable_naming_tidy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.24\n",
                    "main.go": "package main\nfunc main() {}\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                stacks.infer_go(repo)
            msg = str(ctx.exception)
            self.assertIn("go.sum", msg)
            self.assertIn("go mod tidy", msg)

    def test_go_missing_version_directive_raises_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n",
                    "go.sum": "",
                    "main.go": "package main\n",
                },
            )
            with self.assertRaises(init.Undraftable):
                stacks.infer_go(repo)

    def test_go_test_paths_match_only_go_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.24\n",
                    "go.sum": "",
                    "main.go": "package main\n",
                },
            )
            globs = stacks.infer_go(repo)["test_paths"].split(":")
            for p in ("main_test.go", "pkg/calc/calc_test.go", "cmd/server/main_test.go"):
                self.assertTrue(any(fnmatch.fnmatchcase(p, g) for g in globs), p)
            for p in ("main.go", "pkg/calc/calc.go", "pkg/testing_helper.go"):
                self.assertFalse(any(fnmatch.fnmatchcase(p, g) for g in globs), p)


class TypeScriptInferenceTest(unittest.TestCase):
    def test_infer_ts_workbench_without_scripts_test(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "ts-demo", "type": "module"}),
                    "tsconfig.json": json.dumps({"compilerOptions": {"strict": True}}),
                    "package-lock.json": json.dumps({"name": "ts-demo", "lockfileVersion": 3, "packages": {}}),
                    "src/calc.ts": "export const add = (a: number, b: number): number => a + b;\n",
                    "test/calc.test.ts": "import { test } from 'node:test';\n",
                },
            )
            d = stacks.infer_typescript(repo)
            self.assertEqual(d["stack"], "typescript")
            self.assertEqual(d["toolchain"], "workbench")
            self.assertEqual(d["lockfiles"], "package-lock.json")
            self.assertEqual(d["scope"], "src/")
            self.assertEqual(d["test_cmd"], "npm ci && node --test")
            self.assertIn("sast.sh src", d["sast_cmd"])
            self.assertIn('npm ci && sh ', d["mutation_cmd"])
            self.assertIn('mutation.sh src "node --test"', d["mutation_cmd"])
            self.assertEqual(d["test_paths"], "test/*:*/test/*:*.test.*:*.spec.*:*-test.*:*_test.*")

    def test_infer_ts_with_scripts_test_and_node_engine_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps(
                        {
                            "name": "ts-demo",
                            "scripts": {"test": "node --test"},
                            "engines": {"node": "^20"},
                        }
                    ),
                    "tsconfig.json": "{}",
                    "package-lock.json": json.dumps({"name": "ts-demo", "lockfileVersion": 3, "packages": {}}),
                    "lib/mod.ts": "export const x: number = 1;\n",
                },
            )
            d = stacks.infer_typescript(repo)
            self.assertEqual(d["toolchain"], "docker.io/library/node:20")
            self.assertEqual(d["scope"], "lib/")
            self.assertEqual(d["test_cmd"], "npm ci && npm test")
            self.assertIn('mutation.sh lib "npm test"', d["mutation_cmd"])

    def test_ts_missing_lockfile_raises_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "ts-demo"}),
                    "tsconfig.json": "{}",
                    "src/calc.ts": "export const x = 1;\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                stacks.infer_typescript(repo)
            self.assertIn("package-lock.json", str(ctx.exception))


class PythonFallbackInferenceTest(unittest.TestCase):
    def test_infer_poetry_without_uv_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pyproject.toml": (
                        "[tool.poetry]\n"
                        'name = "demo-poetry"\n'
                        'version = "0.1.0"\n'
                        "[tool.poetry.dependencies]\n"
                        'python = "^3.12"\n'
                    ),
                    "poetry.lock": "package = []\n",
                    "demo_poetry/__init__.py": "def add(a, b): return a + b\n",
                    "tests/test_demo.py": "def test_add(): assert True\n",
                },
            )
            d = stacks.infer_python_fallback(repo)
            self.assertEqual(d["stack"], "python")
            self.assertEqual(d["lockfiles"], "poetry.lock")
            self.assertEqual(d["scope"], "demo_poetry/")
            self.assertEqual(d["toolchain"], "workbench")
            self.assertIn("pytest", d["test_cmd"])
            self.assertIn("sast.sh demo_poetry", d["sast_cmd"])
            self.assertIn("mutation.sh demo_poetry", d["mutation_cmd"])
            self.assertIn("pytest", d["mutation_cmd"])

    def test_infer_poetry_incompatible_python_chooses_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pyproject.toml": (
                        "[tool.poetry]\n"
                        'name = "oldpy"\n'
                        "[tool.poetry.dependencies]\n"
                        'python = "~3.11"\n'
                    ),
                    "poetry.lock": "package = []\n",
                    "oldpy/__init__.py": "",
                },
            )
            d = stacks.infer_python_fallback(repo)
            self.assertEqual(d["toolchain"], "ghcr.io/astral-sh/uv:python3.11-trixie-slim")

    def test_infer_pinned_requirements_txt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "requirements.txt": (
                        "# production pins\n"
                        "\n"
                        "fastapi==0.141.1  # framework\n"
                        'uvicorn[standard]==0.34.0 ; python_version >= "3.10"\n'
                        "httpx==\\\n"
                        "  0.28.1\n"
                    ),
                    "mypkg/__init__.py": "def f(): return 1\n",
                    "tests/test_f.py": "def test_f(): assert True\n",
                },
            )
            d = stacks.infer_python_fallback(repo)
            self.assertEqual(d["stack"], "python")
            self.assertEqual(d["lockfiles"], "requirements.txt")
            self.assertEqual(d["scope"], "mypkg/")
            self.assertEqual(d["toolchain"], "workbench")
            self.assertIn("pytest", d["test_cmd"])
            self.assertIn("sast.sh mypkg", d["sast_cmd"])
            self.assertIn("mutation.sh mypkg", d["mutation_cmd"])

    def test_unpinned_requirements_txt_raises_naming_packages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "requirements.txt": (
                        "fastapi==0.141.1\n"
                        "flask\n"
                        "requests>=2.31\n"
                        "httpx~=0.27\n"
                        "pydantic==2.10.*\n"
                    ),
                    "mypkg/__init__.py": "",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                stacks.infer_python_fallback(repo)
            msg = str(ctx.exception)
            for pkg in ("flask", "requests", "httpx", "pydantic"):
                self.assertIn(pkg, msg)
            self.assertNotIn("fastapi", msg)


class PrecedenceAndProvenanceTest(unittest.TestCase):
    def test_precedence_uv_poetry_requirements_go_ts_js(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pyproject.toml": '[project]\nname = "p"\n',
                    "uv.lock": "version = 1\n",
                    "poetry.lock": "package = []\n",
                    "requirements.txt": "requests==2.32.5\n",
                    "go.mod": "module example.com/m\n\ngo 1.24\n",
                    "go.sum": "",
                    "package.json": '{"name": "n"}',
                    "tsconfig.json": "{}",
                    "package-lock.json": '{"name": "n", "lockfileVersion": 3, "packages": {}}',
                    "p/__init__.py": "",
                    "src/a.ts": "export const a = 1;\n",
                    "main.go": "package main\n",
                },
            )
            self.assertEqual(init.infer(repo)["lockfiles"], "uv.lock")

            (repo / "uv.lock").unlink()
            self.assertEqual(init.infer(repo)["lockfiles"], "poetry.lock")

            (repo / "poetry.lock").unlink()
            self.assertEqual(init.infer(repo)["lockfiles"], "requirements.txt")

            (repo / "pyproject.toml").unlink()
            (repo / "requirements.txt").unlink()
            self.assertEqual(init.infer(repo)["stack"], "go")

            (repo / "go.mod").unlink()
            (repo / "go.sum").unlink()
            self.assertEqual(init.infer(repo)["stack"], "typescript")

            (repo / "tsconfig.json").unlink()
            self.assertEqual(init.infer(repo)["stack"], "javascript")

    def test_rendered_workload_env_has_provenance_and_no_make_hazards(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "go.mod": "module example.com/demo\n\ngo 1.24\n",
                    "go.sum": "",
                    "pkg/calc/calc.go": "package calc\n",
                },
            )
            d = init.infer(repo)
            rendered = init.render_workload_env(d, {}, "2026-11-24", "abc1234")
            values, blocks = _parse_env(rendered)
            self.assertEqual(len(values), 12)
            for key, blk in blocks.items():
                self.assertTrue(any(ln.startswith("# Provenance,") for ln in blk), key)
            for key, val in values.items():
                self.assertNotIn("$", val, key)
                self.assertNotIn("#", val, key)


if __name__ == "__main__":
    unittest.main()
