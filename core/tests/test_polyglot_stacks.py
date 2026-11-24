"""Specification tests for ADR 0129 polyglot stacks (dotnet, kotlin, swift, php, ruby).

Strictly vendor-free, workload-free, and model-free: exercises temporary
synthetic repositories and zero-dependency stdlib SAST and mutation runners.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from core import init, stacks

ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return root


class DotnetInferenceTest(unittest.TestCase):
    def test_csproj_with_packages_lock_json_and_tfm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Orders.csproj": (
                        '<Project Sdk="Microsoft.NET.Sdk">\n'
                        "  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n"
                        "</Project>\n"
                    ),
                    "packages.lock.json": '{"version": 1, "dependencies": {"net8.0": {}}}\n',
                    "src/OrderCalc.cs": "namespace Orders;\npublic static class OrderCalc {}\n",
                    "tests/OrderCalcTests.cs": "namespace Orders.Tests;\npublic class OrderCalcTests {}\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "dotnet")
            self.assertEqual(d["lockfiles"], "packages.lock.json")
            self.assertEqual(d["toolchain"], "mcr.microsoft.com/dotnet/sdk:8.0")
            self.assertEqual(d["test_cmd"], "dotnet test")
            self.assertEqual(d["scope"], "src/")
            globs = d["test_paths"].split(":")
            self.assertTrue(any(fnmatch.fnmatchcase("tests/OrderCalcTests.cs", g) for g in globs))
            self.assertTrue(any(fnmatch.fnmatchcase("Orders.Tests/OrderCalcTest.cs", g) for g in globs))
            self.assertFalse(any(fnmatch.fnmatchcase("src/OrderCalc.cs", g) for g in globs))

    def test_sln_with_central_package_management_and_net9(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Billing.sln": "Microsoft Visual Studio Solution File, Format Version 12.00\n",
                    "Directory.Packages.props": "<Project><ItemGroup /></Project>\n",
                    "src/Billing/Billing.csproj": (
                        '<Project Sdk="Microsoft.NET.Sdk">\n'
                        "  <PropertyGroup><TargetFrameworks>net8.0;net9.0</TargetFrameworks></PropertyGroup>\n"
                        "</Project>\n"
                    ),
                    "src/Billing/Ledger.cs": "namespace Billing;\npublic class Ledger {}\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "dotnet")
            self.assertEqual(d["lockfiles"], "Directory.Packages.props")
            self.assertEqual(d["toolchain"], "mcr.microsoft.com/dotnet/sdk:9.0")

    def test_dotnet_global_json_fallback_and_missing_lockfile_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Demo.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>netstandard2.0</TargetFramework></PropertyGroup></Project>\n',
                    "global.json": json.dumps({"sdk": {"version": "10.0.100"}}),
                    "packages.lock.json": '{"version": 1}\n',
                    "Calc.cs": "public class Calc {}\n",
                },
            )
            d = stacks.infer_dotnet(repo)
            self.assertEqual(d["toolchain"], "mcr.microsoft.com/dotnet/sdk:10.0")
            self.assertEqual(d["scope"], ".")

        with tempfile.TemporaryDirectory() as tmp:
            no_lock = _write(
                Path(tmp),
                {
                    "Demo.csproj": '<Project Sdk="Microsoft.NET.Sdk" />\n',
                    "src/Calc.cs": "public class Calc {}\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(no_lock)
            msg = str(ctx.exception)
            self.assertNotIn("\n", msg)
            self.assertIn("packages.lock.json", msg)
            self.assertIn("Directory.Packages.props", msg)


class KotlinInferenceTest(unittest.TestCase):
    def test_kotlin_gradle_kts_and_maven_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gradle_repo = _write(
                Path(tmp),
                {
                    "build.gradle.kts": 'plugins { kotlin("jvm") version "2.0.21" }\n',
                    "gradlew": "#!/bin/sh\n",
                    "gradle.lockfile": "empty=\n",
                    "src/main/kotlin/com/example/Calc.kt": "package com.example\nobject Calc\n",
                    "src/test/kotlin/com/example/CalcTest.kt": "package com.example\nclass CalcTest\n",
                },
            )
            d = init.infer(gradle_repo)
            self.assertEqual(d["stack"], "kotlin")
            self.assertEqual(d["lockfiles"], "gradle.lockfile")
            self.assertEqual(d["test_cmd"], "./gradlew test")
            self.assertEqual(d["scope"], "src/main/kotlin/")
            self.assertEqual(d["toolchain"], "docker.io/library/gradle:8-jdk21")
            globs = d["test_paths"].split(":")
            self.assertTrue(any(fnmatch.fnmatchcase("src/test/kotlin/com/example/CalcTest.kt", g) for g in globs))
            self.assertTrue(any(fnmatch.fnmatchcase("module/CalcTests.kt", g) for g in globs))
            self.assertFalse(any(fnmatch.fnmatchcase("src/main/kotlin/com/example/Calc.kt", g) for g in globs))

        with tempfile.TemporaryDirectory() as tmp:
            maven_repo = _write(
                Path(tmp),
                {
                    "pom.xml": (
                        "<project><build><plugins><plugin>"
                        "<groupId>org.jetbrains.kotlin</groupId>"
                        "<artifactId>kotlin-maven-plugin</artifactId>"
                        "</plugin></plugins></build></project>\n"
                    ),
                    "src/main/kotlin/Calc.kt": "object Calc\n",
                },
            )
            dm = init.infer(maven_repo)
            self.assertEqual(dm["stack"], "kotlin")
            self.assertEqual(dm["lockfiles"], "pom.xml")
            self.assertEqual(dm["test_cmd"], "mvn test")
            self.assertEqual(dm["toolchain"], "docker.io/library/maven:3.9-eclipse-temurin-21")

    def test_pure_java_maven_and_gradle_kts_remain_java(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pure_java_kts = _write(
                Path(tmp),
                {
                    "build.gradle.kts": "plugins {\n    java\n}\n",
                    "gradle.lockfile": "empty=\n",
                    "src/main/java/com/example/Calc.java": "package com.example;\nclass Calc {}\n",
                },
            )
            self.assertEqual(init.infer(pure_java_kts)["stack"], "java")

        with tempfile.TemporaryDirectory() as tmp:
            pure_java_pom = _write(
                Path(tmp),
                {
                    "pom.xml": "<project><modelVersion>4.0.0</modelVersion></project>\n",
                    "src/main/java/com/example/Calc.java": "package com.example;\nclass Calc {}\n",
                },
            )
            self.assertEqual(init.infer(pure_java_pom)["stack"], "java")

    def test_kotlin_gradle_missing_lockfile_raises_undraftable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            no_lock = _write(
                Path(tmp),
                {
                    "build.gradle.kts": 'plugins { kotlin("jvm") }\n',
                    "src/main/kotlin/Calc.kt": "object Calc\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(no_lock)
            self.assertIn("gradle.lockfile", str(ctx.exception))


class SwiftPhpRubyInferenceTest(unittest.TestCase):
    def test_swift_inference_and_missing_resolved_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "Package.swift": '// swift-tools-version: 6.0\nimport PackageDescription\nlet package = Package(name: "Calc")\n',
                    "Package.resolved": '{"pins": [], "version": 2}\n',
                    "Sources/Calc/Calc.swift": "public enum Calc {}\n",
                    "Tests/CalcTests/CalcTests.swift": "final class CalcTests {}\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "swift")
            self.assertEqual(d["lockfiles"], "Package.resolved")
            self.assertEqual(d["test_cmd"], "swift test")
            self.assertEqual(d["toolchain"], "docker.io/library/swift:6.0")
            self.assertEqual(d["scope"], "Sources/")

            (repo / "Package.resolved").unlink()
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(repo)
            self.assertIn("Package.resolved", str(ctx.exception))

    def test_php_inference_version_detection_and_missing_lock_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "composer.json": json.dumps({"name": "acme/calc", "require": {"php": "^8.4"}}),
                    "composer.lock": '{"packages": [], "packages-dev": []}\n',
                    "src/Calc.php": "<?php\nfinal class Calc {}\n",
                    "tests/CalcTest.php": "<?php\nfinal class CalcTest {}\n",
                },
            )
            d = init.infer(repo)
            self.assertEqual(d["stack"], "php")
            self.assertEqual(d["lockfiles"], "composer.lock")
            self.assertEqual(d["test_cmd"], "vendor/bin/phpunit")
            self.assertEqual(d["toolchain"], "docker.io/library/php:8.4-cli")
            self.assertEqual(d["scope"], "src/")

            (repo / "composer.lock").unlink()
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(repo)
            self.assertIn("composer.lock", str(ctx.exception))

    def test_ruby_rspec_vs_rake_version_and_missing_lock_refusal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rspec_repo = _write(
                Path(tmp),
                {
                    "Gemfile": 'source "https://rubygems.org"\n',
                    "Gemfile.lock": "GEM\n  specs:\n\nPLATFORMS\n  ruby\n",
                    ".ruby-version": "ruby-3.4.1\n",
                    "lib/calc.rb": "module Calc; end\n",
                    "spec/calc_spec.rb": "RSpec.describe Calc do; end\n",
                },
            )
            d = init.infer(rspec_repo)
            self.assertEqual(d["stack"], "ruby")
            self.assertEqual(d["lockfiles"], "Gemfile.lock")
            self.assertEqual(d["test_cmd"], "bundle exec rspec")
            self.assertEqual(d["toolchain"], "docker.io/library/ruby:3.4")
            self.assertEqual(d["scope"], "lib/")

        with tempfile.TemporaryDirectory() as tmp:
            rake_repo = _write(
                Path(tmp),
                {
                    "Gemfile": 'source "https://rubygems.org"\n',
                    "Gemfile.lock": "GEM\n  specs:\n\nPLATFORMS\n  ruby\n",
                    "lib/calc.rb": "module Calc; end\n",
                    "test/test_calc.rb": "class CalcTest; end\n",
                },
            )
            dr = init.infer(rake_repo)
            self.assertEqual(dr["test_cmd"], "bundle exec rake test")
            self.assertEqual(dr["toolchain"], "docker.io/library/ruby:3.3")

            (rake_repo / "Gemfile.lock").unlink()
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(rake_repo)
            self.assertIn("Gemfile.lock", str(ctx.exception))


class PolyglotMonorepoTest(unittest.TestCase):
    def test_polyglot_multi_service_discovery_and_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _write(
                Path(tmp),
                {
                    "services/billing/Billing.csproj": '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>\n',
                    "services/billing/packages.lock.json": '{"version": 1}\n',
                    "services/billing/src/Billing.cs": "public class Billing {}\n",
                    "services/notifier/Gemfile": 'source "https://rubygems.org"\n',
                    "services/notifier/Gemfile.lock": "GEM\n  specs:\n",
                    "services/notifier/lib/notifier.rb": "module Notifier; end\n",
                },
            )
            with self.assertRaises(init.Undraftable) as ctx:
                init.infer(repo)
            self.assertIn("INIT_SERVICE", str(ctx.exception))
            self.assertIn("services/billing", str(ctx.exception))
            self.assertIn("services/notifier", str(ctx.exception))

            d_cs = init.infer(repo, service="services/billing")
            self.assertEqual(d_cs["stack"], "dotnet")
            self.assertEqual(d_cs["test_root"], "services/billing/")
            self.assertEqual(d_cs["lockfiles"], "services/billing/packages.lock.json")

            d_rb = init.infer(repo, service="services/notifier")
            self.assertEqual(d_rb["stack"], "ruby")
            self.assertEqual(d_rb["test_root"], "services/notifier/")
            self.assertEqual(d_rb["lockfiles"], "services/notifier/Gemfile.lock")


class PolyglotRunnersTest(unittest.TestCase):
    def test_all_five_sast_runners_detect_findings_and_honor_nosec(self) -> None:
        cases = [
            (
                "dotnet",
                "src/Clean.cs",
                'public class Clean { public int Add(int a, int b) => a + b; /* MD5 password */ }\n',
                "src/Bad.cs",
                (
                    "using System.Diagnostics;\n"
                    "public class Bad {\n"
                    "  public void Run(string cmd, string user) {\n"
                    '    string secret = "hardcoded-demo-value";\n'
                    "    Process.Start(cmd);\n"
                    '    var q = $"SELECT * FROM users WHERE u = {user}";\n'
                    "    var h = MD5.Create();\n"
                    "    var bf = new BinaryFormatter();\n"
                    "    var h2 = SHA1.Create(); // nosec\n"
                    "  }\n"
                    "}\n"
                ),
                9,
            ),
            (
                "kotlin",
                "src/Clean.kt",
                'object Clean { fun add(a: Int, b: Int) = a + b /* MD5 secret */ }\n',
                "src/Bad.kt",
                (
                    "object Bad {\n"
                    "  fun run(cmd: String, user: String, s: java.io.InputStream) {\n"
                    '    val secret = "hardcoded-demo-value"\n'
                    "    Runtime.getRuntime().exec(cmd)\n"
                    '    val sql = "SELECT * FROM users WHERE name = $user"\n'
                    '    MessageDigest.getInstance("MD5")\n'
                    "    ObjectInputStream(s)\n"
                    '    MessageDigest.getInstance("SHA-1") // nosec\n'
                    "  }\n"
                    "}\n"
                ),
                8,
            ),
            (
                "swift",
                "src/Clean.swift",
                'public enum Clean { public static func add(_ a: Int, _ b: Int) -> Int { a + b } // password }\n',
                "src/Bad.swift",
                (
                    "public enum Bad {\n"
                    "  public static func run(_ user: String, _ p: Process, _ d: Data) {\n"
                    '    let secret = "hardcoded-demo-value"\n'
                    '    p.launchPath = "/bin/sh"\n'
                    '    let q = "SELECT * FROM users WHERE id = \\(user)"\n'
                    "    _ = Insecure.MD5.hash(data: d)\n"
                    "    _ = NSKeyedUnarchiver.unarchiveObject(with: d)\n"
                    "    _ = Insecure.SHA1.hash(data: d) // nosec\n"
                    "  }\n"
                    "}\n"
                ),
                8,
            ),
            (
                "php",
                "src/Clean.php",
                "<?php\nfinal class Clean { public function add(int $a, int $b): int { return $a + $b; } /* eval md5 */ }\n",
                "src/Bad.php",
                (
                    "<?php\n"
                    "function bad(string $cmd, string $user, string $raw): void {\n"
                    '  $secret = "hardcoded-demo-value";\n'
                    "  shell_exec($cmd);\n"
                    '  $sql = "SELECT * FROM users WHERE u = $user";\n'
                    "  md5($raw);\n"
                    "  unserialize($raw);\n"
                    "  sha1($raw); // nosec\n"
                    "}\n"
                ),
                8,
            ),
            (
                "ruby",
                "src/clean.rb",
                "module Clean\n  def self.add(a, b)\n    a + b # Marshal.load password\n  end\nend\n",
                "src/bad.rb",
                (
                    "module Bad\n"
                    "  def self.run(cmd, user, raw)\n"
                    '    secret = "hardcoded-demo-value"\n'
                    "    system(cmd)\n"
                    '    sql = "SELECT * FROM users WHERE u = #{user}"\n'
                    "    Digest::MD5.hexdigest(raw)\n"
                    "    Marshal.load(raw)\n"
                    "    Digest::SHA1.hexdigest(raw) # nosec\n"
                    "  end\n"
                    "end\n"
                ),
                8,
            ),
        ]
        for stack_name, clean_rel, clean_src, bad_rel, bad_src, nosec_line in cases:
            with self.subTest(stack=stack_name), tempfile.TemporaryDirectory() as tmp:
                sast_sh = ROOT / "stacks" / stack_name / "runners" / "sast.sh"
                repo = _write(Path(tmp), {clean_rel: clean_src, bad_rel: bad_src})
                proc = subprocess.run(
                    ["sh", str(sast_sh), "src"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                payload = json.loads(proc.stdout.strip())
                self.assertEqual(payload["findings"], len(payload["items"]))
                self.assertGreaterEqual(payload["findings"], 5, f"{stack_name}: {payload}")
                self.assertTrue(all(i["file"] == bad_rel for i in payload["items"]))
                self.assertNotIn(nosec_line, [i["line"] for i in payload["items"]])

                (repo / bad_rel).unlink()
                zero = subprocess.run(
                    ["sh", str(sast_sh), "src"],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(zero.returncode, 0, zero.stderr)
                self.assertEqual(json.loads(zero.stdout.strip()), {"findings": 0, "items": []})

    def test_all_five_mutation_runners_score_mutants_and_refuse_red_suite(self) -> None:
        samples = [
            (
                "dotnet",
                "src/Calc.cs",
                "public class Calc {\n  public static int Add(int a, int b) => a + b;\n  public static bool Flag() => true;\n}\n",
                "a + b",
                "src/Calc.cs=2-3",
            ),
            (
                "kotlin",
                "src/Calc.kt",
                "object Calc {\n  fun add(a: Int, b: Int): Int = a + b\n  fun flag(): Boolean = true\n}\n",
                "a + b",
                "src/Calc.kt=2-3",
            ),
            (
                "swift",
                "src/Calc.swift",
                "public enum Calc {\n  public static func add(_ a: Int, _ b: Int) -> Int { a + b }\n  public static func flag() -> Bool { true }\n}\n",
                "a + b",
                "src/Calc.swift=2-3",
            ),
            (
                "php",
                "src/Calc.php",
                "<?php\nfunction add(int $a, int $b): int { return $a + $b; }\nfunction flag(): bool { return true; }\n",
                "$a + $b",
                "src/Calc.php=2-3",
            ),
            (
                "ruby",
                "src/calc.rb",
                "module Calc\n  def self.add(a, b); a + b; end\n  def self.flag; true; end\nend\n",
                "a + b",
                "src/calc.rb=2-3",
            ),
        ]
        for stack_name, rel, src, expected_expr, mut_lines in samples:
            with self.subTest(stack=stack_name), tempfile.TemporaryDirectory() as tmp:
                mut_sh = ROOT / "stacks" / stack_name / "runners" / "mutation.sh"
                repo = _write(
                    Path(tmp),
                    {
                        rel: src,
                        "check.py": (
                            "import pathlib, sys\n"
                            f"txt = pathlib.Path({rel!r}).read_text(encoding='utf-8')\n"
                            f"if {expected_expr!r} not in txt:\n"
                            "    sys.exit(1)\n"
                        ),
                    },
                )
                env = {**os.environ, "MUTATION_LINES": mut_lines}
                proc = subprocess.run(
                    ["sh", str(mut_sh), "src", "python3 check.py"],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                score = json.loads(proc.stdout.strip())
                self.assertEqual(score["total"], 2, f"{stack_name}: {score}")
                self.assertEqual(score["killed"], 1)
                self.assertEqual(score["survived"], 1)
                self.assertEqual(score["score"], 0.5)
                self.assertIn(f"SURVIVOR {rel}:3", proc.stderr)

                red = subprocess.run(
                    ["sh", str(mut_sh), "src", "python3 -c 'import sys; sys.exit(1)'"],
                    cwd=repo,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(red.returncode, 0)


if __name__ == "__main__":
    unittest.main()
