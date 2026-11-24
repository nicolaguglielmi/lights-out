"""Unknown-repository verification corpus (docs/adr/0106): stdlib only.

Generates isolated temporary git repositories across supported stacks,
container configurations, and deterministic refusal shapes; runs core/init.py
drafting, optional verification callbacks, and core/light.py light-tier gates;
and writes a structured JSON report.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import contextlib
import dataclasses
import datetime as dt
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess  # nosec B404 - invokes git and local toolchain commands on temp repos
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from . import init, light
except ImportError:  # run as a script: core/ is sys.path[0]
    import init
    import light

SCHEMA = "lights-out.corpus/1"
DEFAULT_REPORT = ".crew/corpus/report.json"
REQUIRED_KEYS = frozenset(
    {
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
)


@dataclasses.dataclass(frozen=True)
class Shape:
    """Declarative specification for one synthetic corpus repository."""

    name: str
    kind: str  # "positive" or "negative"
    files: dict[str, str]
    expected_stack: str | None = None
    expected_has_plane: bool = False
    expected_port: int | None = None
    expected_probe: str | None = None
    expected_lockfiles: str | None = None
    expected_test_cmd_substr: str | None = None
    expected_toolchain: str | None = None
    expected_test_root: str | None = None
    expected_error_tokens: tuple[str, ...] = ()
    expected_compose_substrings: tuple[str, ...] = ()
    forbidden_compose_substrings: tuple[str, ...] = ()
    expected_smoke_substrings: tuple[str, ...] = ()
    expect_egress_example: bool = False
    init_service: str | None = None
    change_files: dict[str, str] = dataclasses.field(default_factory=dict)
    uv_lock_refresh: bool = False
    host_testable: bool = False
    host_sast: bool = False


Callback = Callable[[Shape, Path, Path, dict[str, Any]], str | None]


def default_shapes() -> list[Shape]:
    """Return the canonical positive and negative repository corpus shapes."""
    return [
        Shape(
            name="python-uv-http",
            kind="positive",
            expected_stack="python",
            expected_has_plane=True,
            expected_port=8000,
            expected_probe="http:/healthz",
            expected_lockfiles="uv.lock",
            expected_test_cmd_substr="pytest",
            expected_toolchain="workbench",
            uv_lock_refresh=True,
            host_testable=True,
            host_sast=True,
            files={
                "pyproject.toml": (
                    '[project]\nname = "py-http-svc"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.13"\ndependencies = []\n\n'
                    '[dependency-groups]\ndev = ["pytest"]\n\n'
                    '[tool.pytest.ini_options]\npythonpath = ["."]\n'
                ),
                "uv.lock": (
                    "version = 1\nrevision = 3\nrequires-python = \">=3.13\"\n\n"
                    '[[package]]\nname = "py-http-svc"\nversion = "0.1.0"\n'
                    'source = { virtual = "." }\n\n'
                    '[[package]]\nname = "pytest"\nversion = "9.2.0"\n'
                ),
                "py_http_svc/__init__.py": (
                    "def health_payload() -> dict[str, str]:\n"
                    '    return {"status": "ok"}\n'
                ),
                "py_http_svc/server.py": (
                    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                    "import json\nimport os\n\n"
                    "from py_http_svc import health_payload\n\n\n"
                    "class Handler(BaseHTTPRequestHandler):\n"
                    "    def do_GET(self) -> None:\n"
                    "        body = json.dumps(health_payload()).encode()\n"
                    "        self.send_response(200)\n"
                    '        self.send_header("Content-Type", "application/json")\n'
                    '        self.send_header("Content-Length", str(len(body)))\n'
                    "        self.end_headers()\n"
                    "        self.wfile.write(body)\n\n\n"
                    "def main() -> None:\n"
                    '    host = os.environ.get("BIND_HOST", "127.0.0.1")\n'
                    "    HTTPServer((host, 8000), Handler).serve_forever()\n"
                ),
                "tests/test_server.py": (
                    "from py_http_svc import health_payload\n\n\n"
                    "def test_health_payload() -> None:\n"
                    '    assert health_payload() == {"status": "ok"}\n'
                ),
                "Containerfile": (
                    "FROM python:3.13-slim\n"
                    "EXPOSE 8000\n"
                    "HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1\n"
                    'CMD ["python3", "-m", "py_http_svc.server"]\n'
                ),
            },
            change_files={
                "py_http_svc/__init__.py": (
                    "def health_payload() -> dict[str, str]:\n"
                    '    return {"status": "ok"}\n\n\n'
                    "def double(value: int) -> int:\n"
                    "    return value * 2\n"
                ),
                "tests/test_server.py": (
                    "from py_http_svc import double, health_payload\n\n\n"
                    "def test_health_payload() -> None:\n"
                    '    assert health_payload() == {"status": "ok"}\n\n\n'
                    "def test_double() -> None:\n"
                    "    assert double(4) == 8\n"
                ),
            },
        ),
        Shape(
            name="python-uv-lib",
            kind="positive",
            expected_stack="python",
            expected_has_plane=False,
            expected_lockfiles="uv.lock",
            expected_test_cmd_substr="pytest",
            expected_toolchain="workbench",
            uv_lock_refresh=True,
            host_testable=True,
            host_sast=True,
            files={
                "pyproject.toml": (
                    '[project]\nname = "py-lib"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.13"\ndependencies = []\n\n'
                    '[dependency-groups]\ndev = ["pytest"]\n\n'
                    '[tool.pytest.ini_options]\npythonpath = ["."]\n'
                ),
                "uv.lock": (
                    "version = 1\nrevision = 3\nrequires-python = \">=3.13\"\n\n"
                    '[[package]]\nname = "py-lib"\nversion = "0.1.0"\n'
                    'source = { virtual = "." }\n\n'
                    '[[package]]\nname = "pytest"\nversion = "9.2.0"\n'
                ),
                "py_lib/__init__.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "tests/test_py_lib.py": (
                    "from py_lib import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(2, 3) == 5\n"
                ),
            },
            change_files={
                "py_lib/__init__.py": (
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
                    "def sub(a: int, b: int) -> int:\n    return a - b\n"
                ),
                "tests/test_py_lib.py": (
                    "from py_lib import add, sub\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(2, 3) == 5\n\n\n"
                    "def test_sub() -> None:\n"
                    "    assert sub(7, 4) == 3\n"
                ),
            },
        ),
        Shape(
            name="python-requirements",
            kind="positive",
            expected_stack="python",
            expected_has_plane=False,
            expected_lockfiles="requirements.txt",
            expected_test_cmd_substr="--with-requirements requirements.txt",
            expected_toolchain="workbench",
            host_testable=True,
            host_sast=True,
            files={
                "requirements.txt": "# Pinned dependencies\npytest==9.2.0\n",
                "reqpkg/__init__.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "tests/test_reqpkg.py": (
                    "from reqpkg import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(1, 4) == 5\n"
                ),
            },
            change_files={
                "reqpkg/__init__.py": (
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
                    "def mul(a: int, b: int) -> int:\n    return a * b\n"
                ),
                "tests/test_reqpkg.py": (
                    "from reqpkg import add, mul\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(1, 4) == 5\n\n\n"
                    "def test_mul() -> None:\n"
                    "    assert mul(3, 4) == 12\n"
                ),
            },
        ),
        Shape(
            name="python-poetry-lib",
            kind="positive",
            expected_stack="python",
            expected_has_plane=False,
            expected_lockfiles="poetry.lock",
            expected_test_cmd_substr="poetry install",
            expected_toolchain="workbench",
            host_sast=True,
            files={
                "pyproject.toml": (
                    '[tool.poetry]\nname = "poetry-lib"\nversion = "0.1.0"\n'
                    '[tool.poetry.dependencies]\npython = "^3.13"\n'
                ),
                "poetry.lock": "package = []\n",
                "poetry_lib/__init__.py": "def add(a: int, b: int) -> int:\n    return a + b\n",
                "tests/test_poetry_lib.py": (
                    "from poetry_lib import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(4, 5) == 9\n"
                ),
            },
            change_files={
                "poetry_lib/__init__.py": (
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
                    "def double(x: int) -> int:\n    return x * 2\n"
                ),
                "tests/test_poetry_lib.py": (
                    "from poetry_lib import add, double\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(4, 5) == 9\n\n\n"
                    "def test_double() -> None:\n"
                    "    assert double(6) == 12\n"
                ),
            },
        ),
        Shape(
            name="javascript-npm-lib",
            kind="positive",
            expected_stack="javascript",
            expected_has_plane=False,
            expected_lockfiles="package-lock.json",
            expected_test_cmd_substr="npm test",
            expected_toolchain="workbench",
            host_testable=True,
            files={
                "package.json": json.dumps(
                    {
                        "name": "js-lib",
                        "version": "1.0.0",
                        "type": "module",
                        "scripts": {"test": "node --test"},
                    },
                    indent=2,
                )
                + "\n",
                "package-lock.json": json.dumps(
                    {
                        "name": "js-lib",
                        "version": "1.0.0",
                        "lockfileVersion": 3,
                        "requires": True,
                        "packages": {"": {"name": "js-lib", "version": "1.0.0"}},
                    },
                    indent=2,
                )
                + "\n",
                "src/math.js": "export function add(a, b) {\n  return a + b;\n}\n",
                "test/math.test.js": (
                    'import test from "node:test";\n'
                    'import assert from "node:assert/strict";\n'
                    'import { add } from "../src/math.js";\n\n'
                    'test("add", () => {\n  assert.equal(add(1, 2), 3);\n});\n'
                ),
            },
            change_files={
                "src/math.js": (
                    "export function add(a, b) {\n  return a + b;\n}\n\n"
                    "export function double(x) {\n  return x * 2;\n}\n"
                ),
                "test/math.test.js": (
                    'import test from "node:test";\n'
                    'import assert from "node:assert/strict";\n'
                    'import { add, double } from "../src/math.js";\n\n'
                    'test("add", () => {\n  assert.equal(add(1, 2), 3);\n});\n\n'
                    'test("double", () => {\n  assert.equal(double(5), 10);\n});\n'
                ),
            },
        ),
        Shape(
            name="typescript-npm-lib",
            kind="positive",
            expected_stack="typescript",
            expected_has_plane=False,
            expected_lockfiles="package-lock.json",
            expected_test_cmd_substr="node --test",
            expected_toolchain="workbench",
            host_testable=True,
            files={
                "package.json": json.dumps({"name": "ts-lib", "version": "1.0.0", "type": "module"}, indent=2) + "\n",
                "tsconfig.json": json.dumps(
                    {"compilerOptions": {"target": "ES2024", "module": "NodeNext", "strict": True}},
                    indent=2,
                )
                + "\n",
                "package-lock.json": json.dumps(
                    {
                        "name": "ts-lib",
                        "version": "1.0.0",
                        "lockfileVersion": 3,
                        "requires": True,
                        "packages": {"": {"name": "ts-lib", "version": "1.0.0"}},
                    },
                    indent=2,
                )
                + "\n",
                "src/calc.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
                "test/calc.test.ts": (
                    'import test from "node:test";\n'
                    'import assert from "node:assert/strict";\n'
                    'import { add } from "../src/calc.ts";\n\n'
                    'test("add", () => {\n  assert.equal(add(2, 3), 5);\n});\n'
                ),
            },
            change_files={
                "src/calc.ts": (
                    "export function add(a: number, b: number): number {\n  return a + b;\n}\n\n"
                    "export function double(x: number): number {\n  return x * 2;\n}\n"
                ),
                "test/calc.test.ts": (
                    'import test from "node:test";\n'
                    'import assert from "node:assert/strict";\n'
                    'import { add, double } from "../src/calc.ts";\n\n'
                    'test("add", () => {\n  assert.equal(add(2, 3), 5);\n});\n\n'
                    'test("double", () => {\n  assert.equal(double(6), 12);\n});\n'
                ),
            },
        ),
        Shape(
            name="go-http",
            kind="positive",
            expected_stack="go",
            expected_has_plane=True,
            expected_port=8080,
            expected_probe="tcp:8080",
            expected_lockfiles="go.mod",
            expected_test_cmd_substr="go test ./...",
            expected_toolchain="docker.io/library/golang:1.24",
            files={
                "go.mod": "module example.com/go-http\n\ngo 1.24\n",
                "go.sum": "",
                "pkg/calc/calc.go": "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n",
                "pkg/calc/calc_test.go": (
                    'package calc\n\nimport "testing"\n\n'
                    "func TestAdd(t *testing.T) {\n"
                    '\tif Add(1, 2) != 3 {\n\t\tt.Fatal("unexpected sum")\n\t}\n'
                    "}\n"
                ),
                "cmd/server/main.go": (
                    'package main\n\nimport (\n\t"fmt"\n\t"net/http"\n)\n\n'
                    "func main() {\n"
                    '\thttp.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) { fmt.Fprintln(w, "ok") })\n'
                    '\t_ = http.ListenAndServe(":8080", nil)\n'
                    "}\n"
                ),
                "Dockerfile": (
                    "FROM docker.io/library/golang:1.24 AS runtime\n"
                    "EXPOSE 8080\n"
                    'CMD ["/server"]\n'
                ),
            },
            change_files={
                "pkg/calc/calc.go": (
                    "package calc\n\nfunc Add(a, b int) int {\n\treturn a + b\n}\n\n"
                    "func Double(x int) int {\n\treturn x * 2\n}\n"
                ),
                "pkg/calc/calc_test.go": (
                    'package calc\n\nimport "testing"\n\n'
                    "func TestAdd(t *testing.T) {\n"
                    '\tif Add(1, 2) != 3 {\n\t\tt.Fatal("unexpected sum")\n\t}\n'
                    "}\n\n"
                    "func TestDouble(t *testing.T) {\n"
                    '\tif Double(4) != 8 {\n\t\tt.Fatal("unexpected double")\n\t}\n'
                    "}\n"
                ),
            },
        ),
        Shape(
            name="python-plain-http",
            kind="positive",
            expected_stack="python",
            expected_has_plane=True,
            expected_port=5000,
            expected_probe="tcp:5000",
            expected_lockfiles="uv.lock",
            expected_test_cmd_substr="unittest discover",
            expected_toolchain="workbench",
            host_testable=True,
            host_sast=True,
            files={
                "pyproject.toml": (
                    '[project]\nname = "plain-http"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.13"\ndependencies = []\n'
                ),
                "uv.lock": (
                    "version = 1\nrevision = 3\nrequires-python = \">=3.13\"\n\n"
                    '[[package]]\nname = "plain-http"\nversion = "0.1.0"\n'
                    'source = { virtual = "." }\n'
                ),
                "plain_http/__init__.py": (
                    "def status_text(code: int) -> str:\n"
                    '    return "ok" if code < 400 else "err"\n'
                ),
                "plain_http/server.py": (
                    "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                    "import os\n\n"
                    "from plain_http import status_text\n\n\n"
                    "class Handler(BaseHTTPRequestHandler):\n"
                    "    def do_GET(self) -> None:\n"
                    "        body = status_text(200).encode()\n"
                    "        self.send_response(200)\n"
                    '        self.send_header("Content-Length", str(len(body)))\n'
                    "        self.end_headers()\n"
                    "        self.wfile.write(body)\n\n\n"
                    "def main() -> None:\n"
                    '    host = os.environ.get("BIND_HOST", "127.0.0.1")\n'
                    "    HTTPServer((host, 5000), Handler).serve_forever()\n"
                ),
                "tests/test_server.py": (
                    "import unittest\n\nfrom plain_http import status_text\n\n\n"
                    "class StatusTextTest(unittest.TestCase):\n"
                    "    def test_ok(self) -> None:\n"
                    '        self.assertEqual(status_text(200), "ok")\n'
                ),
                "Containerfile": (
                    "FROM python:3.13-slim AS runtime\n"
                    "EXPOSE 5000\n"
                    'CMD ["python3", "-m", "plain_http.server"]\n'
                ),
            },
            change_files={
                "plain_http/__init__.py": (
                    "def status_text(code: int) -> str:\n"
                    '    return "ok" if code < 400 else "err"\n\n\n'
                    "def is_healthy(code: int) -> bool:\n"
                    "    return 200 <= code < 300\n"
                ),
                "tests/test_server.py": (
                    "import unittest\n\nfrom plain_http import is_healthy, status_text\n\n\n"
                    "class StatusTextTest(unittest.TestCase):\n"
                    "    def test_ok(self) -> None:\n"
                    '        self.assertEqual(status_text(200), "ok")\n\n'
                    "    def test_healthy(self) -> None:\n"
                    "        self.assertTrue(is_healthy(204))\n"
                ),
            },
        ),
        Shape(
            name="neg-unpinned-requirements",
            kind="negative",
            expected_error_tokens=("requirements.txt", "unpinned", "flask", "=="),
            files={
                "requirements.txt": "flask>=2.0\nrequests\n",
                "demo/__init__.py": "def ok() -> bool:\n    return True\n",
            },
        ),
        Shape(
            name="neg-pyproject-no-uv-lock",
            kind="negative",
            expected_error_tokens=("pyproject.toml", "uv.lock"),
            files={
                "pyproject.toml": '[project]\nname = "missing-lock"\nversion = "0.1.0"\n',
                "missing_lock/__init__.py": "def ok() -> bool:\n    return True\n",
            },
        ),
        Shape(
            name="neg-unknown-language",
            kind="negative",
            expected_error_tokens=("no stack found", "pyproject.toml", "go.mod", "package.json"),
            files={
                "README.md": "# Unknown stack sample\n",
                "Gemfile": 'source "https://rubygems.invalid"\n',
                "lib/sample.rb": "def ok; true; end\n",
            },
        ),
        Shape(
            name="neg-go-no-sum",
            kind="negative",
            expected_error_tokens=("go.mod", "go.sum", "go mod tidy"),
            files={
                "go.mod": "module example.com/missing-sum\n\ngo 1.24\n",
                "main.go": "package main\nfunc main() {}\n",
            },
        ),
        Shape(
            name="rust-crate-lib",
            kind="positive",
            expected_stack="rust",
            expected_has_plane=False,
            expected_lockfiles="Cargo.lock",
            expected_test_cmd_substr="cargo test",
            expected_toolchain="docker.io/library/rust:1.85",
            host_sast=True,
            files={
                "Cargo.toml": '[package]\nname = "rust-lib"\nversion = "0.1.0"\nedition = "2021"\n',
                "Cargo.lock": 'version = 3\n\n[[package]]\nname = "rust-lib"\nversion = "0.1.0"\n',
                "src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 { a + b }\n",
                "tests/calc_test.rs": "#[test]\nfn adds() { assert_eq!(rust_lib::add(1, 2), 3); }\n",
            },
            change_files={
                "src/lib.rs": (
                    "pub fn add(a: i32, b: i32) -> i32 { a + b }\n"
                    "pub fn double(x: i32) -> i32 { x * 2 }\n"
                ),
                "tests/calc_test.rs": (
                    "#[test]\nfn adds() { assert_eq!(rust_lib::add(1, 2), 3); }\n"
                    "#[test]\nfn doubles() { assert_eq!(rust_lib::double(4), 8); }\n"
                ),
            },
        ),
        Shape(
            name="java-maven-lib",
            kind="positive",
            expected_stack="java",
            expected_has_plane=False,
            expected_lockfiles="pom.xml",
            expected_test_cmd_substr="mvn test",
            expected_toolchain="docker.io/library/maven:3.9-eclipse-temurin-21",
            host_sast=True,
            files={
                "pom.xml": (
                    '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                    "  <modelVersion>4.0.0</modelVersion>\n"
                    "  <groupId>com.example</groupId>\n"
                    "  <artifactId>java-lib</artifactId>\n"
                    "  <version>0.1.0</version>\n"
                    "</project>\n"
                ),
                "src/main/java/com/example/Calc.java": (
                    "package com.example;\npublic class Calc {\n"
                    "  public static int add(int a, int b) { return a + b; }\n}\n"
                ),
                "src/test/java/com/example/CalcTest.java": (
                    "package com.example;\npublic class CalcTest {\n"
                    "  public void testAdd() { assert Calc.add(1, 2) == 3; }\n}\n"
                ),
            },
            change_files={
                "src/main/java/com/example/Calc.java": (
                    "package com.example;\npublic class Calc {\n"
                    "  public static int add(int a, int b) { return a + b; }\n"
                    "  public static int mul(int a, int b) { return a * b; }\n}\n"
                ),
                "src/test/java/com/example/CalcTest.java": (
                    "package com.example;\npublic class CalcTest {\n"
                    "  public void testAdd() { assert Calc.add(1, 2) == 3; }\n"
                    "  public void testMul() { assert Calc.mul(2, 3) == 6; }\n}\n"
                ),
            },
        ),
        Shape(
            name="java-gradle-lib",
            kind="positive",
            expected_stack="java",
            expected_has_plane=False,
            expected_lockfiles="gradle.lockfile",
            expected_test_cmd_substr="gradle test",
            expected_toolchain="docker.io/library/gradle:8-jdk21",
            host_sast=True,
            files={
                "build.gradle": "plugins { id 'java' }\n",
                "gradle.lockfile": "# Gradle lockfile\nempty=\n",
                "src/main/java/com/example/App.java": (
                    "package com.example;\npublic class App {\n"
                    "  public static int inc(int x) { return x + 1; }\n}\n"
                ),
                "src/test/java/com/example/AppTest.java": (
                    "package com.example;\npublic class AppTest {\n"
                    "  public void testInc() { assert App.inc(1) == 2; }\n}\n"
                ),
            },
            change_files={
                "src/main/java/com/example/App.java": (
                    "package com.example;\npublic class App {\n"
                    "  public static int inc(int x) { return x + 1; }\n"
                    "  public static int dec(int x) { return x - 1; }\n}\n"
                ),
                "src/test/java/com/example/AppTest.java": (
                    "package com.example;\npublic class AppTest {\n"
                    "  public void testInc() { assert App.inc(1) == 2; }\n"
                    "  public void testDec() { assert App.dec(3) == 2; }\n}\n"
                ),
            },
        ),
        Shape(
            name="javascript-pnpm-lib",
            kind="positive",
            expected_stack="javascript",
            expected_has_plane=False,
            expected_lockfiles="pnpm-lock.yaml",
            expected_test_cmd_substr="pnpm",
            expected_toolchain="workbench",
            files={
                "package.json": json.dumps(
                    {"name": "js-pnpm", "version": "1.0.0", "scripts": {"test": "node --test"}}, indent=2
                )
                + "\n",
                "pnpm-lock.yaml": "lockfileVersion: '9.0'\nsettings:\n  autoInstallPeers: true\n",
                "src/math.js": "export function add(a, b) {\n  return a + b;\n}\n",
                "test/math.test.js": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add } from "../src/math.js";\ntest("add", () => { assert.equal(add(1, 2), 3); });\n'
                ),
            },
            change_files={
                "src/math.js": "export function add(a, b) {\n  return a + b;\n}\nexport function sub(a, b) {\n  return a - b;\n}\n",
                "test/math.test.js": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add, sub } from "../src/math.js";\n'
                    'test("add", () => { assert.equal(add(1, 2), 3); });\n'
                    'test("sub", () => { assert.equal(sub(5, 2), 3); });\n'
                ),
            },
        ),
        Shape(
            name="typescript-yarn-lib",
            kind="positive",
            expected_stack="typescript",
            expected_has_plane=False,
            expected_lockfiles="yarn.lock",
            expected_test_cmd_substr="yarn",
            expected_toolchain="workbench",
            files={
                "package.json": json.dumps({"name": "ts-yarn", "version": "1.0.0", "type": "module"}, indent=2) + "\n",
                "tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2024", "strict": True}}, indent=2) + "\n",
                "yarn.lock": "# yarn lockfile v1\n\n",
                "src/calc.ts": "export function add(a: number, b: number): number {\n  return a + b;\n}\n",
                "test/calc.test.ts": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add } from "../src/calc.ts";\ntest("add", () => { assert.equal(add(1, 2), 3); });\n'
                ),
            },
            change_files={
                "src/calc.ts": (
                    "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
                    "export function mul(a: number, b: number): number {\n  return a * b;\n}\n"
                ),
                "test/calc.test.ts": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add, mul } from "../src/calc.ts";\n'
                    'test("add", () => { assert.equal(add(1, 2), 3); });\n'
                    'test("mul", () => { assert.equal(mul(2, 3), 6); });\n'
                ),
            },
        ),
        Shape(
            name="monorepo-npm-service",
            kind="positive",
            init_service="services/api",
            expected_stack="javascript",
            expected_has_plane=False,
            expected_test_root="services/api/",
            expected_lockfiles="services/api/package-lock.json",
            expected_test_cmd_substr="--workspaces=false",
            expected_toolchain="workbench",
            host_testable=True,
            files={
                "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["services/*"]}, indent=2) + "\n",
                "services/api/package.json": json.dumps(
                    {"name": "api", "version": "1.0.0", "type": "module", "scripts": {"test": "node --test"}}, indent=2
                )
                + "\n",
                "services/api/package-lock.json": json.dumps(
                    {"name": "api", "version": "1.0.0", "lockfileVersion": 3, "requires": True, "packages": {"": {"name": "api", "version": "1.0.0"}}},
                    indent=2,
                )
                + "\n",
                "services/api/src/calc.js": "export function add(a, b) {\n  return a + b;\n}\n",
                "services/api/test/calc.test.js": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add } from "../src/calc.js";\ntest("add", () => { assert.equal(add(1, 2), 3); });\n'
                ),
                "services/web/package.json": json.dumps({"name": "web", "version": "1.0.0"}, indent=2) + "\n",
                "services/web/src/index.js": "export const web = 1;\n",
            },
            change_files={
                "services/api/src/calc.js": (
                    "export function add(a, b) {\n  return a + b;\n}\n"
                    "export function mul(a, b) {\n  return a * b;\n}\n"
                ),
                "services/api/test/calc.test.js": (
                    'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                    'import { add, mul } from "../src/calc.js";\n'
                    'test("add", () => { assert.equal(add(1, 2), 3); });\n'
                    'test("mul", () => { assert.equal(mul(2, 3), 6); });\n'
                ),
            },
        ),
        Shape(
            name="stateful-python-service",
            kind="positive",
            expected_stack="python",
            expected_has_plane=True,
            expected_port=8000,
            expected_probe="http:/healthz",
            expected_lockfiles="uv.lock",
            expected_test_cmd_substr="pytest",
            expected_toolchain="workbench",
            expected_compose_substrings=("crew-postgres", "crew-redis", "x-crew-worker", "migrate"),
            expected_smoke_substrings=("SMOKE_AUTH_HEADER",),
            expect_egress_example=True,
            uv_lock_refresh=True,
            host_testable=True,
            host_sast=True,
            files={
                "pyproject.toml": (
                    '[project]\nname = "stateful-svc"\nversion = "0.1.0"\n'
                    'requires-python = ">=3.13"\ndependencies = []\n\n'
                    '[dependency-groups]\ndev = ["pytest"]\n\n'
                    '[tool.pytest.ini_options]\npythonpath = ["."]\n'
                ),
                "uv.lock": (
                    'version = 1\nrevision = 3\nrequires-python = ">=3.13"\n\n'
                    '[[package]]\nname = "stateful-svc"\nversion = "0.1.0"\n'
                    'source = { virtual = "." }\n\n'
                    '[[package]]\nname = "pytest"\nversion = "9.2.0"\n'
                ),
                "stateful_svc/__init__.py": 'def ok() -> bool:\n    return True\n',
                "tests/test_ok.py": "from stateful_svc import ok\n\n\ndef test_ok() -> None:\n    assert ok()\n",
                "Containerfile": (
                    "FROM python:3.13-slim\n"
                    "EXPOSE 8000\n"
                    "HEALTHCHECK CMD curl -f http://localhost:8000/healthz || exit 1\n"
                ),
                ".env.example": (
                    "DATABASE_URL=postgresql://app:pass@postgres:5432/app\n"
                    "REDIS_URL=redis://redis:6379/0\n"
                ),
                "docker-compose.yml": "services:\n  db:\n    image: postgres:17-alpine\n",
            },
            change_files={
                "stateful_svc/__init__.py": "def ok() -> bool:\n    return True\n\n\ndef double(x: int) -> int:\n    return x * 2\n",
                "tests/test_ok.py": (
                    "from stateful_svc import double, ok\n\n\ndef test_ok() -> None:\n    assert ok()\n\n\n"
                    "def test_double() -> None:\n    assert double(3) == 6\n"
                ),
            },
        ),
        Shape(
            name="stateless-comment-only-service",
            kind="positive",
            expected_stack="python",
            expected_has_plane=True,
            expected_port=8000,
            expected_probe="tcp:8000",
            expected_lockfiles="uv.lock",
            expected_test_cmd_substr="unittest discover",
            expected_toolchain="workbench",
            forbidden_compose_substrings=("crew-postgres", "crew-redis", "crew-mysql", "x-crew-worker"),
            expected_smoke_substrings=("SMOKE_AUTH_HEADER",),
            expect_egress_example=True,
            host_testable=True,
            host_sast=True,
            files={
                "pyproject.toml": '[project]\nname = "stateless-svc"\nversion = "0.1.0"\nrequires-python = ">=3.13"\n',
                "uv.lock": (
                    'version = 1\nrevision = 3\nrequires-python = ">=3.13"\n\n'
                    '[[package]]\nname = "stateless-svc"\nversion = "0.1.0"\nsource = { virtual = "." }\n'
                ),
                "stateless_svc/__init__.py": "def ping() -> str:\n    return 'pong'\n",
                "tests/test_ping.py": (
                    "import unittest\nfrom stateless_svc import ping\n\n"
                    "class T(unittest.TestCase):\n    def test_ping(self) -> None:\n        self.assertEqual(ping(), 'pong')\n"
                ),
                "Containerfile": "FROM python:3.13-slim\nEXPOSE 8000\n",
                ".env.example": "# Optional DATABASE_URL=postgresql://localhost/unused\n# REDIS_URL=redis://localhost\nAPP_PORT=8000\n",
            },
            change_files={
                "stateless_svc/__init__.py": "def ping() -> str:\n    return 'pong'\n\ndef add(a: int, b: int) -> int:\n    return a + b\n",
                "tests/test_ping.py": (
                    "import unittest\nfrom stateless_svc import add, ping\n\n"
                    "class T(unittest.TestCase):\n    def test_ping(self) -> None:\n        self.assertEqual(ping(), 'pong')\n"
                    "    def test_add(self) -> None:\n        self.assertEqual(add(1, 2), 3)\n"
                ),
            },
        ),
        Shape(
            name="neg-rust-no-lock",
            kind="negative",
            expected_error_tokens=("Cargo.lock", "cargo generate-lockfile"),
            files={
                "Cargo.toml": '[package]\nname = "no-lock"\nversion = "0.1.0"\n',
                "src/lib.rs": "pub fn ok() -> bool { true }\n",
            },
        ),
        Shape(
            name="neg-gradle-no-lock",
            kind="negative",
            expected_error_tokens=("gradle.lockfile", "--write-locks"),
            files={
                "build.gradle": "plugins { id 'java' }\n",
                "src/main/java/App.java": "class App {}\n",
            },
        ),
        Shape(
            name="neg-bun-binary-lockb",
            kind="negative",
            expected_error_tokens=("bun.lockb", "bun.lock"),
            files={
                "package.json": json.dumps({"name": "bun-bin"}),
                "bun.lockb": "binary-lockb",
                "src/index.js": "export const x = 1;\n",
            },
        ),
        Shape(
            name="neg-monorepo-ambiguous",
            kind="negative",
            expected_error_tokens=("INIT_SERVICE", "apps/api", "apps/web"),
            files={
                "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["apps/*"]}),
                "apps/api/package.json": json.dumps({"name": "api"}),
                "apps/api/src/index.js": "export const a = 1;\n",
                "apps/web/package.json": json.dumps({"name": "web"}),
                "apps/web/src/index.js": "export const b = 2;\n",
            },
        ),
    ]


def _clean_git_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "corpus-bot",
            "GIT_AUTHOR_EMAIL": "corpus@localhost.invalid",
            "GIT_COMMITTER_NAME": "corpus-bot",
            "GIT_COMMITTER_EMAIL": "corpus@localhost.invalid",
        }
    )
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(  # nosec B603, B607 - fixed git binary with explicit argv list
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        env=_clean_git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _write_files(repo: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _file_hashes(repo: Path, files: Sequence[str]) -> dict[str, str]:
    return {
        rel: hashlib.sha256((repo / rel).read_bytes()).hexdigest()
        for rel in files
        if (repo / rel).is_file()
    }


def _parse_workload_env(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    errors: list[str] = []
    block: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if line.startswith("#"):
            block.append(line)
            continue
        if not line:
            block = []
            continue
        key, sep, val = line.partition("=")
        if not sep:
            errors.append(f"workload.env line {lineno} is not KEY=VALUE")
            block = []
            continue
        if key in values:
            errors.append(f"workload.env defines duplicate key {key}")
        if not any(c.startswith("# Provenance,") for c in block):
            errors.append(f"workload.env {key} lacks a '# Provenance,' comment")
        values[key] = val
        block = []
    missing = sorted(REQUIRED_KEYS - set(values))
    if missing:
        errors.append(f"workload.env missing required keys: {', '.join(missing)}")
    for cmd_key in ("TEST_CMD", "SAST_CMD", "MUTATION_CMD"):
        val = values.get(cmd_key, "")
        if not val:
            errors.append(f"workload.env {cmd_key} is empty")
        elif "$" in val or "#" in val:
            errors.append(f"workload.env {cmd_key} holds $ or #")
    return values, errors


def _build_repo(shape: Shape, root: Path, refresh_uv: bool) -> tuple[Path, str]:
    repo = root / shape.name
    repo.mkdir(parents=True, exist_ok=True)
    _write_files(repo, shape.files)
    proc = subprocess.run(  # nosec B603, B607 - fixed git init with explicit argv list
        ["git", "-C", str(repo), "init", "-q", "-b", "main"],
        env=_clean_git_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _git(repo, "init", "-q")
        _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    if refresh_uv and shape.uv_lock_refresh and shutil.which("uv"):
        lock_file = repo / "uv.lock"
        if lock_file.exists():
            lock_file.unlink()
        lock_proc = subprocess.run(  # nosec B603, B607 - uv lock inside isolated temp repo
            ["uv", "lock"],
            cwd=repo,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        if lock_proc.returncode != 0:
            raise RuntimeError(f"uv lock failed for {shape.name}: {lock_proc.stderr.strip()}")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial corpus seed")
    return repo, _git(repo, "rev-parse", "HEAD")


def _run_light_gate(repo: Path, base: str, head: str, test_on_host: bool) -> tuple[int, str, str]:
    args = [f"BASE={base}", f"HEAD={head}"]
    if test_on_host:
        args.append("TEST_ON_HOST=1")
    old_cwd = os.getcwd()
    err_buf = io.StringIO()
    try:
        os.chdir(repo)
        with contextlib.redirect_stderr(err_buf):
            line, code, _ = light.judge(args)
    finally:
        os.chdir(old_cwd)
    return code, line, err_buf.getvalue().strip()


def _run_osv_check(repo: Path, lockfiles: str) -> str | None:
    if not shutil.which("osv-scanner"):
        return "osv-scanner not on PATH"
    entries = [p for p in lockfiles.split(":") if p]
    if not entries:
        return "empty WORKLOAD_LOCKFILES"
    argv = ["osv-scanner", "scan", "source", "--format", "json"]
    for entry in entries:
        argv += ["-L", str(repo / entry)]
    proc = subprocess.run(  # nosec B603 - fixed osv-scanner argv list
        argv,
        cwd=repo,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return None
    if proc.returncode == 128 and "No package sources found" in proc.stderr:
        return None
    return f"osv-scanner exited {proc.returncode}: {(proc.stderr or proc.stdout).strip()[:240]}"


def _run_sast_check(repo: Path, sast_cmd: str, test_root: str = "") -> str | None:
    argv = shlex.split(sast_cmd)
    if not argv:
        return "empty SAST_CMD"
    run_cwd = repo / test_root if test_root else repo
    proc = subprocess.run(  # nosec B603 - parsed SAST_CMD argv list from drafted contract
        argv,
        cwd=run_cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return f"SAST_CMD exited {proc.returncode}: {proc.stderr.strip()[:240]}"
    try:
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        return f"SAST_CMD produced invalid JSON ({exc}): {proc.stdout[:160]}"
    if int(payload.get("findings", -1)) != 0:
        return f"SAST_CMD reported {payload.get('findings')} findings"
    return None


def _draft_positive(
    shape: Shape,
    repo: Path,
    contract_dir: Path,
    stacks_dir: Path,
    c1: str,
    today: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    errors: list[str] = []
    before_hashes = _file_hashes(repo, list(shape.files))
    err_buf = io.StringIO()
    stack: str | None = None
    try:
        with contextlib.redirect_stderr(err_buf):
            stack = init.write_draft(
                str(repo),
                str(contract_dir),
                str(stacks_dir),
                today,
                c1[:7],
                service=shape.init_service,
            )
    except init.Undraftable as exc:
        errors.append(f"write_draft raised Undraftable: {exc}")

    if _file_hashes(repo, list(shape.files)) != before_hashes:
        errors.append("write_draft modified existing repository files")

    actual_port: int | None = None
    health_probe: str | None = None
    lockfiles = ""
    toolchain = ""
    test_cmd = ""
    sast_cmd = ""
    test_root = ""
    callbacks_out: dict[str, str] = {}
    draft_info: dict[str, Any] = {}

    if stack is not None:
        if shape.expected_stack and stack != shape.expected_stack:
            errors.append(f"expected stack {shape.expected_stack!r}, got {stack!r}")

        env_path = contract_dir / "workload.env"
        for required_file in ("workload.env", "worker.md", "policy.env"):
            if not (contract_dir / required_file).is_file():
                errors.append(f"missing drafted {required_file}")
        if not (contract_dir / "runners").is_dir() or not any((contract_dir / "runners").iterdir()):
            errors.append("missing or empty drafted runners/")

        if env_path.is_file():
            env_vals, env_errors = _parse_workload_env(env_path)
            errors.extend(env_errors)
            if env_vals.get("MUTATION_BASELINE", "") != "":
                errors.append(f"MUTATION_BASELINE must start empty, got {env_vals.get('MUTATION_BASELINE')!r}")
            lockfiles = env_vals.get("WORKLOAD_LOCKFILES", "")
            toolchain = env_vals.get("TOOLCHAIN_IMAGE", "")
            test_cmd = env_vals.get("TEST_CMD", "")
            sast_cmd = env_vals.get("SAST_CMD", "")
            test_root = env_vals.get("WORKLOAD_TEST_ROOT", "")

            if shape.expected_test_root is not None and test_root != shape.expected_test_root:
                errors.append(f"expected WORKLOAD_TEST_ROOT={shape.expected_test_root!r}, got {test_root!r}")
            if shape.expected_lockfiles and lockfiles != shape.expected_lockfiles:
                errors.append(f"expected WORKLOAD_LOCKFILES={shape.expected_lockfiles!r}, got {lockfiles!r}")
            if shape.expected_toolchain and toolchain != shape.expected_toolchain:
                errors.append(f"expected TOOLCHAIN_IMAGE={shape.expected_toolchain!r}, got {toolchain!r}")
            if shape.expected_test_cmd_substr and shape.expected_test_cmd_substr not in test_cmd:
                errors.append(f"TEST_CMD={test_cmd!r} missing {shape.expected_test_cmd_substr!r}")

            if shape.expected_has_plane:
                for pfile in ("compose.yaml", "flags/flags.json", "smoke.sh", "load.sh"):
                    p = contract_dir / pfile
                    if not p.is_file():
                        errors.append(f"missing drafted plane file {pfile}")
                    elif pfile.endswith(".sh") and not os.access(p, os.X_OK):
                        errors.append(f"drafted plane script {pfile} is not executable")
                compose_text = (
                    (contract_dir / "compose.yaml").read_text(encoding="utf-8")
                    if (contract_dir / "compose.yaml").is_file()
                    else ""
                )
                for needle in shape.expected_compose_substrings:
                    if needle not in compose_text:
                        errors.append(f"compose.yaml missing expected hint {needle!r}")
                for forbidden in shape.forbidden_compose_substrings:
                    if forbidden in compose_text:
                        errors.append(f"compose.yaml unexpectedly contained {forbidden!r}")
                smoke_text = (
                    (contract_dir / "smoke.sh").read_text(encoding="utf-8")
                    if (contract_dir / "smoke.sh").is_file()
                    else ""
                )
                for needle in shape.expected_smoke_substrings:
                    if needle not in smoke_text:
                        errors.append(f"smoke.sh missing expected token {needle!r}")
                if shape.expect_egress_example:
                    egress_ex = contract_dir / "crew-egress.txt.example"
                    if not egress_ex.is_file():
                        errors.append("missing drafted crew-egress.txt.example")
                    else:
                        active = [
                            ln for ln in egress_ex.read_text(encoding="utf-8").splitlines()
                            if ln.strip() and not ln.lstrip().startswith("#")
                        ]
                        if active:
                            errors.append(f"crew-egress.txt.example must stay commented out: {active!r}")
                    if (contract_dir / "crew-egress.txt").exists():
                        errors.append("draft unexpectedly created active crew-egress.txt")
                if not env_vals.get("CANARY_VERSION"):
                    errors.append("CANARY_VERSION is empty for container shape")
                health_probe = env_vals.get("WORKLOAD_HEALTH_PROBE", "")
                if shape.expected_probe and health_probe != shape.expected_probe:
                    errors.append(f"expected WORKLOAD_HEALTH_PROBE={shape.expected_probe!r}, got {health_probe!r}")
                raw_port = env_vals.get("WORKLOAD_PORT")
                if shape.expected_port == 8000:
                    if raw_port is not None:
                        errors.append(f"WORKLOAD_PORT must be omitted for default 8000, got {raw_port!r}")
                    actual_port = 8000
                elif shape.expected_port is not None:
                    if raw_port != str(shape.expected_port):
                        errors.append(f"expected WORKLOAD_PORT={shape.expected_port}, got {raw_port!r}")
                    if raw_port and raw_port.isdigit():
                        actual_port = int(raw_port)
            else:
                if (contract_dir / "compose.yaml").exists():
                    errors.append("non-container shape unexpectedly drafted compose.yaml")
                for empty_key in ("CANARY_VERSION", "WORKLOAD_KILL_SWITCH", "WORKLOAD_SAMPLE_PATH"):
                    if env_vals.get(empty_key, "") != "":
                        errors.append(f"gates-only shape expected empty {empty_key}, got {env_vals.get(empty_key)!r}")
                if "WORKLOAD_PORT" in env_vals:
                    errors.append(f"gates-only shape unexpectedly set WORKLOAD_PORT={env_vals['WORKLOAD_PORT']!r}")

            if (contract_dir / "worker.md").is_file():
                worker_md = (contract_dir / "worker.md").read_text(encoding="utf-8")
                for heading in ("## Scope", "## Tests"):
                    if heading not in worker_md.splitlines():
                        errors.append(f"worker.md missing heading {heading!r}")

        draft_info = {
            "stack": stack,
            "has_plane": shape.expected_has_plane,
            "port": actual_port,
            "health_probe": health_probe,
            "lockfiles": lockfiles,
            "toolchain": toolchain,
            "test_cmd": test_cmd,
        }

    entry = {
        "name": shape.name,
        "kind": shape.kind,
        "status": "PASS" if not errors else "FAIL",
        "stack": stack,
        "has_plane": shape.expected_has_plane,
        "expected_port": shape.expected_port,
        "actual_port": actual_port,
        "health_probe": health_probe,
        "lockfiles": lockfiles,
        "toolchain": toolchain,
        "test_cmd": test_cmd,
        "light_verdict": "",
        "callbacks": callbacks_out,
        "errors": errors,
    }
    ctx = {
        "repo": repo,
        "contract_dir": contract_dir,
        "draft_info": draft_info,
        "lockfiles": lockfiles,
        "toolchain": toolchain,
        "sast_cmd": sast_cmd,
        "test_root": test_root,
    }
    return entry, ctx


def _run_on_draft_step(shape: Shape, entry: dict[str, Any], ctx: dict[str, Any], on_draft: Callback) -> float:
    t0 = time.monotonic()
    cb_err = on_draft(shape, ctx["repo"], ctx["contract_dir"], ctx["draft_info"])
    if cb_err:
        entry["errors"].append(cb_err)
        entry["callbacks"]["on_draft"] = f"FAIL: {cb_err}"
    else:
        entry["callbacks"]["on_draft"] = "PASS"
    return (time.monotonic() - t0) * 1000


def _run_light_step(shape: Shape, entry: dict[str, Any], ctx: dict[str, Any], full: bool) -> float:
    t0 = time.monotonic()
    repo = ctx["repo"]
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit drafted contract")
    c2 = _git(repo, "rev-parse", "HEAD")
    if shape.change_files:
        _write_files(repo, shape.change_files)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "feature and test update")
        c3 = _git(repo, "rev-parse", "HEAD")
    else:
        c3 = c2
    run_host = bool(full and shape.host_testable and ctx["toolchain"] == "workbench")
    rc, line, light_err = _run_light_gate(repo, c2, c3, test_on_host=run_host)
    entry["light_verdict"] = line
    if rc != 0 or not line.startswith("GO: gates-light"):
        entry["errors"].append(f"core.light failed ({line}): {light_err}")
    return (time.monotonic() - t0) * 1000


def _run_full_step(shape: Shape, entry: dict[str, Any], ctx: dict[str, Any]) -> float:
    t0 = time.monotonic()
    osv_err = _run_osv_check(ctx["repo"], ctx["lockfiles"])
    if osv_err:
        entry["errors"].append(osv_err)
    else:
        entry["callbacks"]["osv"] = "PASS"
    if shape.host_sast and ctx["sast_cmd"] and (ctx["contract_dir"] / "runners" / "sast.sh").is_file():
        sast_err = _run_sast_check(ctx["repo"], ctx["sast_cmd"], test_root=ctx["test_root"])
        if sast_err:
            entry["errors"].append(sast_err)
        else:
            entry["callbacks"]["sast"] = "PASS"
    elif not shape.host_testable:
        entry["callbacks"]["host_gates"] = f"skipped: external or stub toolchain ({ctx['toolchain']})"
    return (time.monotonic() - t0) * 1000


def _verify_negative(shape: Shape, repo: Path, contract_dir: Path, stacks_dir: Path, c1: str, today: str) -> dict[str, Any]:
    errors: list[str] = []
    actual_error = ""
    err_buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(err_buf):
            stack = init.write_draft(
                str(repo),
                str(contract_dir),
                str(stacks_dir),
                today,
                c1[:7],
                service=shape.init_service,
            )
        errors.append(f"expected Undraftable, but write_draft succeeded with stack={stack!r}")
    except init.Undraftable as exc:
        actual_error = str(exc).strip()
        if not actual_error:
            errors.append("Undraftable message was empty")
        elif "\n" in actual_error:
            errors.append(f"Undraftable message spanned multiple lines: {actual_error!r}")
        lower_err = actual_error.lower()
        for token in shape.expected_error_tokens:
            if token.lower() not in lower_err:
                errors.append(f"error {actual_error!r} missing expected hint {token!r}")

    if (contract_dir / "workload.env").exists():
        errors.append("failed draft unexpectedly wrote workload.env")

    return {
        "name": shape.name,
        "kind": shape.kind,
        "status": "PASS" if not errors else "FAIL",
        "expected_stack": None,
        "actual_error": actual_error,
        "errors": errors,
    }


def _build_repo_timed(shape: Shape, tmp_path: Path, refresh_uv: bool) -> tuple[Path | None, str, str | None, float]:
    t0 = time.monotonic()
    try:
        repo, c1 = _build_repo(shape, tmp_path, refresh_uv=refresh_uv)
        return repo, c1, None, (time.monotonic() - t0) * 1000
    except Exception as exc:  # noqa: BLE001 - capture setup failures in structured shape report
        return None, "", str(exc), (time.monotonic() - t0) * 1000


def _run_indexed_step(
    indices: list[int],
    fn: Callable[[int], float],
    elapsed_ms: list[float],
    max_workers: int = 10,
) -> None:
    if len(indices) > 1:
        with ThreadPoolExecutor(max_workers=min(max_workers, len(indices))) as pool:
            futs = {i: pool.submit(fn, i) for i in indices}
            for i, fut in futs.items():
                elapsed_ms[i] += fut.result()
    else:
        for i in indices:
            elapsed_ms[i] += fn(i)


def run(
    *,
    contract_rel: str = ".",
    stacks_dir: str | os.PathLike[str] | None = None,
    report_path: str | os.PathLike[str] | None = DEFAULT_REPORT,
    verify_light: bool = True,
    on_draft: Callback | None = None,
    shapes: Sequence[Shape] | None = None,
    full: bool = False,
) -> tuple[int, int, list[dict[str, Any]]]:
    """Build and verify the corpus of synthetic git repositories.

    Returns ``(passed, total, details)`` and writes the structured JSON report
    to ``report_path`` unless ``report_path`` is ``None``.
    """
    repo_root = Path(__file__).resolve().parents[1]
    resolved_stacks = Path(stacks_dir) if stacks_dir is not None else repo_root / "stacks"
    selected_shapes = list(shapes) if shapes is not None else default_shapes()
    today = dt.date.today().isoformat()
    t0 = time.monotonic()
    details: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="corpus-") as tmp:
        tmp_path = Path(tmp)
        contexts: list[dict[str, Any] | None] = []
        elapsed_ms: list[float] = []

        if len(selected_shapes) > 1:
            with ThreadPoolExecutor(max_workers=min(10, len(selected_shapes))) as pool:
                built = list(pool.map(lambda s: _build_repo_timed(s, tmp_path, full), selected_shapes))
        else:
            built = [_build_repo_timed(s, tmp_path, full) for s in selected_shapes]

        for shape, (repo, c1, setup_err, build_ms) in zip(selected_shapes, built):
            s_t0 = time.monotonic()
            if setup_err is not None or repo is None:
                details.append(
                    {
                        "name": shape.name,
                        "kind": shape.kind,
                        "status": "FAIL",
                        "errors": [setup_err or "repo setup failed"],
                    }
                )
                contexts.append(None)
                elapsed_ms.append(build_ms + (time.monotonic() - s_t0) * 1000)
                continue
            contract_dir = repo if contract_rel in ("", ".") else repo / contract_rel
            if shape.kind == "positive":
                entry, ctx = _draft_positive(shape, repo, contract_dir, resolved_stacks, c1, today)
            elif shape.kind == "negative":
                entry = _verify_negative(shape, repo, contract_dir, resolved_stacks, c1, today)
                ctx = None
            else:
                entry = {
                    "name": shape.name,
                    "kind": shape.kind,
                    "status": "FAIL",
                    "errors": [f"unknown shape kind {shape.kind!r}"],
                }
                ctx = None
            details.append(entry)
            contexts.append(ctx)
            elapsed_ms.append(build_ms + (time.monotonic() - s_t0) * 1000)

        def _ready_indices() -> list[int]:
            return [
                i for i, ctx in enumerate(contexts)
                if ctx is not None and ctx["draft_info"] and not details[i]["errors"]
            ]

        if on_draft is not None:
            _run_indexed_step(
                _ready_indices(),
                lambda i: _run_on_draft_step(selected_shapes[i], details[i], contexts[i], on_draft),
                elapsed_ms,
            )

        if verify_light:
            for i in _ready_indices():
                elapsed_ms[i] += _run_light_step(selected_shapes[i], details[i], contexts[i], full=full)

        if full:
            _run_indexed_step(
                _ready_indices(),
                lambda i: _run_full_step(selected_shapes[i], details[i], contexts[i]),
                elapsed_ms,
            )

        for i, entry in enumerate(details):
            entry["status"] = "PASS" if not entry["errors"] else "FAIL"
            entry["duration_ms"] = int(elapsed_ms[i])

    total = len(details)
    passed = sum(1 for item in details if item["status"] == "PASS")
    duration_ms = int((time.monotonic() - t0) * 1000)

    if report_path is not None:
        out = Path(report_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": SCHEMA,
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": "full" if full else "fast",
            "contract_rel": contract_rel,
            "passed": passed,
            "failed": total - passed,
            "total": total,
            "duration_ms": duration_ms,
            "shapes": details,
        }
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return passed, total, details


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify synthetic repository corpus against init and light gates.")
    parser.add_argument("--contract-rel", default=".")
    parser.add_argument("--stacks", default="stacks")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--no-light", action="store_true")
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)

    full = bool(args.full or os.environ.get("CORPUS_FULL", "").strip() in ("1", "true", "yes"))
    passed, total, details = run(
        contract_rel=args.contract_rel,
        stacks_dir=args.stacks,
        report_path=args.report,
        verify_light=not args.no_light,
        full=full,
    )
    if passed != total:
        for item in details:
            if item["status"] != "PASS":
                print(f"{item['name']}: {'; '.join(item['errors'])}", file=sys.stderr)
        print(f"corpus: {passed}/{total} shapes verified ({total - passed} failed)")
        return 1
    print(f"corpus: {passed}/{total} shapes verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
