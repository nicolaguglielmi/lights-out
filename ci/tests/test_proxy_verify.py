"""Specification tests for ADR 0098 (Verify from the proxy)."""

from __future__ import annotations

import http.server
import json
import os
import re
import socket
import socketserver
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class _UnixHTTPHandler(http.server.BaseHTTPRequestHandler):
    containers: list[dict] = []
    inspects: dict[str, dict] = {}

    def log_message(self, *_args):
        return

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path.endswith("/containers/json") or path == "/containers/json":
            payload = json.dumps(self.containers).encode()
        elif path.endswith("/json") and "/containers/" in path:
            cid = path.split("/containers/", 1)[1].rsplit("/json", 1)[0]
            payload = json.dumps(self.inspects.get(cid, {})).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _UnixServer(socketserver.UnixStreamServer):
    pass


class ProxyVerifySpecTest(unittest.TestCase):
    def test_verify_slo_promql_reads_envoy_clusters_without_target_info(self) -> None:
        text = (ROOT / "verify/slo.promql").read_text(encoding="utf-8")
        self.assertIn("envoy_cluster_upstream_rq_completed", text)
        self.assertIn('envoy_cluster_upstream_rq_xx{envoy_response_code_class="5"}', text)
        self.assertIn("envoy_cluster_upstream_rq_time_bucket", text)
        self.assertIn("/ 1000", text)
        self.assertIn("envoy_cluster_name", text)
        self.assertNotIn("target_info", text)
        self.assertNotIn("http_server_request_duration_seconds", text)

    def test_k8s_adapters_keep_app_slo_promql(self) -> None:
        for adapter in ("argo-rollouts", "flagger"):
            path = ROOT / "adapters" / adapter / "slo.promql"
            self.assertTrue(path.is_file(), f"missing {path}")
            text = path.read_text(encoding="utf-8")
            for name in ("traffic", "error_rate", "latency_p95"):
                self.assertIn(f"# name: {name}", text)
            self.assertIn("target_info", text)
            self.assertIn("service_version", text)
            readme = (ROOT / "adapters" / adapter / "README.md").read_text(encoding="utf-8")
            self.assertIn("slo.promql", readme)

    def test_envoy_and_prometheus_config_aligned(self) -> None:
        envoy_yaml = (ROOT / "adapters/envoy-canary/envoy.yaml").read_text(encoding="utf-8")
        self.assertRegex(envoy_yaml, r"(?m)^stats_flush_interval:\s*1s\b")

        prom_yml = (ROOT / "adapters/prometheus/prometheus.yml").read_text(encoding="utf-8")
        self.assertRegex(prom_yml, r"job_name:\s*envoy\b")
        self.assertIn("metrics_path: /stats/prometheus", prom_yml)
        self.assertIn("envoy:9901", prom_yml)
        self.assertNotIn("backend:8000", prom_yml)
        self.assertNotIn("backend-canary:8000", prom_yml)
        self.assertIn("backend-k8s-stable", prom_yml)
        self.assertIn("backend-k8s-canary", prom_yml)

        ready_sh = (ROOT / "adapters/prometheus/ready.sh").read_text(encoding="utf-8")
        self.assertIn('up{job="envoy"}', ready_sh)

    def test_compose_has_probe_service(self) -> None:
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        self.assertRegex(compose, r"(?m)^  probe:\s*$")
        self.assertIn('profiles: ["probe"]', compose)

    def test_verify_slo_sh_maps_envoy_cluster_names(self) -> None:
        responses = {
            "traffic": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.5"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "4.5"]},
            ],
            "error_rate": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.25"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "0.0"]},
            ],
            "latency_p95": [
                {"metric": {"envoy_cluster_name": "canary"}, "value": [1, "0.01"]},
                {"metric": {"envoy_cluster_name": "stable"}, "value": [1, "0.01"]},
            ],
        }

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query).get("query", [""])[0]
                if "rq_xx" in q:
                    res = responses["error_rate"]
                elif "histogram_quantile" in q:
                    res = responses["latency_p95"]
                else:
                    res = responses["traffic"]
                body = json.dumps({"status": "success", "data": {"result": res}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            env = {
                **os.environ,
                "WORKLOAD_CONTRACT": str(ROOT / "app"),
                "CANARY_VERSION": "v-canary-test",
                "STABLE_VERSION": "v-stable-test",
                "METRICS_QUERY": "adapters/promql/query.sh",
                "METRICS_URL": f"http://127.0.0.1:{srv.server_port}",
                "DELIVERY": "envoy-canary",
            }
            proc = subprocess.run(
                ["sh", "verify/slo.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("NO-GO: slo — error_rate", proc.stdout)
            self.assertIn("canary(v-canary-test)=0.25", proc.stderr)
            self.assertIn("stable(v-stable-test)=0", proc.stderr)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_verify_metric_shape_passes_pre_5xx_and_empty_kill_switch(self) -> None:
        seen_paths: list[str] = []
        envoy_prom = "\n".join([
            '# TYPE envoy_cluster_upstream_rq_completed counter',
            'envoy_cluster_upstream_rq_completed{envoy_cluster_name="stable"} 3',
            '# TYPE envoy_cluster_upstream_rq_xx counter',
            'envoy_cluster_upstream_rq_xx{envoy_response_code_class="2",envoy_cluster_name="stable"} 3',
            '# TYPE envoy_cluster_upstream_rq_time histogram',
            'envoy_cluster_upstream_rq_time_bucket{envoy_cluster_name="stable",le="0.5"} 3',
            'envoy_cluster_upstream_rq_time_bucket{envoy_cluster_name="stable",le="3600000"} 3',
            'envoy_cluster_upstream_rq_time_bucket{envoy_cluster_name="stable",le="+Inf"} 3',
            "",
        ]).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                seen_paths.append(self.path)
                if self.path == "/":
                    body = b"ok"
                    ctype = "text/plain"
                elif self.path == "/stats/prometheus":
                    body = envoy_prom
                    ctype = "text/plain; version=0.0.4"
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_port}"
            env = {
                **os.environ,
                "APP_URL": base,
                "ENVOY_ADMIN_URL": base,
                "DELIVERY": "envoy-canary",
                "WORKLOAD_KILL_SWITCH": "",
                "WORKLOAD_SAMPLE_PATH": "/",
            }
            proc = subprocess.run(
                ["sh", "verify/metric-shape.sh"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(proc.stdout.startswith("GO:"), proc.stdout)
            self.assertIn("WORKLOAD_KILL_SWITCH is empty", proc.stderr)
            self.assertIn("/stats/prometheus", seen_paths)
            self.assertNotIn("/metrics", seen_paths)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_envoy_status_sh_version_precedence_over_unix_socket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = Path(tmp) / "docker.sock"

            class TestHandler(_UnixHTTPHandler):
                containers = [
                    {
                        "Id": "cid-stable",
                        "State": "running",
                        "Image": "localhost/plain:tag-stable",
                        "Labels": {
                            "com.docker.compose.project": "lights-out",
                            "com.docker.compose.service": "backend",
                            "lights-out.version": "lbl-stable",
                        },
                    },
                    {
                        "Id": "cid-canary",
                        "State": "running",
                        "Image": "localhost/plain:tag-canary",
                        "Labels": {
                            "com.docker.compose.project": "lights-out",
                            "com.docker.compose.service": "backend-canary",
                        },
                    },
                ]
                inspects = {
                    "cid-stable": {
                        "State": {"Running": True},
                        "Config": {
                            "Image": "localhost/plain:tag-stable",
                            "Labels": {"lights-out.version": "lbl-stable"},
                            "Env": ["APP_VERSION=env-stable"],
                        },
                    },
                    "cid-canary": {
                        "State": {"Running": True},
                        "Config": {
                            "Image": "localhost/plain:tag-canary",
                            "Labels": {},
                            "Env": ["APP_VERSION=env-canary"],
                        },
                    },
                }

            class AdminHandler(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_args):
                    return

                def do_GET(self):  # noqa: N802
                    body = json.dumps({"entries": {"weights.canary": {"final_value": 25}}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            admin_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AdminHandler)
            admin_t = threading.Thread(target=admin_srv.serve_forever, daemon=True)
            admin_t.start()
            srv = _UnixServer(str(sock_path), TestHandler)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            try:
                env = {
                    **os.environ,
                    "DOCKER_HOST": f"unix://{sock_path}",
                    "COMPOSE_PROJECT_NAME": "lights-out",
                    "ENVOY_ADMIN_URL": f"http://127.0.0.1:{admin_srv.server_port}",
                    "APP_URL": "http://127.0.0.1:9",
                }
                proc = subprocess.run(
                    ["sh", "adapters/envoy-canary/status.sh"],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                data = json.loads(proc.stdout)
                self.assertEqual(data["stable"], "lbl-stable")
                self.assertEqual(data["canary"], "env-canary")
                self.assertEqual(data["weight"], 25)
            finally:
                srv.shutdown()
                srv.server_close()
                admin_srv.shutdown()
                admin_srv.server_close()

    def test_envoy_ready_sh_honors_workload_health_probe_without_healthz(self) -> None:
        seen_paths: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                seen_paths.append(self.path)
                if self.path in ("/ok", "/ready"):
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(404)
                    self.end_headers()

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}"
            env = {
                **os.environ,
                "APP_URL": url,
                "ENVOY_ADMIN_URL": url,
                "WORKLOAD_HEALTH_PROBE": "http:/ok",
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
            self.assertIn("/ok", seen_paths)
            self.assertNotIn("/healthz", seen_paths)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_launchpad_reads_stable_version_from_delivery_status_not_healthz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            adapter_dir = tmp_path / "adapters" / "mockdel"
            adapter_dir.mkdir(parents=True)
            status_sh = adapter_dir / "status.sh"
            status_sh.write_text('#!/bin/sh\necho \'{"stable":"v-from-engine","canary":null}\'\n')
            status_sh.chmod(status_sh.stat().st_mode | stat.S_IXUSR)

            core_dir = tmp_path / "core"
            core_dir.mkdir()
            shutil_src = (ROOT / "core/launchpad.sh").read_text(encoding="utf-8")
            cmd_src = (ROOT / "core/launchpad_cmd.py").read_text(encoding="utf-8")
            (core_dir / "launchpad.sh").write_text(shutil_src, encoding="utf-8")
            # Stub rollback/deploy/etc. using PATH or checking before rollback fails:
            # launchpad checks STABLE_VERSION only after deploy/verify/promote exist;
            # confirm core/launchpad.sh and core/launchpad_cmd.py no longer curl /healthz.
            self.assertNotIn("/healthz", shutil_src)
            self.assertNotIn("/healthz", cmd_src)
            self.assertIn("core/launchpad_cmd.py", shutil_src)
            self.assertIn('delivery_dir = Path("adapters") / delivery_name', cmd_src)
            self.assertRegex(cmd_src, r'delivery_dir / "status\.sh"')

    def test_plain_workload_has_no_instrumentation_and_broken_patch(self) -> None:
        plain = ROOT / "workloads/plain"
        self.assertTrue((plain / ".lights-out/workload.env").is_file())
        self.assertTrue((plain / ".lights-out/compose.yaml").is_file())
        self.assertTrue((plain / ".lights-out/fixtures/canary/broken.patch").is_file())

        wenv = (plain / ".lights-out/workload.env").read_text(encoding="utf-8")
        self.assertRegex(wenv, r"(?m)^WORKLOAD_HEALTH_PROBE=http:/ok$")
        self.assertRegex(wenv, r"(?m)^WORKLOAD_KILL_SWITCH=$")

        flags = json.loads((plain / ".lights-out/flags/flags.json").read_text(encoding="utf-8"))
        self.assertIn("release-freeze", flags.get("flags", {}))

        all_py = "\n".join(
            p.read_text(encoding="utf-8")
            for p in (plain / "plain").rglob("*.py")
        )
        for forbidden in ("opentelemetry", "prometheus_client", "openfeature", "/metrics"):
            self.assertNotIn(forbidden, all_py)

        # Ensure broken.patch introduces a 500 path rather than OTel.
        patch = (plain / ".lights-out/fixtures/canary/broken.patch").read_text(encoding="utf-8")
        self.assertIn("500", patch)

    def test_ci_workloads_probe_and_kill_switch_rules(self) -> None:
        ci_workloads = (ROOT / "ci/workloads.sh").read_text(encoding="utf-8")
        self.assertIn("WORKLOAD_HEALTH_PROBE", ci_workloads)
        # Validate Makefile + ./go expose demo-seam-plain
        self.assertIn("demo-seam-plain", (ROOT / "Makefile").read_text(encoding="utf-8"))
        go_text = (ROOT / "go").read_text(encoding="utf-8")
        self.assertIn("demo-seam-plain", go_text)


if __name__ == "__main__":
    unittest.main()
