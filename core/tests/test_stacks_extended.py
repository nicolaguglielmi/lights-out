"""Specification tests for ADR 0110: Rust, Java/Kotlin, JS managers, monorepo, stateful plane.

Strictly vendor-free, app-free, and model-free: uses temporary directories
only and never names delivery/flag/code-host vendors, model IDs, or repo
contract directory literals.
"""

from __future__ import annotations

import fnmatch
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import init, init_plane, stacks

ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


class RustInferenceTest(unittest.TestCase):
    def test_infer_rust_with_src_and_cargo_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\nedition = "2021"\n',
                    "Cargo.lock": 'version = 3\n\n[[package]]\nname = "demo"\nversion = "0.1.0"\n',
                    "src/lib.rs": "pub fn add(a: i32, b: i32) -> i32 { a + b }\n",
                    "tests/calc_test.rs": "#[test]\nfn adds() { assert_eq!(demo::add(1, 2), 3); }\n",
                },
            )
            d = stacks.infer_rust(repo)
            self.assertEqual(d["stack"], "rust")
            self.assertEqual(d["toolchain"], "docker.io/library/rust:1.85")
            self.assertIn("digest", d["toolchain_evidence"].lower())
            self.assertEqual(d["lockfiles"], "Cargo.lock")
            self.assertEqual(d["scope"], "src/")
            self.assertEqual(d["test_root"], "")
            self.assertEqual(d["test_cmd"], "cargo test")
            self.assertIn("sast.sh src", d["sast_cmd"])
            self.assertIn('mutation.sh src "cargo test"', d["mutation_cmd"])

    def test_rust_scope_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                    "Cargo.lock": "version = 3\n",
                    "lib/main.rs": "fn main() {}\n",
                },
            )
            self.assertEqual(stacks.infer_rust(repo)["scope"], "lib/")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                    "Cargo.lock": "version = 3\n",
                    "lib.rs": "pub fn ok() -> bool { true }\n",
                },
            )
            d = stacks.infer_rust(repo)
            self.assertEqual(d["scope"], ".")
            self.assertIn("sast.sh .", d["sast_cmd"])

    def test_rust_missing_cargo_lock_raises_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                    "src/lib.rs": "pub fn ok() -> bool { true }\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                stacks.infer_rust(repo)
            msg = str(ctx.exception)
            self.assertIn("Cargo.lock", msg)
            self.assertIn("cargo generate-lockfile", msg)

    def test_rust_test_paths_match_only_rust_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[package]\nname = "demo"\nversion = "0.1.0"\n',
                    "Cargo.lock": "version = 3\n",
                    "src/lib.rs": "pub fn ok() -> bool { true }\n",
                },
            )
            globs = stacks.infer_rust(repo)["test_paths"].split(":")
            for p in ("tests/calc_test.rs", "tests/api/health.rs", "src/calc_test.rs"):
                self.assertTrue(any(fnmatch.fnmatchcase(p, g) for g in globs), p)
            for p in ("src/lib.rs", "src/main.rs", "src/testing_helper.rs"):
                self.assertFalse(any(fnmatch.fnmatchcase(p, g) for g in globs), p)


class JavaKotlinInferenceTest(unittest.TestCase):
    def test_infer_maven_with_pom_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
                    "src/main/java/com/example/Calc.java": "package com.example;\nclass Calc {}\n",
                    "src/test/java/com/example/CalcTest.java": "package com.example;\nclass CalcTest {}\n",
                },
            )
            d = stacks.infer_java(repo)
            self.assertEqual(d["stack"], "java")
            self.assertEqual(d["toolchain"], "docker.io/library/maven:3.9-eclipse-temurin-21")
            self.assertEqual(d["lockfiles"], "pom.xml")
            self.assertEqual(d["scope"], "src/main/")
            self.assertEqual(d["test_cmd"], "mvn test")
            self.assertIn("sast.sh src/main", d["sast_cmd"])
            self.assertIn('mutation.sh src/main "mvn test"', d["mutation_cmd"])

    def test_infer_maven_uses_mvnw_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
                    "mvnw": "#!/bin/sh\n",
                    "src/main/java/com/example/Calc.java": "class Calc {}\n",
                },
            )
            d = stacks.infer_java(repo)
            self.assertEqual(d["test_cmd"], "./mvnw test")
            self.assertIn('mutation.sh src/main "./mvnw test"', d["mutation_cmd"])

    def test_infer_gradle_with_lockfile_and_kotlin_dsl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "build.gradle": "plugins { id 'java' }\n",
                    "gradle.lockfile": "empty=\n",
                    "src/main/java/App.java": "class App {}\n",
                },
            )
            d = stacks.infer_java(repo)
            self.assertEqual(d["stack"], "java")
            self.assertEqual(d["toolchain"], "docker.io/library/gradle:8-jdk21")
            self.assertEqual(d["lockfiles"], "gradle.lockfile")
            self.assertEqual(d["test_cmd"], "gradle test")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "build.gradle.kts": 'plugins { kotlin("jvm") }\n',
                    "gradlew": "#!/bin/sh\n",
                    "gradle/verification-metadata.xml": "<verification-metadata><components/></verification-metadata>\n",
                    "src/main/kotlin/App.kt": "fun main() {}\n",
                },
            )
            d = stacks.infer_java(repo)
            self.assertEqual(d["lockfiles"], "gradle/verification-metadata.xml")
            self.assertEqual(d["test_cmd"], "./gradlew test")

    def test_gradle_missing_lockfile_raises_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "build.gradle": "plugins { id 'java' }\n",
                    "src/main/java/App.java": "class App {}\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                stacks.infer_java(repo)
            msg = str(ctx.exception)
            self.assertIn("gradle.lockfile", msg)
            self.assertIn("--write-locks", msg)

    def test_java_test_paths_match_java_and_kotlin_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "pom.xml": "<project/>\n",
                    "src/main/java/Calc.java": "class Calc {}\n",
                },
            )
            globs = stacks.infer_java(repo)["test_paths"].split(":")
            for p in (
                "src/test/java/com/example/CalcTest.java",
                "src/test/kotlin/com/example/CalcTest.kt",
                "src/test/java/SuiteTests.java",
            ):
                self.assertTrue(any(fnmatch.fnmatchcase(p, g) for g in globs), p)
            for p in (
                "src/main/java/com/example/Calc.java",
                "src/main/java/com/example/TestHelper.java",
                "src/main/kotlin/App.kt",
            ):
                self.assertFalse(any(fnmatch.fnmatchcase(p, g) for g in globs), p)


class JsPackageManagersTest(unittest.TestCase):
    def test_js_and_ts_pnpm_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "js-pnpm", "scripts": {"test": "node --test"}}),
                    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
                    "src/index.js": "export const x = 1;\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "javascript")
            self.assertEqual(d["lockfiles"], "pnpm-lock.yaml")
            self.assertEqual(d["toolchain"], "workbench")
            self.assertIn("pnpm", d["test_cmd"])
            self.assertIn("--frozen-lockfile", d["test_cmd"])
            self.assertIn("pnpm", d["mutation_cmd"])

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "ts-pnpm"}),
                    "tsconfig.json": "{}",
                    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
                    "src/index.ts": "export const x: number = 1;\n",
                },
            )
            d = stacks.infer_typescript(repo)
            self.assertEqual(d["stack"], "typescript")
            self.assertEqual(d["lockfiles"], "pnpm-lock.yaml")
            self.assertIn("pnpm", d["test_cmd"])
            self.assertIn("node --test", d["test_cmd"])

    def test_yarn_v1_and_berry_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "js-yarn", "scripts": {"test": "node --test"}}),
                    "yarn.lock": "# yarn lockfile v1\n",
                    "src/index.js": "export const x = 1;\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["lockfiles"], "yarn.lock")
            self.assertIn("yarn", d["test_cmd"])
            self.assertIn("--frozen-lockfile", d["test_cmd"])

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "ts-berry", "scripts": {"test": "node --test"}}),
                    "tsconfig.json": "{}",
                    "yarn.lock": "__metadata:\n  version: 8\n",
                    "src/index.ts": "export const x: number = 1;\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["lockfiles"], "yarn.lock")
            self.assertIn("--immutable", d["test_cmd"])

    def test_bun_text_lock_vs_unsupported_binary_lockb(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "ts-bun"}),
                    "tsconfig.json": "{}",
                    "bun.lock": '{"lockfileVersion": 1, "packages": {}}\n',
                    "src/index.ts": "export const x: number = 1;\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "typescript")
            self.assertEqual(d["lockfiles"], "bun.lock")
            self.assertEqual(d["toolchain"], "docker.io/oven/bun:1")
            self.assertIn("bun", d["test_cmd"])
            self.assertIn("--frozen-lockfile", d["test_cmd"])

        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "js-bun-bin"}),
                    "bun.lockb": "binary-lockb",
                    "src/index.js": "export const x = 1;\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(repo)
            msg = str(ctx.exception)
            self.assertIn("bun.lockb", msg)
            self.assertIn("bun.lock", msg)


class MonorepoDiscoveryTest(unittest.TestCase):
    def test_ambiguous_workspaces_raise_undraftable_with_service_hint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["services/*"]}),
                    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
                    "services/api/package.json": json.dumps({"name": "api"}),
                    "services/api/src/index.js": "export const a = 1;\n",
                    "services/web/package.json": json.dumps({"name": "web"}),
                    "services/web/src/index.js": "export const b = 2;\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(repo)
            msg = str(ctx.exception)
            self.assertIn("INIT_SERVICE", msg)
            self.assertIn("services/api", msg)
            self.assertIn("services/web", msg)

    def test_ambiguous_cargo_workspace_and_gradle_settings_raise_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cargo_repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
                    "Cargo.lock": "version = 3\n",
                    "crates/alpha/Cargo.toml": '[package]\nname = "alpha"\nversion = "0.1.0"\n',
                    "crates/alpha/src/lib.rs": "pub fn a() {}\n",
                    "crates/beta/Cargo.toml": '[package]\nname = "beta"\nversion = "0.1.0"\n',
                    "crates/beta/src/lib.rs": "pub fn b() {}\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(cargo_repo)
            msg = str(ctx.exception)
            self.assertIn("INIT_SERVICE", msg)
            self.assertIn("crates/alpha", msg)
            self.assertIn("crates/beta", msg)

        with tempfile.TemporaryDirectory() as tmp:
            gradle_repo = _write(
                Path(tmp),
                {
                    "settings.gradle": "rootProject.name = 'mono'\ninclude ':services:api', ':services:worker'\n",
                    "gradle.lockfile": "empty=\n",
                    "services/api/build.gradle": "plugins { id 'java' }\n",
                    "services/api/src/main/java/A.java": "class A {}\n",
                    "services/worker/build.gradle": "plugins { id 'java' }\n",
                    "services/worker/src/main/java/W.java": "class W {}\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(gradle_repo)
            msg = str(ctx.exception)
            self.assertIn("INIT_SERVICE", msg)
            self.assertIn("services/api", msg)
            self.assertIn("services/worker", msg)

    def test_init_service_selects_subservice_via_param_and_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "package.json": json.dumps({"name": "mono", "private": True, "workspaces": ["services/*"]}),
                    "pnpm-lock.yaml": "lockfileVersion: '9.0'\n",
                    "services/api/package.json": json.dumps({"name": "api", "scripts": {"test": "node --test"}}),
                    "services/api/src/index.js": "export const a = 1;\n",
                    "services/api/test/index.test.js": "import test from 'node:test';\n",
                    "services/web/package.json": json.dumps({"name": "web"}),
                    "services/web/src/index.js": "export const b = 2;\n",
                },
            )
            d = init.infer(repo, service="services/api")
            self.assertEqual(d["stack"], "javascript")
            self.assertEqual(d["test_root"], "services/api/")
            self.assertEqual(d["scope"], "services/api/src/")
            self.assertEqual(d["lockfiles"], "pnpm-lock.yaml")
            self.assertTrue(all(p.startswith("services/api/") for p in d["test_paths"].split(":")))
            self.assertIn("../../", d["sast_cmd"])
            self.assertNotIn("$", d["sast_cmd"])
            self.assertNotIn("#", d["sast_cmd"])
            self.assertIn("../../", d["mutation_cmd"])

            old = os.environ.get("INIT_SERVICE")
            try:
                os.environ["INIT_SERVICE"] = "services/api"
                d_env = init.infer(repo)
                self.assertEqual(d_env["test_root"], "services/api/")
                self.assertEqual(d_env["scope"], "services/api/src/")
            finally:
                if old is None:
                    os.environ.pop("INIT_SERVICE", None)
                else:
                    os.environ["INIT_SERVICE"] = old

    def test_cargo_workspace_member_inherits_root_lockfile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
                    "Cargo.lock": "version = 3\n",
                    "crates/api/Cargo.toml": '[package]\nname = "api"\nversion = "0.1.0"\n',
                    "crates/api/src/lib.rs": "pub fn ok() -> bool { true }\n",
                    "crates/other/Cargo.toml": '[package]\nname = "other"\nversion = "0.1.0"\n',
                    "crates/other/src/lib.rs": "pub fn other() -> bool { true }\n",
                },
            )
            d = init.infer(repo, service="crates/api")
            self.assertEqual(d["stack"], "rust")
            self.assertEqual(d["lockfiles"], "Cargo.lock")
            self.assertEqual(d["test_root"], "crates/api/")
            self.assertEqual(d["scope"], "crates/api/src/")
            self.assertIn("../../", d["sast_cmd"])

    def test_init_service_rejects_traversal_and_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(Path(tmp), {"Cargo.toml": '[package]\nname="a"\n', "Cargo.lock": "version=3\n", "src/lib.rs": ""})
            for bad in ("../outside", "/etc", "missing/svc", "svc$bad"):
                with self.subTest(bad=bad):
                    with self.assertRaises(init.Undraftable):
                        init.infer(repo, service=bad)


class StatefulPlaneInitTest(unittest.TestCase):
    def test_detect_container_under_init_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "services/api/Dockerfile": "FROM rust:1.85\nEXPOSE 9000\nHEALTHCHECK CMD curl -f http://localhost:9000/healthz\n",
                },
            )
            self.assertIsNone(init_plane.detect_container(repo))
            info = init_plane.detect_container(repo, service="services/api")
            self.assertIsNotNone(info)
            self.assertEqual(info["dockerfile"], "services/api/Dockerfile")
            self.assertEqual(info["port"], 9000)
            self.assertEqual(info["health_probe"], "http:/healthz")

    def test_stateful_deps_positive_and_comment_only_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pos = _write(
                Path(tmp),
                {
                    "Containerfile": "FROM python:3.13-slim\nEXPOSE 8000\n",
                    ".env.example": "DATABASE_URL=postgresql://user:pass@localhost:5432/app\nREDIS_URL=redis://localhost:6379/0\n",
                    "docker-compose.yml": "services:\n  cache:\n    image: redis:7\n  mysql:\n    image: mysql:8\n",
                },
            )
            out_pos = Path(tmp) / "out_pos"
            info_pos = init_plane.detect_container(pos)
            init_plane.draft_plane(pos, out_pos, info_pos, {})
            compose_pos = (out_pos / "compose.yaml").read_text(encoding="utf-8")
            self.assertIn("crew-postgres", compose_pos)
            self.assertIn("crew-redis", compose_pos)
            self.assertIn("crew-mysql", compose_pos)
            self.assertIn("x-crew-worker", compose_pos)
            self.assertIn("192.0.2.1", compose_pos)
            self.assertIn("migrate", compose_pos)

            neg = _write(
                Path(tmp) / "neg",
                {
                    "Containerfile": "FROM python:3.13-slim\nEXPOSE 8000\n",
                    ".env.example": "# DATABASE_URL=postgresql://localhost/unused\n# REDIS_URL=redis://localhost\nAPP_ENV=dev\n",
                },
            )
            out_neg = Path(tmp) / "out_neg"
            info_neg = init_plane.detect_container(neg)
            init_plane.draft_plane(neg, out_neg, info_neg, {})
            compose_neg = (out_neg / "compose.yaml").read_text(encoding="utf-8")
            self.assertNotIn("crew-postgres", compose_neg)
            self.assertNotIn("crew-redis", compose_neg)
            self.assertNotIn("crew-mysql", compose_neg)
            self.assertNotIn("x-crew-worker", compose_neg)

    def test_auth_smoke_load_and_commented_egress_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(Path(tmp) / "repo", {"Containerfile": "FROM python:3.13-slim\nEXPOSE 8000\n"})
            out = Path(tmp) / "out"
            info = init_plane.detect_container(repo)
            init_plane.draft_plane(repo, out, info, {})

            smoke_text = (out / "smoke.sh").read_text(encoding="utf-8")
            load_text = (out / "load.sh").read_text(encoding="utf-8")
            self.assertIn("SMOKE_AUTH_HEADER", smoke_text)
            self.assertIn("SMOKE_AUTH_HEADER", load_text)

            egress_example = out / "crew-egress.txt.example"
            self.assertTrue(egress_example.is_file())
            lines = egress_example.read_text(encoding="utf-8").splitlines()
            active = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
            self.assertEqual(active, [], f"crew-egress.txt.example must be commented out by default: {active}")
            self.assertTrue((out / "crew-egress.txt").exists() is False)


class RustAndJavaRunnersTest(unittest.TestCase):
    def test_rust_sast_runner_detects_findings_and_respects_nosec(self) -> None:
        sast_sh = ROOT / "stacks/rust/runners/sast.sh"
        self.assertTrue(sast_sh.is_file(), f"missing {sast_sh}")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "src/clean.rs": 'pub fn add(a: i32, b: i32) -> i32 { a + b }\n// comment mentioning unsafe password\nconst MSG: &str = "unsafe secret";\n',
                    "src/bad.rs": (
                        "use std::process::Command;\n"
                        "pub fn run(user: &str) {\n"
                        "    unsafe { std::ptr::null::<i32>(); }\n"
                        '    let api_key = "hardcoded-demo-value";\n'
                        '    let q = format!("SELECT * FROM t WHERE u = {}", user);\n'
                        '    let _ = Command::new("sh").arg("-c").arg(user);\n'
                        "    unsafe { std::ptr::null::<i32>(); } // nosec\n"
                        "    let _ = (api_key, q);\n"
                        "}\n"
                    ),
                },
            )
            clean = subprocess.run(["sh", str(sast_sh), "src"], cwd=repo, capture_output=True, text=True, check=False)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            payload = json.loads(clean.stdout.strip())
            rules = {item["rule"] for item in payload["items"]}
            self.assertEqual(payload["findings"], len(payload["items"]))
            self.assertGreaterEqual(payload["findings"], 4)
            self.assertTrue(all(item["file"] == "src/bad.rs" for item in payload["items"]))
            self.assertNotIn(7, [item["line"] for item in payload["items"]])
            self.assertTrue(rules)

            (repo / "src/bad.rs").unlink()
            zero = subprocess.run(["sh", str(sast_sh), "src"], cwd=repo, capture_output=True, text=True, check=False)
            self.assertEqual(zero.returncode, 0, zero.stderr)
            self.assertEqual(json.loads(zero.stdout.strip()), {"findings": 0, "items": []})

    def test_java_sast_runner_detects_findings_and_respects_nosec(self) -> None:
        sast_sh = ROOT / "stacks/java/runners/sast.sh"
        self.assertTrue(sast_sh.is_file(), f"missing {sast_sh}")
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "src/main/java/Clean.java": 'class Clean { int add(int a, int b) { return a + b; } /* MD5 password */ }\n',
                    "src/main/java/Bad.java": (
                        "import java.security.MessageDigest;\n"
                        "class Bad {\n"
                        "  void run(String cmd, String user) throws Exception {\n"
                        '    String password = "hardcoded-demo-value";\n'
                        "    Runtime.getRuntime().exec(cmd);\n"
                        '    String sql = "SELECT * FROM users WHERE name = " + user;\n'
                        '    MessageDigest.getInstance("MD5");\n'
                        '    MessageDigest.getInstance("SHA-1"); // nosec\n'
                        "  }\n"
                        "}\n"
                    ),
                },
            )
            proc = subprocess.run(["sh", str(sast_sh), "src/main"], cwd=repo, capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())
            self.assertEqual(payload["findings"], len(payload["items"]))
            self.assertGreaterEqual(payload["findings"], 4)
            self.assertTrue(all(item["file"] == "src/main/java/Bad.java" for item in payload["items"]))
            self.assertNotIn(8, [item["line"] for item in payload["items"]])

            (repo / "src/main/java/Bad.java").unlink()
            zero = subprocess.run(["sh", str(sast_sh), "src/main"], cwd=repo, capture_output=True, text=True, check=False)
            self.assertEqual(zero.returncode, 0, zero.stderr)
            self.assertEqual(json.loads(zero.stdout.strip()), {"findings": 0, "items": []})

    def test_rust_and_java_mutation_runners_scope_lines_and_score_mutants(self) -> None:
        rust_mut = ROOT / "stacks/rust/runners/mutation.sh"
        java_mut = ROOT / "stacks/java/runners/mutation.sh"
        self.assertTrue(rust_mut.is_file(), f"missing {rust_mut}")
        self.assertTrue(java_mut.is_file(), f"missing {java_mut}")

        # Rust mutation runner using a Python verifier acting as the test command.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "src/lib.rs": (
                        "pub fn add(a: i32, b: i32) -> i32 {\n"
                        "    a + b\n"
                        "}\n"
                        "pub fn ignored(x: i32) -> i32 {\n"
                        "    x * 2\n"
                        "}\n"
                    ),
                    "check.py": (
                        "import pathlib, sys\n"
                        'txt = pathlib.Path("src/lib.rs").read_text()\n'
                        'if "a + b" not in txt:\n'
                        "    sys.exit(1)\n"
                    ),
                },
            )
            env = {**os.environ, "MUTATION_LINES": "src/lib.rs=2-2"}
            proc = subprocess.run(
                ["sh", str(rust_mut), "src", "python3 check.py"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            score = json.loads(proc.stdout.strip())
            self.assertEqual(score["total"], 1)
            self.assertEqual(score["killed"], 1)
            self.assertEqual(score["score"], 1.0)

        # Java mutation runner with survivor + red-suite failure check.
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "src/main/java/Calc.java": (
                        "class Calc {\n"
                        "  static int add(int a, int b) { return a + b; }\n"
                        "  static boolean flag() { return true; }\n"
                        "}\n"
                    ),
                    "check.py": (
                        "import pathlib, sys\n"
                        'txt = pathlib.Path("src/main/java/Calc.java").read_text()\n'
                        'if "a + b" not in txt:\n'
                        "    sys.exit(1)\n"
                    ),
                },
            )
            env = {**os.environ, "MUTATION_LINES": "src/main/java/Calc.java=2-3"}
            proc = subprocess.run(
                ["sh", str(java_mut), "src/main", "python3 check.py"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            score = json.loads(proc.stdout.strip())
            self.assertEqual(score["total"], 2)
            self.assertEqual(score["killed"], 1)
            self.assertEqual(score["survived"], 1)
            self.assertEqual(score["score"], 0.5)
            self.assertIn("SURVIVOR src/main/java/Calc.java:3", proc.stderr)

            red = subprocess.run(
                ["sh", str(java_mut), "src/main", "python3 -c 'import sys; sys.exit(1)'"],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(red.returncode, 0)


if __name__ == "__main__":
    unittest.main()
