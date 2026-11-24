"""Unit tests for vendor-free proxy readiness derivation and health-probe init preservation."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core import init
from core.gates import readiness

STACKS = Path(__file__).resolve().parents[2] / "stacks"


class ProxyReadinessDerivationTest(unittest.TestCase):
    def _write_promql(self, rel: str, text: str) -> None:
        p = Path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")

    def test_metrics_derives_count_from_traffic_rate_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                self._write_promql(
                    "verify/slo.promql",
                    "# name: traffic\nsum by (slot) (rate(proxy_upstream_rq_completed[1m]))\n",
                )
                seen: list[str] = []

                def fake_query(expr: str) -> str:
                    seen.append(expr)
                    return '{"status":"success","data":{"result":[{"metric":{},"value":[1,"2"]}]}}'

                ok, msg = readiness.metrics(
                    {"METRICS": "promql", "METRICS_URL": "http://metrics:9090"},
                    fake_query,
                )
                self.assertTrue(ok, msg)
                self.assertEqual(seen, ["count(proxy_upstream_rq_completed)"])
                self.assertIn("proxy_upstream_rq_completed", msg)
            finally:
                os.chdir(old)

    def test_metrics_uses_adapter_slo_promql_and_target_info_when_referenced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                self._write_promql(
                    "verify/slo.promql",
                    "# name: traffic\nsum by (slot) (rate(proxy_upstream_rq_completed[1m]))\n",
                )
                self._write_promql(
                    "adapters/k8s-sample/slo.promql",
                    "# name: traffic\nsum by (service_version) (rate(http_requests_total[1m]) * on (job) group_left (service_version) target_info)\n",
                )
                seen: list[str] = []

                def fake_query(expr: str) -> str:
                    seen.append(expr)
                    return '{"status":"success","data":{"result":[{"metric":{},"value":[1,"1"]}]}}'

                ok, msg = readiness.metrics(
                    {
                        "METRICS": "promql",
                        "METRICS_URL": "http://metrics:9090",
                        "DELIVERY": "k8s-sample",
                    },
                    fake_query,
                )
                self.assertTrue(ok, msg)
                self.assertEqual(seen, ["count(target_info)"])
            finally:
                os.chdir(old)

    def test_metrics_strips_selector_braces_from_rate_metric(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                self._write_promql(
                    "verify/slo.promql",
                    '# name: traffic\nsum(rate(edge_requests_total{status="200"}[1m]))\n',
                )
                seen: list[str] = []

                def fake_query(expr: str) -> str:
                    seen.append(expr)
                    return '{"status":"success","data":{"result":[]}}'

                ok, _ = readiness.metrics(
                    {"METRICS": "promql", "METRICS_URL": "http://metrics:9090"},
                    fake_query,
                )
                self.assertFalse(ok)
                self.assertEqual(seen, ["count(edge_requests_total)"])
            finally:
                os.chdir(old)


class InitHealthProbePreservationTest(unittest.TestCase):
    def _sample_repo(self, tmp: str) -> Path:
        repo = Path(tmp)
        (repo / "pyproject.toml").write_text('[project]\nname = "sample"\nrequires-python = ">=3.12"\n')
        (repo / "uv.lock").write_text("version = 1\n")
        (repo / "sample").mkdir()
        (repo / "sample" / "__init__.py").write_text("")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_ok.py").write_text("def test_ok(): pass\n")
        return repo

    def test_render_omits_health_probe_when_not_in_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._sample_repo(tmp)
            d = init.infer(repo)
            out = init.render_workload_env(d, {}, "2026-11-23", "deadbeef")
            self.assertNotIn("WORKLOAD_HEALTH_PROBE=", out)

    def test_render_preserves_health_probe_when_in_committed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._sample_repo(tmp)
            d = init.infer(repo)
            committed = {
                "CANARY_VERSION": "v1",
                "WORKLOAD_KILL_SWITCH": "",
                "WORKLOAD_SAMPLE_PATH": "/",
                "WORKLOAD_HEALTH_PROBE": "http:/ok",
            }
            out = init.render_workload_env(d, committed, "2026-11-23", "deadbeef")
            self.assertIn("WORKLOAD_HEALTH_PROBE=http:/ok\n", out)
            self.assertIn("WORKLOAD_KILL_SWITCH=\n", out)


if __name__ == "__main__":
    unittest.main()
