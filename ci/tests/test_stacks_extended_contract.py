"""Contract and integration tests for ADR 0110 (Rust, Java, monorepo, stateful plane)."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from core import init, light

ROOT = Path(__file__).resolve().parents[2]


class ExtendedRunnersAndWorkloadsContractTest(unittest.TestCase):
    def _run_workloads_check(self, repo: Path, contract: Path) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE") or "localhost/lights-out-workbench:test",
            "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE") or "localhost/lights-out-workbench:test",
            "WORKLOAD_CONTRACT": str(contract),
        }
        return subprocess.run(
            ["sh", "ci/workloads.sh", str(repo)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_rust_and_java_runners_pass_shellcheck(self) -> None:
        if not shutil.which("shellcheck"):
            self.fail("shellcheck not installed outside workbench")
        scripts = [
            ROOT / "stacks/rust/runners/sast.sh",
            ROOT / "stacks/rust/runners/mutation.sh",
            ROOT / "stacks/java/runners/sast.sh",
            ROOT / "stacks/java/runners/mutation.sh",
        ]
        for s in scripts:
            self.assertTrue(s.is_file(), f"missing {s}")
        proc = subprocess.run(
            ["shellcheck", "-s", "sh", *map(str, scripts)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_minimal_rust_workload_contract_runners_and_osv(self) -> None:
        wl = ROOT / "workloads/minimal-rust"
        for rel in (
            "Cargo.toml",
            "Cargo.lock",
            "src/lib.rs",
            "tests/calc_test.rs",
            ".lights-out/workload.env",
            ".lights-out/worker.md",
            ".lights-out/policy.env",
        ):
            self.assertTrue((wl / rel).is_file(), f"missing workloads/minimal-rust/{rel}")

        diff = subprocess.run(
            ["diff", "-r", "-x", "__pycache__", str(ROOT / "stacks/rust/runners"), str(wl / ".lights-out/runners")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)

        w = self._run_workloads_check(wl, wl / ".lights-out")
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        self.assertEqual(w.stdout.strip(), "GO: workloads")

        sast = subprocess.run(
            ["sh", ".lights-out/runners/sast.sh", "src"],
            cwd=wl,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(sast.returncode, 0, sast.stderr)
        self.assertEqual(json.loads(sast.stdout.strip())["findings"], 0)

        if shutil.which("osv-scanner"):
            osv = subprocess.run(
                ["osv-scanner", "scan", "source", "--format", "json", "-L", str(wl / "Cargo.lock")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn(osv.returncode, (0, 128), osv.stderr)

    def test_minimal_java_workload_contract_runners_and_osv(self) -> None:
        wl = ROOT / "workloads/minimal-java"
        for rel in (
            "pom.xml",
            "Containerfile",
            "src/main/java/com/example/Calc.java",
            "src/test/java/com/example/CalcTest.java",
            ".lights-out/workload.env",
            ".lights-out/compose.yaml",
            ".lights-out/flags/flags.json",
            ".lights-out/smoke.sh",
            ".lights-out/load.sh",
            ".lights-out/worker.md",
            ".lights-out/policy.env",
        ):
            self.assertTrue((wl / rel).is_file(), f"missing workloads/minimal-java/{rel}")

        diff = subprocess.run(
            ["diff", "-r", "-x", "__pycache__", str(ROOT / "stacks/java/runners"), str(wl / ".lights-out/runners")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(diff.returncode, 0, diff.stdout + diff.stderr)

        w = self._run_workloads_check(wl, wl / ".lights-out")
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        self.assertEqual(w.stdout.strip(), "GO: workloads")

        sast = subprocess.run(
            ["sh", ".lights-out/runners/sast.sh", "src/main"],
            cwd=wl,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(sast.returncode, 0, sast.stderr)
        self.assertEqual(json.loads(sast.stdout.strip())["findings"], 0)

        if shutil.which("osv-scanner"):
            osv = subprocess.run(
                ["osv-scanner", "scan", "source", "--format", "json", "-L", str(wl / "pom.xml")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertIn(osv.returncode, (0, 128), osv.stderr)

    def test_stateful_draft_and_auth_smoke_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "svc"
            repo.mkdir()
            (repo / "pyproject.toml").write_text('[project]\nname = "svc"\nversion = "0.1.0"\n', encoding="utf-8")
            (repo / "uv.lock").write_text("version = 1\n", encoding="utf-8")
            (repo / "svc").mkdir()
            (repo / "svc/__init__.py").write_text("def ok(): return True\n", encoding="utf-8")
            (repo / "tests").mkdir()
            (repo / "tests/test_svc.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
            (repo / "Containerfile").write_text(
                "FROM python:3.13-slim\nEXPOSE 8000\nHEALTHCHECK CMD curl -f http://localhost:8000/healthz\n",
                encoding="utf-8",
            )
            (repo / ".env.example").write_text(
                "DATABASE_URL=postgresql://app:pass@db:5432/app\nREDIS_URL=redis://cache:6379/0\n",
                encoding="utf-8",
            )
            contract = repo / ".lights-out"
            stack = init.write_draft(str(repo), str(contract), str(ROOT / "stacks"), "2026-11-24", "abc1234")
            self.assertEqual(stack, "python")
            init.set_baseline(str(contract / "workload.env"), "1.0", "test")

            if shutil.which("docker-compose"):
                w = self._run_workloads_check(repo, contract)
                self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
                self.assertEqual(w.stdout.strip(), "GO: workloads")

            if shutil.which("shellcheck"):
                sc = subprocess.run(
                    ["shellcheck", "-s", "sh", str(contract / "smoke.sh"), str(contract / "load.sh")],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)

            class AuthHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    return

                def do_GET(self):  # noqa: N802
                    if self.headers.get("Authorization") == "Bearer smoke-tok":
                        self.send_response(200)
                    else:
                        self.send_response(401)
                    self.end_headers()

            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AuthHandler)
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                url = f"http://127.0.0.1:{srv.server_port}"
                unauth = subprocess.run(
                    ["sh", str(contract / "smoke.sh")],
                    env={**os.environ, "APP_URL": url},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(unauth.returncode, 0)
                auth = subprocess.run(
                    ["sh", str(contract / "smoke.sh")],
                    env={**os.environ, "APP_URL": url, "SMOKE_AUTH_HEADER": "Authorization: Bearer smoke-tok"},
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(auth.returncode, 0, auth.stderr)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_monorepo_init_service_draft_passes_workloads_and_light_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "mono"
            repo.mkdir()
            (repo / "package.json").write_text(
                json.dumps({"name": "mono", "private": True, "workspaces": ["services/*"]}),
                encoding="utf-8",
            )
            api = repo / "services/api"
            (api / "src").mkdir(parents=True)
            (api / "test").mkdir(parents=True)
            (api / "package.json").write_text(
                json.dumps({"name": "api", "type": "module", "scripts": {"test": "node --test"}}),
                encoding="utf-8",
            )
            (api / "package-lock.json").write_text(
                json.dumps({"name": "api", "version": "1.0.0", "lockfileVersion": 3, "packages": {"": {"name": "api"}}}),
                encoding="utf-8",
            )
            (api / "src/calc.js").write_text("export function add(a, b) { return a + b; }\n", encoding="utf-8")
            (api / "test/calc.test.js").write_text(
                'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                'import { add } from "../src/calc.js";\ntest("add", () => { assert.equal(add(1, 2), 3); });\n',
                encoding="utf-8",
            )
            web = repo / "services/web"
            (web / "src").mkdir(parents=True)
            (web / "package.json").write_text(json.dumps({"name": "web"}), encoding="utf-8")
            (web / "src/index.js").write_text("export const w = 1;\n", encoding="utf-8")

            env_git = {
                **os.environ,
                "GIT_AUTHOR_NAME": "test",
                "GIT_AUTHOR_EMAIL": "t@localhost.invalid",
                "GIT_COMMITTER_NAME": "test",
                "GIT_COMMITTER_EMAIL": "t@localhost.invalid",
            }
            subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], env=env_git, check=True)
            subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env_git, check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "seed"], env=env_git, check=True)

            contract = repo / ".lights-out"
            stack = init.write_draft(str(repo), str(contract), str(ROOT / "stacks"), "2026-11-24", "abc", service="services/api")
            self.assertEqual(stack, "javascript")
            init.set_baseline(str(contract / "workload.env"), "1.0", "test")

            if shutil.which("docker-compose"):
                w = self._run_workloads_check(repo, contract)
                self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
                self.assertEqual(w.stdout.strip(), "GO: workloads")

            subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env_git, check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "contract"], env=env_git, check=True)
            base = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

            (api / "src/calc.js").write_text(
                "export function add(a, b) { return a + b; }\nexport function mul(a, b) { return a * b; }\n",
                encoding="utf-8",
            )
            (api / "test/calc.test.js").write_text(
                'import test from "node:test";\nimport assert from "node:assert/strict";\n'
                'import { add, mul } from "../src/calc.js";\n'
                'test("add", () => { assert.equal(add(1, 2), 3); });\n'
                'test("mul", () => { assert.equal(mul(2, 3), 6); });\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(repo), "add", "-A"], env=env_git, check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "mul"], env=env_git, check=True)
            head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()

            old_cwd = os.getcwd()
            try:
                os.chdir(repo)
                line, rc, _ = light.judge([f"BASE={base}", f"HEAD={head}", "TEST_ON_HOST=1"])
                self.assertEqual(rc, 0, line)
                self.assertTrue(line.startswith("GO: gates-light"), line)
            finally:
                os.chdir(old_cwd)

    def test_vendor_free_word_boundary_allows_cargo_and_rejects_vendor(self) -> None:
        live = subprocess.run(["sh", "ci/vendor-free.sh"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(live.returncode, 0, live.stdout + live.stderr)
        self.assertEqual(live.stdout.strip(), "GO: vendor-free")

        with tempfile.TemporaryDirectory() as tmp:
            troot = Path(tmp)
            (troot / "ci").mkdir()
            (troot / "core").mkdir()
            shutil.copy2(ROOT / "ci/vendor-free.sh", troot / "ci/vendor-free.sh")
            shutil.copy2(ROOT / "ci/lib.sh", troot / "ci/lib.sh")

            rust_file = troot / "core/rust.py"
            rust_file.write_text('MANIFEST = "Cargo.toml"\nLOCK = "Cargo.lock"\nCMD = "cargo test"\n', encoding="utf-8")
            neg = subprocess.run(["sh", "ci/vendor-free.sh"], cwd=troot, capture_output=True, text=True, check=False)
            self.assertEqual(neg.returncode, 0, neg.stdout + neg.stderr)
            self.assertEqual(neg.stdout.strip(), "GO: vendor-free")

            for token in ("argo", "argo-rollouts", "argocd", "ARGO_NAMESPACE", "my_argo"):
                rust_file.write_text(f'X = "{token}"\n', encoding="utf-8")
                pos = subprocess.run(["sh", "ci/vendor-free.sh"], cwd=troot, capture_output=True, text=True, check=False)
                self.assertEqual(pos.returncode, 1, f"{token} unexpectedly passed: {pos.stdout}")
                self.assertTrue(pos.stdout.startswith("NO-GO: vendor-free"), pos.stdout)


if __name__ == "__main__":
    unittest.main()
