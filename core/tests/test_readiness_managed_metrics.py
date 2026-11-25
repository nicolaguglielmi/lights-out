"""Tests for core/gates/readiness.py support of METRICS=managed-prometheus.

Strictly vendor-free (ci/vendor-free.sh) and isolated from cloud provider names.
"""

import contextlib
import http.server
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import urllib.parse

from core.gates import NoGo, readiness

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _promql(*values):
    return json.dumps({"status": "success", "data": {"result": [{"value": [0, v]} for v in values]}})


class _Tree:
    def __init__(self, test):
        self.dir = tempfile.mkdtemp()
        test.addCleanup(lambda: subprocess.run(["rm", "-rf", self.dir], check=False))
        cwd = os.getcwd()
        os.chdir(self.dir)
        test.addCleanup(os.chdir, cwd)

    def write(self, rel, text):
        os.makedirs(os.path.dirname(rel) or ".", exist_ok=True)
        with open(rel, "w", encoding="utf-8") as fh:
            fh.write(text)


BASE_ENV = {
    "DELIVERY": "stagehand",
    "METRICS": "managed-prometheus",
    "METRICS_URL": "http://127.0.0.1:9090",
    "CONSOLE_AUTH": "proxy",
    "CONSOLE_IDENTITY_HEADER": "X-User",
    "CONSOLE_LAUNCHERS": "ada,grace",
    "READINESS_ENV_FILE": "prod.env",
    "CONFORMANCE_DIR": "records",
}


class ManagedMetricsReadinessTest(unittest.TestCase):
    def setUp(self):
        self.t = _Tree(self)
        self.t.write("adapters/stagehand/rollback.sh", "echo rollback\n")
        self.t.write("adapters/stagehand/deploy.sh", "echo deploy\n")
        self.t.write(
            "verify/slo.promql",
            "# name: traffic\nsum by (service_version) (rate(http_server_request_duration_seconds_count[1m]) "
            "* on (job, instance) group_left (service_version) target_info)\n",
        )
        self.t.write("prod.env", "APP_PORT=9000\nSERVICE_KEY=\n")
        digest = readiness.adapter_hash("adapters/stagehand")
        self.t.write("records/delivery-stagehand.env", f"ADAPTER=stagehand\nHASH={digest}\nAT=2026-11-24T00:00:00Z\n")

    def _run_gate(self, env, answer=_promql("3")):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                return "GO", readiness.judge([], env=dict(env), query=lambda e: answer), err.getvalue()
            except NoGo as no:
                return "NO-GO", no.reason, err.getvalue()

    def test_managed_prometheus_with_metrics_url_is_go(self):
        verdict, text, _ = self._run_gate(BASE_ENV, _promql("2", "1"))
        self.assertEqual(verdict, "GO", text)
        self.assertIn("metrics from METRICS_URL, 3 target_info series", text)

    def test_managed_prometheus_with_gcp_project_without_metrics_url_is_go(self):
        for key in ("GCP_PROJECT", "GOOGLE_CLOUD_PROJECT"):
            with self.subTest(key=key):
                env = {k: v for k, v in BASE_ENV.items() if k != "METRICS_URL"}
                env[key] = "demo-proj-1"
                verdict, text, _ = self._run_gate(env, _promql("4"))
                self.assertEqual(verdict, "GO", text)
                self.assertIn("managed-prometheus (demo-proj-1), 4 target_info series", text)

    def test_managed_prometheus_requires_metrics_url_or_project(self):
        for bad_proj in ("", "replace-me"):
            with self.subTest(bad_proj=bad_proj):
                env = {k: v for k, v in BASE_ENV.items() if k != "METRICS_URL"}
                if bad_proj:
                    env["GCP_PROJECT"] = bad_proj
                verdict, text, _ = self._run_gate(env)
                self.assertEqual(verdict, "NO-GO", text)
                self.assertIn("metrics (METRICS_URL or GCP_PROJECT is not set)", text)

    def test_managed_prometheus_rejects_empty_zero_or_nan_samples(self):
        for answer in (_promql(), _promql("0"), _promql("NaN"), None, "not-json"):
            with self.subTest(answer=answer):
                verdict, text, _ = self._run_gate(BASE_ENV, answer)
                self.assertEqual(verdict, "NO-GO", text)
                self.assertIn("metrics (", text)

    def test_unknown_metrics_adapter_still_fails(self):
        verdict, text, _ = self._run_gate(dict(BASE_ENV, METRICS="prometheus"))
        self.assertEqual(verdict, "NO-GO", text)
        self.assertIn("METRICS=prometheus is not promql", text)

    def test_real_managed_prometheus_adapter_invocation_via_judge(self):
        adapter_src = os.path.join(REPO, "adapters", "managed-prometheus")
        os.makedirs("adapters", exist_ok=True)
        os.symlink(adapter_src, "adapters/managed-prometheus")

        received_auth = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):  # noqa: N802
                received_auth.append(self.headers.get("Authorization", ""))
                u = urllib.parse.urlsplit(self.path)
                if u.path != "/api/v1/query" or self.headers.get("Authorization") != "Bearer tok-mp-readiness":
                    self.send_response(401 if u.path == "/api/v1/query" else 404)
                    self.end_headers()
                    return
                body = _promql("2").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(srv.shutdown)
        self.addCleanup(srv.server_close)
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        env = dict(
            BASE_ENV,
            METRICS_URL=f"http://127.0.0.1:{srv.server_port}",
            METRICS_AUTH_HEADER="Authorization: Bearer tok-mp-readiness",
        )
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            text = readiness.judge([], env=env)
        self.assertIn("2 target_info series", text)
        self.assertEqual(received_auth, ["Bearer tok-mp-readiness"])
        self.assertNotIn("tok-mp-readiness", text + err.getvalue())


if __name__ == "__main__":
    unittest.main()
