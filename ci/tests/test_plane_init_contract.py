"""Contract and Envoy-port specification tests for ADR 0100 (M66)."""

from __future__ import annotations

import http.server
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _make_attached_repo(root: Path, dockerfile_name: str, dockerfile_text: str) -> None:
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "demo").mkdir()
    (root / "demo" / "__init__.py").write_text("def ok(): return True\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_demo.py").write_text("def test_ok(): assert True\n", encoding="utf-8")
    (root / dockerfile_name).write_text(dockerfile_text, encoding="utf-8")


class WorkloadPortContractTest(unittest.TestCase):
    def _run_workloads_check(self, repo: Path, contract: Path) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:test"),
            "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE", "localhost/lights-out-workbench:test"),
            "WORKLOAD_CONTRACT": str(contract),
        }
        return subprocess.run(
            ["sh", "ci/workloads.sh", str(repo)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
        )

    def test_ci_workloads_accepts_valid_and_rejects_invalid_workload_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base_repo = Path(tmp) / "repo"
            contract = base_repo / ".lights-out"
            shutil.copytree(ROOT / "workloads/minimal-js", base_repo)
            env_path = contract / "workload.env"
            original = env_path.read_text(encoding="utf-8")

            for valid in ("", "1", "3000", "65535"):
                with self.subTest(valid=valid):
                    env_path.write_text(original + f"WORKLOAD_PORT={valid}\n", encoding="utf-8")
                    proc = self._run_workloads_check(base_repo, contract)
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertEqual(proc.stdout.strip(), "GO: workloads")

            for invalid in ("0", "65536", "abc", "08000", "8000/tcp", "-1"):
                with self.subTest(invalid=invalid):
                    env_path.write_text(original + f"WORKLOAD_PORT={invalid}\n", encoding="utf-8")
                    proc = self._run_workloads_check(base_repo, contract)
                    self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                    self.assertIn("WORKLOAD_PORT", proc.stdout)

    def test_drafted_plane_passes_ci_workloads_and_smoke_executes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _make_attached_repo(
                repo,
                "Dockerfile",
                "FROM python:3.13-slim\nEXPOSE 3000\nHEALTHCHECK CMD curl -f http://localhost:3000/ready || exit 1\n",
            )
            contract = repo / ".lights-out"
            proc = subprocess.run(
                ["python3", "core/init.py", "--repo", str(repo), "--out", str(contract), "--stacks", "stacks"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            set_bl = subprocess.run(
                ["python3", "core/init.py", "--set-baseline", str(contract / "workload.env"), "1.0", "test"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(set_bl.returncode, 0, set_bl.stdout + set_bl.stderr)

            if shutil.which("docker-compose"):
                wproc = self._run_workloads_check(repo, contract)
                self.assertEqual(wproc.returncode, 0, wproc.stdout + wproc.stderr)
                self.assertEqual(wproc.stdout.strip(), "GO: workloads")

            if shutil.which("shellcheck"):
                sc = subprocess.run(
                    ["shellcheck", "-s", "sh", str(contract / "smoke.sh"), str(contract / "load.sh")],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)

            # Exercise drafted smoke.sh against a real HTTP server.
            state = {"code": 204}

            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    return

                def do_GET(self):  # noqa: N802
                    if self.path == "/ready":
                        self.send_response(state["code"])
                    else:
                        self.send_response(404)
                    self.end_headers()

            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                url = f"http://127.0.0.1:{srv.server_port}"
                ok = subprocess.run(
                    ["sh", str(contract / "smoke.sh")],
                    env={**os.environ, "APP_URL": url},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)

                state["code"] = 500
                bad = subprocess.run(
                    ["sh", str(contract / "smoke.sh")],
                    env={**os.environ, "APP_URL": url},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(bad.returncode, 0)
            finally:
                srv.shutdown()
                srv.server_close()


class EnvoyWorkloadPortTest(unittest.TestCase):
    def test_compose_and_envoy_containerfile_wire_workload_port(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertIn("WORKLOAD_PORT", compose)
        self.assertIn("${WORKLOAD_PORT:-8000}", compose)

        cf = (ROOT / "adapters/envoy-canary/Containerfile").read_text(encoding="utf-8")
        self.assertIn("envoy --mode validate -c /etc/envoy/envoy.yaml", cf)
        self.assertIn("entrypoint.sh", cf)
        self.assertIn("ENTRYPOINT", cf)
        self.assertIn("CMD []", cf)

        ep = ROOT / "adapters/envoy-canary/entrypoint.sh"
        self.assertTrue(ep.is_file())
        if shutil.which("shellcheck"):
            sc = subprocess.run(["shellcheck", "-s", "sh", str(ep)], capture_output=True, text=True)
            self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)

    def test_entrypoint_substitutes_port_value_and_execs_envoy(self) -> None:
        ep = ROOT / "adapters/envoy-canary/entrypoint.sh"
        self.assertTrue(ep.is_file(), f"missing {ep}")
        envoy_yaml = (ROOT / "adapters/envoy-canary/envoy.yaml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bindir = t / "bin"
            bindir.mkdir()
            args_file = t / "args.txt"
            stub = bindir / "envoy"
            stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{args_file}"\n', encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

            src = t / "envoy.yaml"
            src.write_text(envoy_yaml, encoding="utf-8")
            dst = t / "out.yaml"

            # Default 8000
            env = {
                "PATH": f"{bindir}:{os.environ['PATH']}",
                "ENVOY_CONFIG_SRC": str(src),
                "ENVOY_CONFIG_DST": str(dst),
            }
            proc = subprocess.run(["sh", str(ep)], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(args_file.read_text(encoding="utf-8").splitlines(), ["-c", str(dst)])
            self.assertIn("port_value: 8000", dst.read_text(encoding="utf-8"))

            # Custom WORKLOAD_PORT=3000
            env["WORKLOAD_PORT"] = "3000"
            proc = subprocess.run(["sh", str(ep)], env=env, capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            rendered = dst.read_text(encoding="utf-8")
            self.assertNotIn("port_value: 8000", rendered)
            self.assertEqual(rendered.count("port_value: 3000"), 2)
            # Admin/listener ports stay untouched
            self.assertIn("port_value: 9901", rendered)
            self.assertIn("port_value: 8080", rendered)
            self.assertIn("port_value: 8081", rendered)

            # Invalid WORKLOAD_PORT fails loudly
            env["WORKLOAD_PORT"] = "notaport"
            bad = subprocess.run(["sh", str(ep)], env=env, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("WORKLOAD_PORT", bad.stderr)

    def test_deploy_and_ready_honor_workload_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            bindir = t / "bin"
            bindir.mkdir()
            calls = t / "calls.jsonl"
            dc = bindir / "docker-compose"
            dc.write_text(
                "#!/bin/sh\n"
                f'if [ "$1" = run ]; then printf \'%s\\n\' "$*" >> "{calls}"; exit 0; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            dc.chmod(dc.stat().st_mode | stat.S_IXUSR)

            # Stub curl for ready.sh ENVOY_ADMIN_URL/ready
            class AdminHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    return

                def do_GET(self):  # noqa: N802
                    self.send_response(200)
                    self.end_headers()

            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AdminHandler)
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                # ready.sh with WORKLOAD_HEALTH_PROBE=tcp and WORKLOAD_PORT=3456
                env = {
                    **os.environ,
                    "PATH": f"{bindir}:{os.environ['PATH']}",
                    "ENVOY_ADMIN_URL": f"http://127.0.0.1:{srv.server_port}",
                    "WORKLOAD_HEALTH_PROBE": "tcp",
                    "WORKLOAD_PORT": "3456",
                    "READY_TIMEOUT": "2",
                }
                proc = subprocess.run(
                    ["sh", "adapters/envoy-canary/ready.sh"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                recorded = calls.read_text(encoding="utf-8")
                self.assertIn("3456", recorded)

                # Explicit tcp:9123 overrides WORKLOAD_PORT
                calls.unlink()
                env["WORKLOAD_HEALTH_PROBE"] = "tcp:9123"
                proc = subprocess.run(
                    ["sh", "adapters/envoy-canary/ready.sh"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn("9123", calls.read_text(encoding="utf-8"))
                self.assertNotIn("3456", calls.read_text(encoding="utf-8"))

                # deploy.sh passes WORKLOAD_PORT=3456 to probe
                calls.unlink()
                denv = {
                    **os.environ,
                    "PATH": f"{bindir}:{os.environ['PATH']}",
                    "WORKLOAD_HEALTH_PROBE": "http:/ok",
                    "WORKLOAD_PORT": "3456",
                    "READY_TIMEOUT": "2",
                }
                dproc = subprocess.run(
                    ["sh", "adapters/envoy-canary/deploy.sh", "0.2.0-canary", "img@sha256:" + "0" * 64],
                    cwd=ROOT,
                    env=denv,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(dproc.returncode, 0, dproc.stdout + dproc.stderr)
                self.assertIn("3456", calls.read_text(encoding="utf-8"))
            finally:
                srv.shutdown()
                srv.server_close()


class PackageSummaryPlaneTest(unittest.TestCase):
    def test_entrypoint_lists_drafted_plane_files_in_summary(self) -> None:
        entry = (ROOT / "package/entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("compose.yaml", entry)
        self.assertIn("flags/flags.json", entry)
        self.assertIn("smoke.sh", entry)
        self.assertIn("load.sh", entry)

    def test_init_on_existing_plane_workload_preserves_flags_and_passes_ci_workloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "minimal-copy"
            shutil.copytree(ROOT / "workloads/minimal", repo)
            contract = repo / ".lights-out"

            proc = subprocess.run(
                ["python3", "core/init.py", "--repo", str(repo), "--out", str(contract), "--stacks", "stacks"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            flags = json.loads((contract / "flags/flags.json").read_text(encoding="utf-8"))
            self.assertIn("release-freeze", flags.get("flags", {}))
            self.assertIn("outage", flags.get("flags", {}))

            set_bl = subprocess.run(
                ["python3", "core/init.py", "--set-baseline", str(contract / "workload.env"), "1.0", "test"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(set_bl.returncode, 0, set_bl.stdout + set_bl.stderr)

            if shutil.which("docker-compose"):
                env = {
                    **os.environ,
                    "WORKBENCH_IMAGE": os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:test"),
                    "HARNESS_BASE_IMAGE": os.environ.get("HARNESS_BASE_IMAGE", "localhost/lights-out-workbench:test"),
                    "WORKLOAD_CONTRACT": str(contract),
                }
                wproc = subprocess.run(
                    ["sh", "ci/workloads.sh", str(repo)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(wproc.returncode, 0, wproc.stdout + wproc.stderr)
                self.assertEqual(wproc.stdout.strip(), "GO: workloads")


if __name__ == "__main__":
    unittest.main()
