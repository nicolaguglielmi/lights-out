"""End-to-end integration tests for M78 greenfield app builder (docs/adr/0118)."""

from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from core import adr_gov

ROOT = Path(__file__).resolve().parents[2]
GENESIS_PY = ROOT / "core" / "genesis.py"
GENESIS_SH = ROOT / "core" / "genesis.sh"
ADR_0118 = ROOT / "docs" / "adr" / "0118-greenfield-app-builder-genesis.md"
ROLE_GENESIS = ROOT / "roles" / "architect" / "genesis.md"


def _clean_env(**extra: str) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_")
        and k not in ("BASE", "HEAD", "STAGED", "WORKLOAD", "WORKLOAD_CONTRACT", "WORKLOAD_ATTACHED", "TICKET", "GATES_OUT")
    }
    env.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_AUTHOR_NAME="genesis-e2e",
        GIT_AUTHOR_EMAIL="genesis-e2e@localhost.invalid",
        GIT_COMMITTER_NAME="genesis-e2e",
        GIT_COMMITTER_EMAIL="genesis-e2e@localhost.invalid",
        PYTHONPATH=str(ROOT),
        NO_COLOR="1",
    )
    env.update(extra)
    return env


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _http_get(port: int, path: str) -> tuple[int, dict[str, str], bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        body = resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        return resp.status, headers, body
    finally:
        conn.close()


def _http_post(port: int, path: str, payload: dict[str, object]) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", path, body=data, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


class GreenfieldGenesisE2ETest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="genesis-e2e-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _run_workloads_gate(self, repo_dir: Path) -> None:
        if not shutil.which("docker-compose"):
            return
        wproc = subprocess.run(
            ["sh", "ci/workloads.sh", str(repo_dir)],
            cwd=ROOT,
            env=_clean_env(
                WORKBENCH_IMAGE=os.environ.get("WORKBENCH_IMAGE", "localhost/lights-out-workbench:test"),
                HARNESS_BASE_IMAGE=os.environ.get("HARNESS_BASE_IMAGE", "localhost/lights-out-workbench:test"),
                WORKLOAD_CONTRACT=str(repo_dir / ".lights-out"),
            ),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(wproc.returncode, 0, wproc.stdout + wproc.stderr)
        self.assertEqual(wproc.stdout.strip(), "GO: workloads")

    def _run_spec_gate(self, ticket_path: Path) -> None:
        sproc = subprocess.run(
            ["sh", "gates/spec.sh", str(ticket_path)],
            cwd=ROOT,
            env=_clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(sproc.returncode, 0, sproc.stdout + sproc.stderr)
        self.assertEqual(sproc.stdout.strip(), "GO: spec")

    def _run_light_gates(self, repo_dir: Path, *, host_test: bool) -> str:
        args = [sys.executable, "-m", "core", "gates", "--light", "TICKET=tickets/0001.md"]
        if host_test:
            args.append("TEST_ON_HOST=1")
        lproc = subprocess.run(
            args,
            cwd=repo_dir,
            env=_clean_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(lproc.returncode, 0, lproc.stdout + lproc.stderr)
        line = lproc.stdout.strip()
        self.assertTrue(line.startswith("GO: gates-light"), line)
        return line

    def test_python_greenfield_e2e_from_brief_passes_specs_http_tests_workloads_and_gates(self) -> None:
        repo_dir = self.tmp / "orders-api"
        proc = subprocess.run(
            [
                sys.executable,
                str(GENESIS_PY),
                "--repo",
                str(repo_dir),
                "--brief",
                "Build an orders inventory HTTP service",
                "--stack",
                "python",
                "--port",
                "8080",
                "--json",
            ],
            cwd=ROOT,
            env=_clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        info = json.loads(proc.stdout)
        self.assertTrue(info["ok"])
        self.assertEqual(info["stack"], "python")
        self.assertEqual(info["port"], 8080)

        # 1. Validate ADR 0001 and every starter ticket via gates/spec.sh.
        adr_text = (repo_dir / info["adr_path"]).read_text(encoding="utf-8")
        self.assertEqual(adr_gov.validate_adr(adr_text), [])
        for rel in info["tickets"]:
            self._run_spec_gate(repo_dir / rel)

        # 2. Validate .lights-out/ contract with ci/workloads.sh.
        self._run_workloads_gate(repo_dir)

        # 3. Run generated unit tests via uv --locked.
        venv_dir = self.tmp / "venv-py"
        uv_env = _clean_env(
            UV_PYTHON_DOWNLOADS="never",
            UV_CACHE_DIR=str(self.tmp / "uv-cache"),
            UV_PROJECT_ENVIRONMENT=str(venv_dir),
        )
        uproc = subprocess.run(
            ["uv", "run", "--locked", "python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=repo_dir,
            env=uv_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(uproc.returncode, 0, uproc.stdout + uproc.stderr)

        # 4. Boot the generated HTTP server and verify /healthz, /metrics, and domain endpoint live.
        pkg = info["package"]
        live_port = _free_port()
        srv = subprocess.Popen(
            [sys.executable, "-m", f"{pkg}.server"],
            cwd=repo_dir,
            env=_clean_env(HOST="127.0.0.1", PORT=str(live_port)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 5.0
            ready = False
            while time.monotonic() < deadline:
                try:
                    code, _, body = _http_get(live_port, "/healthz")
                    if code == 200:
                        self.assertEqual(json.loads(body)["status"], "ok")
                        ready = True
                        break
                except OSError:
                    time.sleep(0.05)
            self.assertTrue(ready, "generated HTTP server did not answer /healthz within 5s")

            m_code, m_hdr, m_body = _http_get(live_port, "/metrics")
            self.assertEqual(m_code, 200)
            self.assertIn("text/plain", m_hdr.get("content-type", ""))
            self.assertIn("service_up", m_body.decode("utf-8"))
            self.assertIn("http_requests_total", m_body.decode("utf-8"))

            endpoint = info["endpoint"]
            d_code, _, d_body = _http_get(live_port, endpoint)
            self.assertEqual(d_code, 200)
            self.assertEqual(json.loads(d_body)["service"], info["name"])

            p_code, p_body = _http_post(live_port, endpoint, {"label": "first-entry"})
            self.assertEqual(p_code, 201)
            self.assertEqual(json.loads(p_body)["item"]["label"], "first-entry")

            bad_code, _ = _http_post(live_port, endpoint, {"label": ""})
            self.assertEqual(bad_code, 400)

            nf_code, _, _ = _http_get(live_port, "/does-not-exist")
            self.assertEqual(nf_code, 404)
        finally:
            srv.terminate()
            srv.wait(timeout=5)

        # 5. Prove generated unit tests kill a planted mutant on domain code.
        mutated_dir = self.tmp / "mutated-py"
        shutil.copytree(repo_dir, mutated_dir)
        domain_file = mutated_dir / pkg / "domain.py"
        original_domain = domain_file.read_text(encoding="utf-8")
        mutated_domain = original_domain.replace("201", "200", 1)
        self.assertNotEqual(mutated_domain, original_domain)
        domain_file.write_text(mutated_domain, encoding="utf-8")
        mut_proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"],
            cwd=mutated_dir,
            env=_clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(mut_proc.returncode, 0, "mutant survived generated unit tests")

        # 6. Run core.light on HEAD~1..HEAD -> GO (6 of 10 gates).
        line = self._run_light_gates(repo_dir, host_test=True)
        self.assertIn("6 of 10 gates ran", line)
        self.assertIn("risk low, auto-merge", line)

        # 7. Run individual repository gates (risk, deps, test-weakening, test, test-proof, adr-check, secrets).
        gates_out = self.tmp / "gates-out"
        gates_out.mkdir()
        head_sha = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        base_sha = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD~1"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        gate_env = _clean_env(
            BASE=base_sha,
            HEAD=head_sha,
            WORKLOAD=str(repo_dir),
            WORKLOAD_CONTRACT=str(repo_dir / ".lights-out"),
            WORKLOAD_ATTACHED="1",
            GATES_OUT=str(gates_out),
            GATES_TICKET=str(repo_dir / "tickets" / "0001.md"),
        )
        # Load workload.env keys just as Makefile exports them for gates/*.sh.
        for raw in (repo_dir / ".lights-out" / "workload.env").read_text(encoding="utf-8").splitlines():
            s = raw.strip()
            if s and not s.startswith("#") and "=" in s:
                k, _, v = s.partition("=")
                gate_env[k] = v

        for script in (
            "gates/risk.sh",
            "gates/deps.sh",
            "gates/test-weakening.sh",
            "gates/test.sh",
            "gates/test-proof.sh",
            "gates/adr-check.sh",
            "ci/secrets.sh",
        ):
            with self.subTest(script=script):
                gproc = subprocess.run(
                    ["sh", script],
                    cwd=ROOT,
                    env=gate_env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(gproc.returncode, 0, f"{script}:\n{gproc.stdout}\n{gproc.stderr}")
                self.assertTrue(gproc.stdout.startswith("GO: "), gproc.stdout)

    def test_genesis_sh_wrapper_supports_js_and_ts_with_gates_light(self) -> None:
        for stack, port in (("javascript", "8091"), ("typescript", "8092")):
            with self.subTest(stack=stack):
                repo_dir = self.tmp / f"sh-{stack}"
                env = _clean_env(
                    REPO=str(repo_dir),
                    BRIEF=f"Build a bookings ledger service on {stack}",
                    STACK=stack,
                    SERVICE_NAME=f"bookings-{stack}",
                    PORT=port,
                    JSON="1",
                )
                proc = subprocess.run(
                    ["sh", str(GENESIS_SH)],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                info = json.loads(proc.stdout)
                self.assertEqual(info["stack"], stack)
                self.assertEqual(info["port"], int(port))
                self._run_spec_gate(repo_dir / "tickets" / "0001.md")
                self._run_workloads_gate(repo_dir)
                line = self._run_light_gates(repo_dir, host_test=True)
                self.assertIn("6 of 10 gates ran", line)

    def test_go_rust_java_greenfield_e2e_pass_workloads_specs_deps_and_light(self) -> None:
        for stack, port in (("go", 8081), ("rust", 8082), ("java", 8083)):
            with self.subTest(stack=stack):
                repo_dir = self.tmp / f"poly-{stack}"
                proc = subprocess.run(
                    [
                        sys.executable,
                        str(GENESIS_PY),
                        "--repo",
                        str(repo_dir),
                        "--brief",
                        f"Build an invoicing service in {stack}",
                        "--stack",
                        stack,
                        "--port",
                        str(port),
                        "--json",
                    ],
                    cwd=ROOT,
                    env=_clean_env(),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                info = json.loads(proc.stdout)
                self.assertEqual(adr_gov.validate_adr((repo_dir / info["adr_path"]).read_text(encoding="utf-8")), [])
                self._run_spec_gate(repo_dir / "tickets" / "0001.md")
                self._run_workloads_gate(repo_dir)
                self._run_light_gates(repo_dir, host_test=False)

    def test_m78_boundaries_adr_shell_prompts_and_roles(self) -> None:
        self.assertEqual(adr_gov.validate_adr(ADR_0118.read_text(encoding="utf-8")), [])
        self.assertTrue(GENESIS_SH.is_file())
        self.assertTrue(os.access(GENESIS_SH, os.X_OK), "core/genesis.sh must be executable")
        code_lines = [
            ln for ln in GENESIS_SH.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.lstrip().startswith("#")
        ]
        self.assertLessEqual(len(code_lines), 25, f"core/genesis.sh has {len(code_lines)} code lines (> 25)")
        self.assertTrue(ROLE_GENESIS.is_file())

        for check in (
            "ci/vendor-free.sh",
            "ci/app-free.sh",
            "ci/model-free.sh",
            "ci/shell-boundary.sh",
            "ci/prompts.sh",
            "ci/roles.sh",
        ):
            with self.subTest(check=check):
                cproc = subprocess.run(["sh", check], cwd=ROOT, env=_clean_env(), capture_output=True, text=True)
                self.assertEqual(cproc.returncode, 0, cproc.stdout + cproc.stderr)
        if shutil.which("shellcheck"):
            sc = subprocess.run(["shellcheck", "-s", "sh", str(GENESIS_SH)], capture_output=True, text=True)
            self.assertEqual(sc.returncode, 0, sc.stdout + sc.stderr)


if __name__ == "__main__":
    unittest.main()
