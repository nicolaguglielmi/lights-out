"""Tests for core/obs.py and core/gates/charts.py (docs/adr/0122).

Written from the ADR 0122 specification, strictly vendor-free, app-free,
and model-free. Covers synthetic stage probes, extended SLO signals,
W3C trace exemplar extraction, bounded Charts queries, and the 5-case
Charts port conformance suite.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from core import obs
from core.gates import NoGo, charts


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class HoldStageWithProbesTests(unittest.TestCase):
    def test_sleeps_full_stage_when_smoke_cmd_unset_or_interval_nonpositive(self) -> None:
        clock = FakeClock()
        res = obs.hold_stage_with_probes(
            12.0, smoke_cmd=None, interval_s=5, _sleep=clock.sleep, _monotonic=clock.monotonic
        )
        self.assertEqual(res["verdict"], "GO")
        self.assertEqual(res["probes_run"], 0)
        self.assertEqual(res["failures"], 0)
        self.assertEqual(res["consecutive_failures"], 0)
        self.assertIsNone(res["reason"])
        self.assertEqual(clock.sleeps, [12.0])

        clock2 = FakeClock()
        res2 = obs.hold_stage_with_probes(
            8.0, smoke_cmd=["true"], interval_s=0, _sleep=clock2.sleep, _monotonic=clock2.monotonic
        )
        self.assertEqual(res2["verdict"], "GO")
        self.assertEqual(res2["probes_run"], 0)
        self.assertEqual(clock2.sleeps, [8.0])

    def test_periodic_probes_pass_and_reset_on_transient_failure(self) -> None:
        clock = FakeClock()
        outcomes = iter([1, 0, 0, 0])

        def runner(_argv):
            return next(outcomes), "transient blip"

        res = obs.hold_stage_with_probes(
            30.0,
            smoke_cmd=["sh", "smoke.sh"],
            interval_s=10.0,
            max_consecutive_failures=2,
            _sleep=clock.sleep,
            _monotonic=clock.monotonic,
            _runner=runner,
        )
        self.assertEqual(res["verdict"], "GO")
        self.assertEqual(res["probes_run"], 3)
        self.assertEqual(res["failures"], 1)
        self.assertEqual(res["consecutive_failures"], 0)
        self.assertEqual(res["elapsed_s"], 30.0)
        self.assertIsNone(res["reason"])
        self.assertEqual(clock.sleeps, [10.0, 10.0, 10.0])

    def test_aborts_immediately_on_consecutive_failures(self) -> None:
        clock = FakeClock()
        outcomes = iter([0, 1, 1, 0])

        def runner(_argv):
            return next(outcomes), "503 upstream"

        res = obs.hold_stage_with_probes(
            60.0,
            smoke_cmd="sh smoke.sh",
            interval_s=10.0,
            max_consecutive_failures=2,
            _sleep=clock.sleep,
            _monotonic=clock.monotonic,
            _runner=runner,
        )
        self.assertEqual(res["verdict"], "NO-GO")
        self.assertEqual(res["probes_run"], 3)
        self.assertEqual(res["failures"], 2)
        self.assertEqual(res["consecutive_failures"], 2)
        self.assertEqual(res["elapsed_s"], 20.0)
        self.assertIn("2 consecutive", res["reason"])
        self.assertEqual(clock.sleeps, [10.0, 10.0])

    def test_subprocess_probe_runs_real_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            counter = root / "count"
            smoke = root / "smoke.sh"
            smoke.write_text(
                f"#!/bin/sh\n"
                f"n=0; [ -f '{counter}' ] && n=$(cat '{counter}')\n"
                f"n=$((n + 1)); printf '%s' \"$n\" > '{counter}'\n"
                f"exit 1\n",
                encoding="utf-8",
            )
            smoke.chmod(smoke.stat().st_mode | stat.S_IXUSR)
            res = obs.hold_stage_with_probes(
                5.0, smoke_cmd=str(smoke), interval_s=0.02, max_consecutive_failures=2
            )
            self.assertEqual(res["verdict"], "NO-GO")
            self.assertEqual(res["probes_run"], 2)
            self.assertEqual(res["failures"], 2)
            self.assertLess(res["elapsed_s"], 2.0)

    def test_invalid_arguments_raise_value_error(self) -> None:
        with self.assertRaises(ValueError):
            obs.hold_stage_with_probes(-1)
        with self.assertRaises(ValueError):
            obs.hold_stage_with_probes(10, smoke_cmd=["true"], interval_s=1, max_consecutive_failures=0)


class EvaluateExtendedSignalsTests(unittest.TestCase):
    def test_minimal_metrics_and_empty_policy_never_regress(self) -> None:
        minimal = {
            "traffic": {"0.2.0": 5.0, "0.1.0": 10.0},
            "error_rate": {"0.2.0": 0.0, "0.1.0": 0.0},
            "latency_p95": {"0.2.0": 0.12, "0.1.0": 0.11},
        }
        ok, findings = obs.evaluate_extended_signals(minimal, "0.2.0", "0.1.0", {})
        self.assertTrue(ok)
        self.assertEqual(findings, [])

        ok2, findings2 = obs.evaluate_extended_signals(
            minimal, "0.2.0", "0.1.0", {"ERROR_RATE_MAX_DELTA": "0.01", "LATENCY_P95_MAX_DELTA": "0.25"}
        )
        self.assertTrue(ok2)
        self.assertEqual(findings2, [])

    def test_saturation_restarts_and_burn_rates_pass_within_bounds(self) -> None:
        metrics = {
            "saturation": {"0.2.0": 0.72, "0.1.0": 0.45},
            "restarts": {"0.2.0": 1.0, "0.1.0": 1.0},
            "burn_rate_fast": {"0.2.0": 4.2, "0.1.0": 0.5},
            "burn_rate_slow": {"0.2.0": 1.5, "0.1.0": 0.4},
        }
        policy = {
            "saturation_max": 0.85,
            "restarts_max_delta": 0,
            "burn_rate_fast_max": 14.4,
            "burn_rate_slow_max": 6.0,
        }
        ok, findings = obs.evaluate_extended_signals(metrics, "0.2.0", "0.1.0", policy)
        self.assertTrue(ok, findings)
        self.assertEqual(findings, [])

    def test_breaches_report_clear_findings(self) -> None:
        metrics = {
            "cpu_saturation": {"0.2.0": 0.96, "0.1.0": 0.30},
            "restarts": {"0.2.0": 4.0, "0.1.0": 1.0},
            "burn_rate_fast": {"0.2.0": 20.0, "0.1.0": 1.0},
            "burn_rate_slow": {"0.2.0": 7.5, "0.1.0": 1.0},
        }
        policy = {
            "SATURATION_MAX": "0.85",
            "RESTARTS_MAX_DELTA": "1",
            "BURN_RATE_FAST_MAX": "14.4",
            "BURN_RATE_SLOW_MAX": "6.0",
        }
        ok, findings = obs.evaluate_extended_signals(metrics, "0.2.0", "0.1.0", policy)
        self.assertFalse(ok)
        self.assertEqual(len(findings), 4)
        joined = "\n".join(findings)
        for token in ("cpu_saturation", "restarts", "burn_rate_fast", "burn_rate_slow"):
            self.assertIn(token, joined)

    def test_configured_policy_without_metric_data_fails_closed(self) -> None:
        ok, findings = obs.evaluate_extended_signals({}, "0.2.0", "0.1.0", {"saturation_max": 0.85})
        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)
        self.assertIn("saturation", findings[0])

    def test_metric_present_without_policy_uses_documented_defaults(self) -> None:
        ok, findings = obs.evaluate_extended_signals(
            {"burn_rate_fast": {"0.2.0": 15.0}}, "0.2.0", "0.1.0", {}
        )
        self.assertFalse(ok)
        self.assertIn("burn_rate_fast", findings[0])


class ExtractTraceExemplarsTests(unittest.TestCase):
    def test_extracts_traceparent_and_trace_id_deduplicated_in_order(self) -> None:
        t1 = "4bf92f3577b34da6a3ce929d0e0e4736"
        s1 = "00f067aa0ba902b7"
        t2 = "abcdef0123456789abcdef0123456789"
        s2 = "1234567890abcdef"
        t3 = "11112222333344445555666677778888"
        lines = [
            f"INFO request traceparent=00-{t1.upper()}-{s1.upper()}-01 status=500",
            f"ERROR fallback trace_id={t2}",
            f"DEBUG duplicate traceparent=00-{t1}-{s1}-01",
            f"WARN late span trace_id: {t2} span_id: {s2}",
            {"headline": "alert", "trace_id": t3},
        ]
        got = obs.extract_trace_exemplars(lines)
        self.assertEqual(
            got,
            [
                {"trace_id": t1, "span_id": s1},
                {"trace_id": t2, "span_id": s2},
                {"trace_id": t3, "span_id": None},
            ],
        )

    def test_rejects_all_zero_and_40_char_shas_and_caps_max_traces(self) -> None:
        lines = [
            "traceparent=00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            "trace_id=0123456789abcdef0123456789abcdef01234567",
            "trace_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "trace_id=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "trace_id=cccccccccccccccccccccccccccccccc",
        ]
        got = obs.extract_trace_exemplars(lines, max_traces=2)
        self.assertEqual(
            got,
            [
                {"trace_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "span_id": None},
                {"trace_id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "span_id": None},
            ],
        )


class QueryChartsSeriesTests(unittest.TestCase):
    def test_queries_adapter_and_bounds_series_and_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter_dir = Path(tmp) / "fake-charts"
            adapter_dir.mkdir()
            script = adapter_dir / "query-range.sh"
            script.write_text(
                "#!/bin/sh\n"
                "set -eu\n"
                'python3 - "$1" "$2" "$3" "$4" <<\'PY\'\n'
                "import json, sys\n"
                "q, start, end, step = sys.argv[1:5]\n"
                "points = [[1000 + i, float(i)] for i in range(600)]\n"
                'print(json.dumps({"status": "ok", "series": [{"name": q, "points": points}]}))\n'
                "PY\n",
                encoding="utf-8",
            )
            script.chmod(script.stat().st_mode | stat.S_IXUSR)

            got = obs.query_charts_series(str(adapter_dir), ["traffic", "error_rate"], 1000, 2000, step_s=15)
            self.assertEqual(set(got), {"series"})
            self.assertEqual(len(got["series"]), 2)
            self.assertEqual(got["series"][0]["name"], "traffic")
            self.assertEqual(len(got["series"][0]["points"]), obs.MAX_CHART_POINTS)
            self.assertEqual(got["series"][0]["points"][-1], [1599, 599.0])

    def test_refuses_bad_step_empty_queries_and_adapter_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter_dir = Path(tmp) / "fake-charts"
            adapter_dir.mkdir()
            script = adapter_dir / "query-range.sh"
            script.write_text("#!/bin/sh\necho bad >&2\nexit 1\n", encoding="utf-8")
            script.chmod(script.stat().st_mode | stat.S_IXUSR)

            with self.assertRaises(ValueError):
                obs.query_charts_series(str(adapter_dir), [], 1000, 2000, step_s=15)
            with self.assertRaises(ValueError):
                obs.query_charts_series(str(adapter_dir), ["up"], 1000, 2000, step_s=0)
            with self.assertRaises(RuntimeError):
                obs.query_charts_series(str(adapter_dir), ["up"], 1000, 2000, step_s=15)


class FakeChartsVerb:
    def __init__(self, brk: str | None = None) -> None:
        self.brk = brk

    def __call__(self, name: str, *args: str, data=None, env=None):
        if name != "query-range":
            raise AssertionError(name)
        query, _start, _end, step_s = args
        if env and env.get("CHARTS_URL") and "127.0.0.1:" in env["CHARTS_URL"]:
            if self.brk == "unreachable-succeeds":
                return 0, json.dumps({"status": "ok", "series": []}), ""
            if self.brk == "unreachable-stdout":
                return 1, "error", "unreachable"
            return 7, "", "connection refused"
        if not query.strip():
            if self.brk == "empty-query-succeeds":
                return 0, json.dumps({"status": "ok", "series": []}), ""
            if self.brk == "empty-query-stdout":
                return 1, "bad query", ""
            return 1, "", "query must not be empty"
        try:
            step_val = float(step_s)
        except ValueError:
            step_val = 0.0
        if step_val <= 0:
            if self.brk == "bad-step-succeeds":
                return 0, json.dumps({"status": "ok", "series": []}), ""
            return 1, "", "step_s must be > 0"
        if "absent" in query:
            if self.brk == "empty-not-empty":
                return 0, json.dumps({"status": "ok", "series": [{"name": "x", "points": [[1, 1]]}]}), ""
            return 0, json.dumps({"status": "ok", "series": []}), ""
        if self.brk == "valid-empty":
            return 0, json.dumps({"status": "ok", "series": []}), ""
        if self.brk == "valid-bad-points":
            return 0, json.dumps({"status": "ok", "series": [{"name": "up", "points": [["bad", 1]]}]}), ""
        return 0, json.dumps({"status": "ok", "series": [{"name": "up", "points": [[1700000000, 1.0]]}]}), "ok"


class ChartsConformanceSuiteTests(unittest.TestCase):
    def run_suite(self, brk: str | None = None):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            try:
                got = (
                    "GO",
                    charts.judge(
                        [],
                        env={"CHARTS": "fake-charts", "CONFORMANCE_QUERY": "up"},
                        verb=FakeChartsVerb(brk),
                    ),
                )
            except NoGo as no:
                got = ("NO-GO", no.reason)
        return got, err.getvalue()

    def test_fair_charts_adapter_passes_five_cases(self) -> None:
        got, err = self.run_suite()
        self.assertEqual(got, ("GO", "fake-charts, 5 cases"))
        self.assertIn("case 5 unreachable-endpoint-refused: GO", err)

    def test_each_break_names_its_case(self) -> None:
        cases = (
            ("valid-empty", "case1 valid-range:"),
            ("valid-bad-points", "case1 valid-range:"),
            ("empty-not-empty", "case2 empty-series:"),
            ("bad-step-succeeds", "case3 invalid-step-refused:"),
            ("empty-query-succeeds", "case4 empty-query-refused:"),
            ("empty-query-stdout", "case4 empty-query-refused:"),
            ("unreachable-succeeds", "case5 unreachable-endpoint-refused:"),
            ("unreachable-stdout", "case5 unreachable-endpoint-refused:"),
        )
        for brk, prefix in cases:
            with self.subTest(brk=brk):
                (verdict, reason), _ = self.run_suite(brk)
                self.assertEqual(verdict, "NO-GO", reason)
                self.assertTrue(reason.startswith(prefix), reason)
                self.assertNotIn("\n", reason)

    def test_unset_charts_env_is_refused(self) -> None:
        with self.assertRaises(NoGo):
            charts.judge([], env={})


if __name__ == "__main__":
    unittest.main()
