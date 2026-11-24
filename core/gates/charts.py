# SPDX-License-Identifier: Apache-2.0
"""gates/charts-conformance.sh's suite: the Charts port (ADR 0122).

    python3 -m core.gates.charts

Drives whichever CHARTS adapter is set via <adapter_dir>/query-range.sh.
Five cases in order:
  1 valid-range: query-range.sh <query> <start> <end> <step_s> returns
    {"status": "ok", "series": [{"name": ..., "points": [[ts, val], ...]}]}
  2 empty-series: unmatched query returns {"status": "ok", "series": []}
  3 invalid-step-refused: zero, negative, or non-numeric step_s exits nonzero
    with empty stdout
  4 empty-query-refused: empty query exits nonzero with empty stdout
  5 unreachable-endpoint-refused: unreachable CHARTS_URL exits nonzero with
    empty stdout
"""

from __future__ import annotations

import json
import math
import os
import socket
import time

from . import NoGo, adapter, main, run_id, say


def _is_finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _resolve_adapter_dir(charts_name: str, env) -> str:
    if charts_name.startswith("outside/"):
        outside_name = charts_name[len("outside/") :]
        linked = os.path.join("adapters", "outside", outside_name)
        if os.path.isdir(linked):
            return linked
        base = env.get("ADAPTERS_PATH", "")
        if base and os.path.isdir(os.path.join(base, outside_name)):
            return os.path.join(base, outside_name)
    return os.path.join("adapters", charts_name)


def _parse_range_json(text: str, case_label: str) -> dict:
    try:
        payload = json.loads(text)
    except ValueError:
        raise NoGo(f"{case_label}: query-range.sh stdout is not valid JSON: {text!r}") from None
    if not isinstance(payload, dict):
        raise NoGo(f"{case_label}: query-range.sh output is not a JSON object")
    if payload.get("status") != "ok":
        raise NoGo(f"{case_label}: status={payload.get('status')!r}, want 'ok'")
    series = payload.get("series")
    if not isinstance(series, list):
        raise NoGo(f"{case_label}: 'series' must be a list, got {type(series).__name__}")
    return payload


class Suite:
    def __init__(self, charts_name: str, verb, env) -> None:
        self.charts_name = charts_name
        self.verb = verb
        self.env = env
        self.rid = run_id()
        now = int(time.time())
        self.start = env.get("CONFORMANCE_START") or str(now - 300)
        self.end = env.get("CONFORMANCE_END") or str(now)
        self.step_s = env.get("CONFORMANCE_STEP_S") or "15"
        self.query = env.get("CONFORMANCE_QUERY") or "up"
        self.empty_query = env.get("CONFORMANCE_EMPTY_QUERY") or f"absent_series_{self.rid}"
        say(f"[charts-conformance] run {self.rid} on CHARTS={charts_name}")

    def call(self, name: str, *args: str, env=None):
        if env is None:
            rc, out, err = self.verb(name, *args)
        else:
            rc, out, err = self.verb(name, *args, env=env)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def run(self) -> str:
        rc, out = self.call("query-range", self.query, self.start, self.end, self.step_s)
        if rc != 0:
            raise NoGo(f"case1 valid-range: query-range.sh exited {rc}")
        payload = _parse_range_json(out, "case1 valid-range")
        series = payload["series"]
        if not series:
            raise NoGo("case1 valid-range: expected at least one series, got series=[]")
        for idx, item in enumerate(series):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
                raise NoGo(f"case1 valid-range: series[{idx}] missing non-empty string 'name'")
            pts = item.get("points")
            if not isinstance(pts, list):
                raise NoGo(f"case1 valid-range: series[{idx}].points is not a list")
            for p_idx, pt in enumerate(pts):
                if not isinstance(pt, list) or len(pt) != 2 or not (_is_finite_number(pt[0]) and _is_finite_number(pt[1])):
                    raise NoGo(f"case1 valid-range: series[{idx}].points[{p_idx}]={pt!r}, want [ts, val] numbers")
        if not series[0]["points"]:
            raise NoGo("case1 valid-range: series[0].points is empty")
        say(f"[charts-conformance] case 1 valid-range ({len(series)} series): GO")

        rc, out = self.call("query-range", self.empty_query, self.start, self.end, self.step_s)
        if rc != 0:
            raise NoGo(f"case2 empty-series: query-range.sh exited {rc}")
        payload2 = _parse_range_json(out, "case2 empty-series")
        if payload2["series"] != []:
            raise NoGo(f"case2 empty-series: expected series=[], got {payload2['series']!r}")
        say("[charts-conformance] case 2 empty-series: GO")

        for bad_step in ("0", "-5", "not-a-number"):
            rc, out = self.call("query-range", self.query, self.start, self.end, bad_step)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case3 invalid-step-refused: query-range.sh step={bad_step!r} exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
        say("[charts-conformance] case 3 invalid-step-refused: GO")

        rc, out = self.call("query-range", "", self.start, self.end, self.step_s)
        if rc == 0 or out != "":
            raise NoGo(
                f"case4 empty-query-refused: query-range.sh '' exited {rc} with stdout {out!r}, "
                "want nonzero and empty stdout"
            )
        say("[charts-conformance] case 4 empty-query-refused: GO")

        unreachable_url = f"http://127.0.0.1:{_closed_loopback_port()}"
        unreachable_env = dict(self.env)
        unreachable_env["CHARTS_URL"] = unreachable_url
        unreachable_env["METRICS_URL"] = unreachable_url
        rc, out = self.call("query-range", self.query, self.start, self.end, self.step_s, env=unreachable_env)
        if rc == 0 or out != "":
            raise NoGo(
                f"case5 unreachable-endpoint-refused: query-range.sh on {unreachable_url} exited {rc} "
                f"with stdout {out!r}, want nonzero and empty stdout"
            )
        say("[charts-conformance] case 5 unreachable-endpoint-refused: GO")

        return f"{self.charts_name}, 5 cases"


def judge(argv, env=None, verb=None):
    env = os.environ if env is None else env
    charts_name = env.get("CHARTS", "")
    if not charts_name:
        raise NoGo("CHARTS is not set; set CHARTS=<adapter> (CONFORMANCE=1)")
    if verb is None:
        adapter_dir = _resolve_adapter_dir(charts_name, env)
        script = os.path.join(adapter_dir, "query-range.sh")
        if not os.path.isfile(script):
            raise NoGo(f"{script} is missing: a Charts adapter ships query-range.sh")
        verb = adapter(adapter_dir)
    suite = Suite(charts_name, verb, env)
    return suite.run()


if __name__ == "__main__":
    raise SystemExit(main(judge))
