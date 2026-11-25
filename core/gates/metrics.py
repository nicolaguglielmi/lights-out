# SPDX-License-Identifier: Apache-2.0
"""gates/metrics-conformance.sh's suite: the Metrics port (ADR 0044, ADR 0133).

    python3 -m core.gates.metrics

Drives whichever METRICS adapter is set via query.sh (and optional ready.sh).
Five cases in order:
  1 ready: ready.sh exits 0 with empty stdout (skipped when adapter has no ready.sh)
  2 valid-query: query.sh <query> returns {"status":"success","data":{"result":[...]}}
    with >= 1 sample and valid [timestamp, numeric_value] pairs
  3 empty-result: unmatched query returns {"status":"success","data":{"result":[]}}
  4 invalid-query-refused: empty or malformed query exits nonzero with empty stdout
  5 unreachable-endpoint-refused: unreachable METRICS_URL exits nonzero with empty stdout
"""

from __future__ import annotations

import json
import math
import os
import re
import socket

from . import NoGo, adapter, main, run_id, say

ADAPTER_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _is_finite_number(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _is_sample_number(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, str):
        try:
            float(value)
            return True
        except ValueError:
            return False
    return False


def resolve_adapter_dir(metrics_name: str, env) -> str:
    """Resolve METRICS to an in-repo directory or outside/<name> path."""
    if not metrics_name:
        raise NoGo("METRICS is not set; set METRICS=<adapter> (CONFORMANCE=1)")
    if metrics_name.startswith("outside/"):
        name = metrics_name[len("outside/"):]
        if not ADAPTER_NAME.fullmatch(name):
            raise NoGo(f"METRICS={metrics_name} is invalid: outside adapter name must match [a-z0-9][a-z0-9-]*")
        linked = os.path.join("adapters", "outside", name)
        if os.path.isdir(linked):
            return linked
        adapters_path = env.get("ADAPTERS_PATH", "")
        if not adapters_path or not os.path.isabs(adapters_path) or not os.path.isdir(adapters_path):
            raise NoGo(
                f"METRICS={metrics_name} has no {linked} link and ADAPTERS_PATH [{adapters_path}] "
                "is not an absolute directory"
            )
        candidate = os.path.join(adapters_path, name)
        if not os.path.isdir(candidate):
            raise NoGo(f"METRICS={metrics_name} adapter directory not found at {candidate}")
        return candidate
    if not ADAPTER_NAME.fullmatch(metrics_name):
        raise NoGo(f"METRICS={metrics_name} is invalid: adapter name must match [a-z0-9][a-z0-9-]*")
    candidate = os.path.join("adapters", metrics_name)
    if not os.path.isdir(candidate):
        raise NoGo(f"{candidate} does not exist")
    return candidate


def _parse_query_json(text: str, case_label: str) -> list:
    try:
        payload = json.loads(text)
    except ValueError:
        raise NoGo(f"{case_label}: query.sh stdout is not valid JSON: {text!r}") from None
    if not isinstance(payload, dict):
        raise NoGo(f"{case_label}: query.sh output is not a JSON object")
    if payload.get("status") != "success":
        raise NoGo(f"{case_label}: status={payload.get('status')!r}, want 'success'")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise NoGo(f"{case_label}: 'data' must be a JSON object")
    result = data.get("result")
    if not isinstance(result, list):
        raise NoGo(f"{case_label}: 'data.result' must be a list, got {type(result).__name__}")
    return result


class Suite:
    def __init__(self, metrics_name: str, verb, env, *, has_ready: bool = False) -> None:
        self.metrics_name = metrics_name
        self.verb = verb
        self.env = env
        self.has_ready = has_ready
        self.rid = run_id()
        self.query = env.get("CONFORMANCE_QUERY") or "up"
        self.empty_query = env.get("CONFORMANCE_EMPTY_QUERY") or f"absent_metric_{self.rid}"
        self.bad_query = env.get("CONFORMANCE_BAD_QUERY") or "invalid(((query"
        self.allow_empty = env.get("CONFORMANCE_ALLOW_EMPTY") == "1"
        say(f"[metrics-conformance] run {self.rid} on METRICS={metrics_name}")

    def call(self, name: str, *args: str, env=None):
        if env is None:
            rc, out, err = self.verb(name, *args)
        else:
            rc, out, err = self.verb(name, *args, env=env)
        for line in err.splitlines():
            say(f"  [{name}] {line}")
        return rc, out

    def run(self) -> str:
        if self.has_ready:
            rc, out = self.call("ready")
            if rc != 0 or out != "":
                raise NoGo(f"case1 ready: ready.sh exited {rc} with stdout {out!r}, want exit 0 and empty stdout")
            say("[metrics-conformance] case 1 ready: GO")
        else:
            say("[metrics-conformance] case 1 ready: skipped (no ready.sh)")

        rc, out = self.call("query", self.query)
        if rc != 0:
            raise NoGo(f"case2 valid-query: query.sh {self.query!r} exited {rc}")
        result = _parse_query_json(out, "case2 valid-query")
        if not self.allow_empty and not result:
            raise NoGo("case2 valid-query: expected at least one sample, got data.result=[]")
        for idx, item in enumerate(result):
            if not isinstance(item, dict) or not isinstance(item.get("metric"), dict):
                raise NoGo(f"case2 valid-query: data.result[{idx}] missing dict 'metric'")
            val = item.get("value")
            if not isinstance(val, list) or len(val) != 2 or not _is_finite_number(val[0]) or not _is_sample_number(val[1]):
                raise NoGo(f"case2 valid-query: data.result[{idx}].value={val!r}, want [ts, numeric_value]")
        say(f"[metrics-conformance] case 2 valid-query ({len(result)} sample(s)): GO")

        rc, out = self.call("query", self.empty_query)
        if rc != 0:
            raise NoGo(f"case3 empty-result: query.sh {self.empty_query!r} exited {rc}")
        empty_res = _parse_query_json(out, "case3 empty-result")
        if empty_res != []:
            raise NoGo(f"case3 empty-result: expected data.result=[], got {empty_res!r}")
        say("[metrics-conformance] case 3 empty-result: GO")

        for bad_q, desc in (("", "empty query"), (self.bad_query, "malformed query")):
            rc, out = self.call("query", bad_q)
            if rc == 0 or out != "":
                raise NoGo(
                    f"case4 invalid-query-refused: query.sh on {desc} exited {rc} with stdout {out!r}, "
                    "want nonzero and empty stdout"
                )
        say("[metrics-conformance] case 4 invalid-query-refused: GO")

        port = _closed_loopback_port()
        unreachable_url = f"http://127.0.0.1:{port}"
        unreachable_env = dict(self.env)
        unreachable_env["METRICS_URL"] = unreachable_url
        unreachable_env["PLANE_HOST"] = "127.0.0.1"
        unreachable_env["METRICS_PORT"] = str(port)
        unreachable_env.pop("GCP_PROJECT", None)
        unreachable_env.pop("GOOGLE_CLOUD_PROJECT", None)
        rc, out = self.call("query", self.query, env=unreachable_env)
        if rc == 0 or out != "":
            raise NoGo(
                f"case5 unreachable-endpoint-refused: query.sh on {unreachable_url} exited {rc} "
                f"with stdout {out!r}, want nonzero and empty stdout"
            )
        say("[metrics-conformance] case 5 unreachable-endpoint-refused: GO")

        suffix = "" if self.has_ready else " (no ready.sh)"
        return f"{self.metrics_name}, 5 cases{suffix}"


def judge(argv, env=None, verb=None, *, has_ready=None):
    _ = argv
    env = os.environ if env is None else env
    metrics_name = env.get("METRICS", "")
    if not metrics_name:
        raise NoGo("METRICS is not set; set METRICS=<adapter> (CONFORMANCE=1)")
    if verb is None:
        adapter_dir = resolve_adapter_dir(metrics_name, env)
        script = os.path.join(adapter_dir, "query.sh")
        if not os.path.isfile(script):
            raise NoGo(f"{script} is missing: a Metrics adapter ships query.sh")
        if has_ready is None:
            has_ready = os.path.isfile(os.path.join(adapter_dir, "ready.sh"))
        verb = adapter(adapter_dir)
    elif has_ready is None:
        has_ready = False
    suite = Suite(metrics_name, verb, env, has_ready=has_ready)
    return suite.run()


if __name__ == "__main__":
    raise SystemExit(main(judge))
