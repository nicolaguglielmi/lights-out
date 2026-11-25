"""End-to-end tests for M82 Enterprise Observability & Charts Port (ADR 0122).

Runs live ThreadingHTTPServer mock endpoints for Grafana (/api/health and
/api/v1/query_range) and Google Cloud Monitoring (POST
/v3/projects/<project>/timeSeries:query), executing gates/charts-conformance.sh,
adapter ready.sh / query-range.sh scripts, core.obs helpers, and traced
incident -> postmortem checks.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import stat
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import obs, postmortem

ROOT = Path(__file__).resolve().parents[2]


class _GrafanaState:
    def __init__(self, expected_token: str = "tok-grafana-secret") -> None:
        self.expected_token = expected_token
        self.requests: list[dict] = []


def _start_grafana_mock(state: _GrafanaState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _authorized(self) -> bool:
            if not state.expected_token:
                return True
            if self.headers.get("Authorization") == f"Bearer {state.expected_token}":
                return True
            self.send_response(401)
            self.end_headers()
            return False

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query, keep_blank_values=True)
            state.requests.append(
                {
                    "path": parsed.path,
                    "query": qs,
                    "auth": self.headers.get("Authorization", ""),
                }
            )
            if not self._authorized():
                return
            if parsed.path == "/api/health":
                body = json.dumps({"database": "ok"}).encode("utf-8")
            elif parsed.path == "/api/v1/query_range":
                expr = (qs.get("query") or [""])[0]
                if "absent" in expr:
                    result = []
                else:
                    result = [
                        {
                            "metric": {"__name__": "http_requests_total", "service_version": "0.2.0"},
                            "values": [[1700000000, "12.5"], [1700000015, "14.0"]],
                        }
                    ]
                body = json.dumps({"status": "success", "data": {"resultType": "matrix", "result": result}}).encode(
                    "utf-8"
                )
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _CloudMonitoringState:
    def __init__(self, expected_token: str = "tok-cm-secret") -> None:
        self.expected_token = expected_token
        self.requests: list[dict] = []


def _start_cloud_monitoring_mock(state: _CloudMonitoringState):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode("utf-8", "replace")
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {}
            state.requests.append(
                {
                    "path": parsed.path,
                    "payload": payload,
                    "auth": self.headers.get("Authorization", ""),
                }
            )
            if state.expected_token and self.headers.get("Authorization") != f"Bearer {state.expected_token}":
                self.send_response(401)
                self.end_headers()
                return
            if parsed.path.endswith("/timeSeries:query"):
                expr = str(payload.get("query") or "")
                if "within 1m" in expr:
                    body = json.dumps({"timeSeriesData": []}).encode("utf-8")
                elif "absent" in expr:
                    body = json.dumps({"timeSeriesData": []}).encode("utf-8")
                else:
                    body = json.dumps(
                        {
                            "timeSeriesDescriptor": {
                                "labelDescriptors": [{"key": "metric.service_version"}],
                                "pointDescriptors": [{"key": "value", "valueType": "DOUBLE"}],
                            },
                            "timeSeriesData": [
                                {
                                    "labelValues": [{"stringValue": "0.2.0"}],
                                    "pointData": [
                                        {
                                            "values": [{"doubleValue": 0.025}],
                                            "timeInterval": {
                                                "startTime": "2026-11-24T10:00:00Z",
                                                "endTime": "2026-11-24T10:00:15Z",
                                            },
                                        },
                                        {
                                            "values": [{"doubleValue": 0.030}],
                                            "timeInterval": {
                                                "startTime": "2026-11-24T10:00:15Z",
                                                "endTime": "2026-11-24T10:00:30Z",
                                            },
                                        },
                                    ],
                                }
                            ],
                        }
                    ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ObsChartsE2ETests(unittest.TestCase):
    def _run_gate(self, extra_env: dict[str, str]):
        env = dict(os.environ)
        env.update(extra_env)
        return subprocess.run(
            ["sh", str(ROOT / "gates" / "charts-conformance.sh")],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cold_charts_conformance_emits_one_nogo_line(self) -> None:
        env = dict(os.environ)
        env.pop("CONFORMANCE", None)
        proc = subprocess.run(
            ["sh", str(ROOT / "gates" / "charts-conformance.sh")],
            cwd=str(ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertTrue(lines[0].startswith("NO-GO: charts-conformance — "), lines[0])
        self.assertNotIn("\x1b", proc.stdout)

    def test_grafana_adapter_conformance_ready_and_query_charts_series(self) -> None:
        state = _GrafanaState(expected_token="tok-grafana-secret")
        server, thread = _start_grafana_mock(state)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            base_env = {
                "CONFORMANCE": "1",
                "CHARTS": "grafana",
                "GRAFANA_URL": url,
                "GRAFANA_TOKEN": "tok-grafana-secret",
                "CONFORMANCE_QUERY": "http_requests_total",
            }
            proc = self._run_gate(base_env)
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
            self.assertEqual(proc.stdout.strip(), "GO: charts-conformance — grafana, 5 cases")
            self.assertNotIn("tok-grafana-secret", proc.stdout + proc.stderr)
            self.assertTrue(any(r["auth"] == "Bearer tok-grafana-secret" for r in state.requests))

            ready_env = dict(os.environ, GRAFANA_URL=url, GRAFANA_TOKEN="tok-grafana-secret", READY_TIMEOUT="2")
            r_ok = subprocess.run(
                ["sh", str(ROOT / "adapters" / "grafana" / "ready.sh")],
                cwd=str(ROOT),
                env=ready_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual((r_ok.returncode, r_ok.stdout), (0, ""), r_ok.stderr)

            old_env = {k: os.environ.get(k) for k in ("GRAFANA_URL", "GRAFANA_TOKEN")}
            os.environ["GRAFANA_URL"] = url
            os.environ["GRAFANA_TOKEN"] = "tok-grafana-secret"
            try:
                series_doc = obs.query_charts_series(
                    str(ROOT / "adapters" / "grafana"), ["http_requests_total"], 1700000000, 1700000300, step_s=15
                )
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(len(series_doc["series"]), 1)
            self.assertEqual(series_doc["series"][0]["points"], [[1700000000.0, 12.5], [1700000015.0, 14.0]])

            bad_ready = subprocess.run(
                ["sh", str(ROOT / "adapters" / "grafana" / "ready.sh")],
                cwd=str(ROOT),
                env=dict(os.environ, GRAFANA_URL=f"http://127.0.0.1:{_closed_port()}", READY_TIMEOUT="1"),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(bad_ready.returncode, 1)
            self.assertEqual(bad_ready.stdout, "")
            self.assertTrue(bad_ready.stderr.strip())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_cloud_monitoring_adapter_conformance_ready_and_query_charts_series(self) -> None:
        state = _CloudMonitoringState(expected_token="tok-cm-secret")
        server, thread = _start_cloud_monitoring_mock(state)
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            base_env = {
                "CONFORMANCE": "1",
                "CHARTS": "cloud-monitoring",
                "MONITORING_API_URL": url,
                "MONITORING_PROJECT": "demo-proj",
                "MONITORING_ACCESS_TOKEN": "tok-cm-secret",
                "CONFORMANCE_QUERY": "fetch k8s_container::custom.googleapis.com/http_latency",
            }
            proc = self._run_gate(base_env)
            self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}")
            self.assertEqual(proc.stdout.strip(), "GO: charts-conformance — cloud-monitoring, 5 cases")
            self.assertNotIn("tok-cm-secret", proc.stdout + proc.stderr)
            self.assertTrue(
                any(
                    r["path"] == "/v3/projects/demo-proj/timeSeries:query" and r["auth"] == "Bearer tok-cm-secret"
                    for r in state.requests
                )
            )

            ready_env = dict(
                os.environ,
                MONITORING_API_URL=url,
                MONITORING_PROJECT="demo-proj",
                MONITORING_ACCESS_TOKEN="tok-cm-secret",
                READY_TIMEOUT="2",
            )
            r_ok = subprocess.run(
                ["sh", str(ROOT / "adapters" / "cloud-monitoring" / "ready.sh")],
                cwd=str(ROOT),
                env=ready_env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual((r_ok.returncode, r_ok.stdout), (0, ""), r_ok.stderr)

            old_env = {
                k: os.environ.get(k) for k in ("MONITORING_API_URL", "MONITORING_PROJECT", "MONITORING_ACCESS_TOKEN")
            }
            os.environ["MONITORING_API_URL"] = url
            os.environ["MONITORING_PROJECT"] = "demo-proj"
            os.environ["MONITORING_ACCESS_TOKEN"] = "tok-cm-secret"
            try:
                series_doc = obs.query_charts_series(
                    str(ROOT / "adapters" / "cloud-monitoring"),
                    ["fetch k8s_container::custom.googleapis.com/http_latency"],
                    "2026-11-24T10:00:00Z",
                    "2026-11-24T10:05:00Z",
                    step_s=15,
                )
            finally:
                for k, v in old_env.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(len(series_doc["series"]), 1)
            self.assertEqual(len(series_doc["series"][0]["points"]), 2)
            self.assertAlmostEqual(series_doc["series"][0]["points"][1][1], 0.030)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_stage_probes_and_traced_incident_e2e(self) -> None:
        t1 = "4bf92f3577b34da6a3ce929d0e0e4736"
        s1 = "00f067aa0ba902b7"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            smoke = root / "smoke.sh"
            smoke.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            smoke.chmod(smoke.stat().st_mode | stat.S_IXUSR)
            res = obs.hold_stage_with_probes(4.0, smoke_cmd=str(smoke), interval_s=0.02, max_consecutive_failures=2)
            self.assertEqual(res["verdict"], "NO-GO")
            self.assertEqual(res["probes_run"], 2)
            self.assertLess(res["elapsed_s"], 1.5)

            rec_path = root / "release.json"
            rec_path.write_text(
                json.dumps(
                    {
                        "version": "0.2.0-canary",
                        "stable": "0.1.0",
                        "image": "registry.local/app@sha256:1",
                        "verdict": "rolled-back",
                        "held_at": None,
                        "stages": [{"pct": 5, "verdict": "NO-GO: probe failed", "numbers": []}],
                    }
                ),
                encoding="utf-8",
            )
            logs_path = root / "canary.log"
            logs_path.write_text(f"2026-11-24T10:00:05Z traceparent=00-{t1}-{s1}-01 status=500\n", encoding="utf-8")
            out_dir = root / "incidents"
            inc_proc = subprocess.run(
                ["sh", str(ROOT / "core" / "incident.sh")],
                cwd=str(ROOT),
                env=dict(
                    os.environ,
                    NAME="e2e-traced",
                    FROM="2026-11-24T10:00:00Z",
                    TO="2026-11-24T10:05:00Z",
                    RELEASE_RECORD=str(rec_path),
                    LOGS=str(logs_path),
                    INCIDENT_OUT=str(out_dir),
                    TICKET="",
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(inc_proc.returncode, 0, inc_proc.stdout + inc_proc.stderr)
            inc_path = out_dir / "e2e-traced.json"
            doc = postmortem.validate(str(inc_path))
            self.assertEqual(doc["traces"], [{"trace_id": t1, "span_id": s1}])
            self.assertIn(t1, postmortem.anchor_evidence(f"trace:{t1}", doc)[0])


if __name__ == "__main__":
    unittest.main()
