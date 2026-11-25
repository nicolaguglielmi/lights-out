# SPDX-License-Identifier: Apache-2.0
"""Tests for core/gates/metrics.py, core/gates/orchestrator.py, and gates/*-conformance.sh."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core.gates import NoGo, metrics, orchestrator

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeMetricsVerb:
    """In-memory fake METRICS verb callable matching core.gates.adapter."""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[tuple[str, tuple[str, ...], dict[str, str]]] = []

    def __call__(self, name: str, *args: str, env=None):
        env_map = dict(env or {})
        self.calls.append((name, args, env_map))
        return self.handler(name, args, env_map)


class MetricsGateScriptTest(unittest.TestCase):
    def test_gate_scripts_are_thin_executable_and_cold_nogo(self):
        for script_rel, suite_name in (
            ("gates/metrics-conformance.sh", "metrics-conformance"),
            ("gates/orchestrator-conformance.sh", "orchestrator-conformance"),
        ):
            path = REPO_ROOT / script_rel
            self.assertTrue(path.is_file(), f"missing {script_rel}")
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, f"{script_rel} not executable")
            code_lines = [
                line for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            self.assertLessEqual(len(code_lines), 25, f"{script_rel} exceeded 25 lines")

            env = {k: v for k, v in os.environ.items() if k != "CONFORMANCE"}
            proc = subprocess.run(
                ["sh", str(path)],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 1)
            self.assertTrue(proc.stdout.startswith(f"NO-GO: {suite_name} — "), proc.stdout)


class MetricsJudgeUnitTest(unittest.TestCase):
    def test_requires_metrics_env_and_valid_adapter(self):
        with self.assertRaises(NoGo):
            metrics.judge([], env={})
        with self.assertRaises(NoGo):
            metrics.judge([], env={"METRICS": "outside/../escape"})
        with self.assertRaises(NoGo):
            metrics.judge([], env={"METRICS": "nonexistent-metrics-adapter"})

    def test_passes_with_and_without_ready(self):
        def ok_handler(name, args, env):
            if name == "ready":
                return 0, "", ""
            if env.get("METRICS_URL"):
                return 1, "", "unreachable"
            q = args[0]
            if not q or "invalid" in q:
                return 1, "", "bad query"
            if q.startswith("absent_metric_"):
                return 0, json.dumps({"status": "success", "data": {"result": []}}), ""
            return 0, json.dumps({
                "status": "success",
                "data": {"result": [{"metric": {"__name__": q}, "value": [1700000000, "1"]}]},
            }), ""

        verb = FakeMetricsVerb(ok_handler)
        summary_no_ready = metrics.judge([], env={"METRICS": "fake-m"}, verb=verb, has_ready=False)
        self.assertEqual(summary_no_ready, "fake-m, 5 cases (no ready.sh)")
        self.assertFalse(any(c[0] == "ready" for c in verb.calls))

        verb.calls.clear()
        summary_ready = metrics.judge([], env={"METRICS": "fake-m"}, verb=verb, has_ready=True)
        self.assertEqual(summary_ready, "fake-m, 5 cases")
        self.assertTrue(any(c[0] == "ready" for c in verb.calls))
        unreachable_calls = [c for c in verb.calls if c[2].get("METRICS_URL")]
        self.assertEqual(len(unreachable_calls), 1)
        self.assertTrue(unreachable_calls[0][2]["METRICS_URL"].startswith("http://127.0.0.1:"))

    def test_rejects_bad_ready_bad_payload_and_bad_refusals(self):
        def noisy_ready(name, args, env):
            return (0, "ok\n", "") if name == "ready" else (0, "", "")

        with self.assertRaisesRegex(NoGo, "case1 ready"):
            metrics.judge([], env={"METRICS": "m"}, verb=FakeMetricsVerb(noisy_ready), has_ready=True)

        bad_valid_payloads = [
            "not-json",
            json.dumps({"status": "error", "data": {"result": []}}),
            json.dumps({"status": "success", "data": {"result": []}}),
            json.dumps({"status": "success", "data": {"result": [{"metric": "x", "value": [1, "1"]}]}}),
            json.dumps({"status": "success", "data": {"result": [{"metric": {}, "value": [1, "nan-word"]}]}}),
            json.dumps({"status": "success", "data": {"result": [{"metric": {}, "value": [True, "1"]}]}}),
        ]
        for payload in bad_valid_payloads:
            def bad_valid(name, args, env, _p=payload):
                return 0, _p, ""

            with self.assertRaisesRegex(NoGo, "case2 valid-query"):
                metrics.judge([], env={"METRICS": "m"}, verb=FakeMetricsVerb(bad_valid), has_ready=False)

        def non_empty_case3(name, args, env):
            return 0, json.dumps({
                "status": "success",
                "data": {"result": [{"metric": {"__name__": "x"}, "value": [1, "1"]}]},
            }), ""

        with self.assertRaisesRegex(NoGo, "case3 empty-result"):
            metrics.judge([], env={"METRICS": "m"}, verb=FakeMetricsVerb(non_empty_case3), has_ready=False)

        def accepts_bad_query(name, args, env):
            q = args[0]
            if q.startswith("absent_metric_"):
                return 0, json.dumps({"status": "success", "data": {"result": []}}), ""
            if not q or "invalid" in q:
                return 0, "unexpected stdout", ""
            return 0, json.dumps({
                "status": "success",
                "data": {"result": [{"metric": {"__name__": q}, "value": [1, "1"]}]},
            }), ""

        with self.assertRaisesRegex(NoGo, "case4 invalid-query-refused"):
            metrics.judge([], env={"METRICS": "m"}, verb=FakeMetricsVerb(accepts_bad_query), has_ready=False)

        def ignores_unreachable(name, args, env):
            q = args[0]
            if not q or "invalid" in q:
                return 1, "", "bad query"
            if q.startswith("absent_metric_"):
                return 0, json.dumps({"status": "success", "data": {"result": []}}), ""
            return 0, json.dumps({
                "status": "success",
                "data": {"result": [{"metric": {"__name__": q}, "value": [1, "1"]}]},
            }), ""

        with self.assertRaisesRegex(NoGo, "case5 unreachable-endpoint-refused"):
            metrics.judge([], env={"METRICS": "m"}, verb=FakeMetricsVerb(ignores_unreachable), has_ready=False)


class OrchestratorJudgeUnitTest(unittest.TestCase):
    def _make_verbs(self, *, fail_ready=False, leave_undrained=False, skip_step_exec=False,
                    ack_missing=True, accept_missing_bus=False, accept_bad_store=False):
        queue: list[tuple[str, str]] = []
        store: dict[str, dict[str, dict]] = {"tickets": {}}
        next_id = [1]

        def bus_verb(name, *args, data=None, env=None):
            _ = data, env
            if name == "publish":
                queue.append((f"m{next_id[0]}", args[0]))
                next_id[0] += 1
                return 0, "", ""
            if name == "pull":
                if not queue:
                    return 0, "", ""
                mid, body = queue[0]
                return 0, f"{mid} {body}\n", ""
            if name == "ack":
                if queue and queue[0][0] == args[0]:
                    queue.pop(0)
                return 0, "", ""
            return 1, "", f"unknown bus verb {name}"

        def store_verb(name, *args, data=None, env=None):
            _ = env
            kind, key = args[0], args[1]
            bucket = store.setdefault(kind, {})
            if name == "put":
                bucket[key] = json.loads((data or b"{}").decode("utf-8"))
                return 0, "", ""
            if name == "get":
                if key not in bucket:
                    return 1, "", "not found"
                return 0, json.dumps(bucket[key]), ""
            if name == "delete":
                bucket.pop(key, None)
                return 0, "", ""
            return 1, "", f"unknown store verb {name}"

        def orch_verb(name, *args, data=None, env=None):
            _ = args, data
            env = env or {}
            if name == "ready":
                return (1, "", "not ready") if fail_ready else (0, "", "")
            if name == "run":
                bus_name = env.get("EVENT_BUS", "")
                store_name = env.get("STATE_STORE", "")
                if not bus_name or bus_name.startswith("missing-bus-"):
                    if accept_missing_bus:
                        return 0, "", ""
                    return 1, "", "missing EVENT_BUS"
                if store_name.startswith("missing-store-"):
                    if queue and ack_missing:
                        queue.pop(0)
                    return (0, "", "") if accept_bad_store else (1, "", "unreadable store")
                if not queue:
                    return 0, "", ""
                if leave_undrained:
                    return 0, "", ""
                mid, raw = queue[0]
                msg = json.loads(raw)
                tid = msg["ticket"]
                if tid in store["tickets"]:
                    if not skip_step_exec:
                        marker = env.get("CONFORMANCE_STEP_MARKER")
                        if marker:
                            Path(marker).write_text("", encoding="utf-8")
                        store["tickets"][tid]["state"] = "held"
                    queue.pop(0)
                    return 0, "", ""
                if ack_missing:
                    queue.pop(0)
                return 1, "", f"ticket {tid} missing"
            return 1, "", f"unknown verb {name}"

        return orch_verb, bus_verb, store_verb

    def test_requires_orchestrator_env_and_passes_with_fakes(self):
        with self.assertRaises(NoGo):
            orchestrator.judge([], env={})
        with self.assertRaises(NoGo):
            orchestrator.judge([], env={"ORCHESTRATOR": "outside/../escape"})
        with self.assertRaises(NoGo):
            orchestrator.judge([], env={"ORCHESTRATOR": "nonexistent-orch-adapter"})

        orch, bus, store = self._make_verbs()
        summary = orchestrator.judge(
            [], env={"ORCHESTRATOR": "fake-orch"}, verb=orch, has_ready=True, bus_verb=bus, store_verb=store
        )
        self.assertEqual(summary, "fake-orch, 5 cases")

        orch2, bus2, store2 = self._make_verbs()
        summary2 = orchestrator.judge(
            [], env={"ORCHESTRATOR": "fake-orch"}, verb=orch2, has_ready=False, bus_verb=bus2, store_verb=store2
        )
        self.assertEqual(summary2, "fake-orch, 5 cases (no ready.sh)")

    def test_detects_contract_violations(self):
        orch, bus, store = self._make_verbs(fail_ready=True)
        with self.assertRaisesRegex(NoGo, "case1 ready"):
            orchestrator.judge([], env={"ORCHESTRATOR": "o"}, verb=orch, has_ready=True, bus_verb=bus, store_verb=store)

        orch, bus, store = self._make_verbs(leave_undrained=True)
        with self.assertRaisesRegex(NoGo, "case3 step-dispatch: EVENT_BUS was not drained"):
            orchestrator.judge([], env={"ORCHESTRATOR": "o"}, verb=orch, has_ready=False, bus_verb=bus, store_verb=store)

        orch, bus, store = self._make_verbs(ack_missing=False)
        with self.assertRaisesRegex(NoGo, "case4 failed-step-acked"):
            orchestrator.judge([], env={"ORCHESTRATOR": "o"}, verb=orch, has_ready=False, bus_verb=bus, store_verb=store)

        orch, bus, store = self._make_verbs(accept_missing_bus=True)
        with self.assertRaisesRegex(NoGo, "case5 missing-bus-and-store-refused"):
            orchestrator.judge([], env={"ORCHESTRATOR": "o"}, verb=orch, has_ready=False, bus_verb=bus, store_verb=store)

        orch, bus, store = self._make_verbs(accept_bad_store=True)
        with self.assertRaisesRegex(NoGo, "case5 missing-bus-and-store-refused"):
            orchestrator.judge([], env={"ORCHESTRATOR": "o"}, verb=orch, has_ready=False, bus_verb=bus, store_verb=store)


class MetricsAndOrchestratorE2ETest(unittest.TestCase):
    def test_outside_file_metrics_and_promql_conformance_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, CONFORMANCE="1", METRICS="outside/file-metrics",
                       ADAPTERS_PATH=str(REPO_ROOT / "fixtures" / "outside-adapters"),
                       FILE_METRICS_DIR=tmp, METRICS_URL="")
            proc = subprocess.run(
                ["sh", "gates/metrics-conformance.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.strip(), "GO: metrics-conformance — outside/file-metrics, 5 cases")

        class PromHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                _ = format, args

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path != "/api/v1/query":
                    self.send_response(404)
                    self.end_headers()
                    return
                query = parse_qs(parsed.query).get("query", [""])[0]
                if not query or "invalid" in query or "(" in query:
                    body = json.dumps({"status": "error", "errorType": "bad_data", "error": "parse error"}).encode("utf-8")
                    self.send_response(400)
                elif query == "up":
                    body = json.dumps({
                        "status": "success",
                        "data": {"resultType": "vector",
                                 "result": [{"metric": {"__name__": "up"}, "value": [1700000000, "1"]}]},
                    }).encode("utf-8")
                    self.send_response(200)
                else:
                    body = json.dumps({"status": "success", "data": {"resultType": "vector", "result": []}}).encode("utf-8")
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), PromHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}"
            env = dict(os.environ, CONFORMANCE="1", METRICS="promql", METRICS_URL=url)
            env.pop("METRICS_AUTH_HEADER", None)
            proc = subprocess.run(
                ["sh", "gates/metrics-conformance.sh"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.strip(), "GO: metrics-conformance — promql, 5 cases (no ready.sh)")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_inproc_and_outside_file_orchestrator_conformance_end_to_end(self):
        inproc_env = dict(os.environ, CONFORMANCE="1", ORCHESTRATOR="inproc", EVENT_BUS="spool", STATE_STORE="sqlite")
        inproc_proc = subprocess.run(
            ["sh", "gates/orchestrator-conformance.sh"],
            cwd=REPO_ROOT,
            env=inproc_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(inproc_proc.returncode, 0, inproc_proc.stdout + inproc_proc.stderr)
        self.assertEqual(inproc_proc.stdout.strip(), "GO: orchestrator-conformance — inproc, 5 cases (no ready.sh)")

        out_env = dict(os.environ, CONFORMANCE="1", ORCHESTRATOR="outside/file-orchestrator",
                       ADAPTERS_PATH=str(REPO_ROOT / "fixtures" / "outside-adapters"),
                       EVENT_BUS="spool", STATE_STORE="sqlite")
        out_proc = subprocess.run(
            ["sh", "gates/orchestrator-conformance.sh"],
            cwd=REPO_ROOT,
            env=out_env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out_proc.returncode, 0, out_proc.stdout + out_proc.stderr)
        self.assertEqual(
            out_proc.stdout.strip(),
            "GO: orchestrator-conformance — outside/file-orchestrator, 5 cases",
        )


if __name__ == "__main__":
    unittest.main()
