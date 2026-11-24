# SPDX-License-Identifier: Apache-2.0
"""Enterprise observability helpers (docs/adr/0122): stdlib only.

Strictly vendor-free, app-free, and model-free. Provides:
- hold_stage_with_probes(): periodic synthetic smoke probes during stage holds
- evaluate_extended_signals(): optional saturation, restarts, and burn-rate SLOs
- extract_trace_exemplars(): W3C traceparent / trace_id extraction
- query_charts_series(): bounded time-series retrieval via a Charts adapter
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess  # nosec B404 - explicit argv lists for smoke probes and Charts port verbs
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

# Provenance for all defaults: docs/adr/0122, "Provenance".
DEFAULT_PROBE_TIMEOUT_S = 30.0
DEFAULT_SATURATION_MAX = 0.90
DEFAULT_RESTARTS_MAX_DELTA = 0.0
DEFAULT_BURN_RATE_FAST_MAX = 14.4
DEFAULT_BURN_RATE_SLOW_MAX = 6.0
MAX_TRACES = 20
MAX_CHART_SERIES = 50
MAX_CHART_POINTS = 500

_TRACEPARENT_RE = re.compile(
    r"(?i)\btraceparent\s*[:=]\s*[\"']?(?:[0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-(?:[0-9a-f]{2})(?![0-9a-f])"
)
_TRACE_ID_RE = re.compile(r"(?i)\btrace[_-]?id\s*[:=]\s*[\"']?([0-9a-f]{32})(?![0-9a-f])")
_SPAN_ID_RE = re.compile(r"(?i)\bspan[_-]?id\s*[:=]\s*[\"']?([0-9a-f]{16})(?![0-9a-f])")

_ALL_ZERO_TRACE = "0" * 32
_ALL_ZERO_SPAN = "0" * 16


def _normalize_probe_cmd(smoke_cmd: str | Sequence[str] | None) -> list[str]:
    if smoke_cmd is None:
        return []
    if isinstance(smoke_cmd, str):
        parts = shlex.split(smoke_cmd)
    elif isinstance(smoke_cmd, (list, tuple)):
        parts = [str(part) for part in smoke_cmd if str(part)]
    else:
        raise TypeError("smoke_cmd must be a string, sequence of strings, or None")
    if len(parts) == 1 and parts[0].endswith(".sh"):
        return ["sh", parts[0]]
    return parts


def _run_probe(argv: Sequence[str], timeout_s: float) -> tuple[int, str]:
    try:
        proc = subprocess.run(  # nosec B603 - explicit argv list, shell=False
            list(argv),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout_s}s"
    except OSError as exc:
        return 127, str(exc)
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return proc.returncode, (detail[-1].strip() if detail else "")


def hold_stage_with_probes(
    stage_seconds: float,
    smoke_cmd: str | Sequence[str] | None = None,
    interval_s: float = 0,
    max_consecutive_failures: int = 2,
    *,
    probe_timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    _sleep: Callable[[float], None] | None = None,
    _monotonic: Callable[[], float] | None = None,
    _runner: Callable[[Sequence[str]], tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """Hold for `stage_seconds`, running periodic synthetic probes when enabled."""
    if stage_seconds < 0:
        raise ValueError("stage_seconds must be >= 0")
    if max_consecutive_failures < 1:
        raise ValueError("max_consecutive_failures must be >= 1")
    if probe_timeout_s <= 0:
        raise ValueError("probe_timeout_s must be > 0")

    sleep_fn = time.sleep if _sleep is None else _sleep
    mono_fn = time.monotonic if _monotonic is None else _monotonic
    run_fn = (lambda cmd: _run_probe(cmd, probe_timeout_s)) if _runner is None else _runner
    argv = _normalize_probe_cmd(smoke_cmd)

    t0 = mono_fn()
    if interval_s <= 0 or not argv:
        if stage_seconds > 0:
            sleep_fn(float(stage_seconds))
        elapsed = round(max(0.0, mono_fn() - t0), 6)
        return {
            "verdict": "GO",
            "probes_run": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "elapsed_s": elapsed,
            "reason": None,
        }

    if stage_seconds == 0:
        return {
            "verdict": "GO",
            "probes_run": 0,
            "failures": 0,
            "consecutive_failures": 0,
            "elapsed_s": 0.0,
            "reason": None,
        }

    deadline = t0 + float(stage_seconds)
    probes_run = 0
    failures = 0
    consecutive = 0

    while True:
        probes_run += 1
        rc, err = run_fn(argv)
        if rc == 0:
            consecutive = 0
        else:
            failures += 1
            consecutive += 1
            if consecutive >= max_consecutive_failures:
                elapsed = round(max(0.0, mono_fn() - t0), 6)
                suffix = f": {err}" if err else ""
                return {
                    "verdict": "NO-GO",
                    "probes_run": probes_run,
                    "failures": failures,
                    "consecutive_failures": consecutive,
                    "elapsed_s": elapsed,
                    "reason": f"smoke probe failed {consecutive} consecutive times (exit {rc}){suffix}",
                }

        remaining = deadline - mono_fn()
        if remaining <= 0:
            break
        if remaining <= interval_s:
            sleep_fn(remaining)
            break
        sleep_fn(float(interval_s))

    elapsed = round(max(0.0, mono_fn() - t0), 6)
    return {
        "verdict": "GO",
        "probes_run": probes_run,
        "failures": failures,
        "consecutive_failures": consecutive,
        "elapsed_s": elapsed,
        "reason": None,
    }


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return None if not math.isfinite(num) else num


def _policy_threshold(policy: Mapping[str, Any], lower_key: str) -> tuple[bool, float | None, str | None]:
    for key in (lower_key, lower_key.upper()):
        if key in policy:
            raw = policy[key]
            if raw is None or raw == "":
                return False, None, None
            num = _as_number(raw)
            if num is None:
                return True, None, f"invalid policy threshold {key}={raw!r}"
            return True, num, None
    return False, None, None


_EXTENDED_SIGNALS = (
    (
        "saturation_max",
        DEFAULT_SATURATION_MAX,
        ("saturation", "cpu_saturation", "memory_saturation", "mem_saturation"),
        "ceiling",
    ),
    (
        "restarts_max_delta",
        DEFAULT_RESTARTS_MAX_DELTA,
        ("restarts", "container_restarts", "pod_restarts"),
        "delta",
    ),
    (
        "burn_rate_fast_max",
        DEFAULT_BURN_RATE_FAST_MAX,
        ("burn_rate_fast", "burn_rate_1h"),
        "ceiling",
    ),
    (
        "burn_rate_slow_max",
        DEFAULT_BURN_RATE_SLOW_MAX,
        ("burn_rate_slow", "burn_rate_6h"),
        "ceiling",
    ),
)


def evaluate_extended_signals(
    metrics_map: Mapping[str, Mapping[str, Any]] | None,
    canary_v: str,
    stable_v: str,
    policy: Mapping[str, Any] | None,
) -> tuple[bool, list[str]]:
    """Evaluate optional saturation, restart delta, and burn-rate SLO signals."""
    metrics = metrics_map or {}
    cfg = policy or {}
    findings: list[str] = []

    for policy_key, default_limit, metric_keys, mode in _EXTENDED_SIGNALS:
        has_policy, policy_limit, err = _policy_threshold(cfg, policy_key)
        if err:
            findings.append(err)
            continue

        present_keys = [k for k in metric_keys if k in metrics and isinstance(metrics[k], Mapping)]
        if not has_policy and not present_keys:
            continue

        limit = policy_limit if has_policy and policy_limit is not None else default_limit
        if not present_keys:
            findings.append(f"{metric_keys[0]}: no data for canary({canary_v}) while {policy_key} is configured")
            continue

        for mkey in present_keys:
            series = metrics[mkey]
            canary_val = _as_number(series.get(canary_v))
            if canary_val is None:
                findings.append(f"{mkey}: no data for canary({canary_v})")
                continue
            if mode == "delta":
                stable_raw = series.get(stable_v)
                stable_val = 0.0 if stable_raw is None else _as_number(stable_raw)
                if stable_val is None:
                    findings.append(f"{mkey}: invalid data for stable({stable_v})")
                    continue
                delta = canary_val - stable_val
                if delta > limit:
                    findings.append(
                        f"{mkey} canary({canary_v})={canary_val:.3f} stable({stable_v})={stable_val:.3f} "
                        f"delta={delta:.3f} > max={limit:.3f}"
                    )
            elif canary_val > limit:
                findings.append(f"{mkey} canary({canary_v})={canary_val:.3f} > max={limit:.3f}")

    return len(findings) == 0, findings


def _iter_text_chunks(item: Any) -> Iterable[str]:
    if isinstance(item, str):
        yield item
        return
    if isinstance(item, Mapping):
        scalars = [f"{k}={v}" for k, v in item.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
        if scalars:
            yield " ".join(scalars)
        for val in item.values():
            if isinstance(val, (Mapping, list, tuple)):
                yield from _iter_text_chunks(val)
        return
    if isinstance(item, (list, tuple)):
        for sub in item:
            yield from _iter_text_chunks(sub)


def extract_trace_exemplars(lines: Iterable[Any] | str | None, max_traces: int = MAX_TRACES) -> list[dict[str, str | None]]:
    """Extract W3C traceparent / trace_id exemplars deduplicated in input order."""
    if not lines or max_traces <= 0:
        return []
    seq: Iterable[Any] = lines.splitlines() if isinstance(lines, str) else lines

    ordered: list[dict[str, str | None]] = []
    by_trace: dict[str, dict[str, str | None]] = {}

    def record(trace_id: str, span_id: str | None) -> None:
        tid = trace_id.lower()
        if tid == _ALL_ZERO_TRACE:
            return
        sid = span_id.lower() if span_id and span_id.lower() != _ALL_ZERO_SPAN else None
        existing = by_trace.get(tid)
        if existing is not None:
            if existing["span_id"] is None and sid is not None:
                existing["span_id"] = sid
            return
        if len(ordered) >= max_traces:
            return
        entry = {"trace_id": tid, "span_id": sid}
        by_trace[tid] = entry
        ordered.append(entry)

    for item in seq:
        for chunk in _iter_text_chunks(item):
            for match in _TRACEPARENT_RE.finditer(chunk):
                record(match.group(1), match.group(2))
            span_match = _SPAN_ID_RE.search(chunk)
            chunk_span = span_match.group(1) if span_match else None
            for match in _TRACE_ID_RE.finditer(chunk):
                record(match.group(1), chunk_span)

    return ordered


def _normalize_queries(queries: str | Sequence[str] | Mapping[str, str]) -> list[str]:
    if isinstance(queries, str):
        items = [queries]
    elif isinstance(queries, Mapping):
        items = list(queries.values())
    elif isinstance(queries, Iterable):
        items = list(queries)
    else:
        raise ValueError("queries must be a string, sequence, or mapping")
    out = [str(q).strip() for q in items if isinstance(q, str) and q.strip()]
    if not out or len(out) != len(items):
        raise ValueError("queries must contain at least one non-empty query string")
    return out


def query_charts_series(
    adapter_dir: str,
    queries: str | Sequence[str] | Mapping[str, str],
    start_ts: str | int | float,
    end_ts: str | int | float,
    step_s: int | float = 15,
    *,
    max_series: int = MAX_CHART_SERIES,
    max_points: int = MAX_CHART_POINTS,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
) -> dict[str, list[dict[str, Any]]]:
    """Query `<adapter_dir>/query-range.sh` via explicit argv and return bounded series."""
    script = os.path.join(adapter_dir, "query-range.sh")
    if not os.path.isfile(script):
        raise ValueError(f"Charts adapter script not found: {script}")
    query_list = _normalize_queries(queries)
    if start_ts is None or isinstance(start_ts, bool) or str(start_ts).strip() == "":
        raise ValueError("start_ts must be non-empty")
    if end_ts is None or isinstance(end_ts, bool) or str(end_ts).strip() == "":
        raise ValueError("end_ts must be non-empty")
    step_num = _as_number(step_s)
    if step_num is None or step_num <= 0:
        raise ValueError(f"step_s must be > 0 (got {step_s!r})")
    if max_series < 1 or max_points < 1:
        raise ValueError("max_series and max_points must be >= 1")

    step_arg = str(int(step_num)) if float(step_num).is_integer() else str(step_num)
    collected: list[dict[str, Any]] = []

    for query in query_list:
        if len(collected) >= max_series:
            break
        try:
            proc = subprocess.run(  # nosec B603 B607 - explicit argv over adapter query-range.sh
                ["sh", script, query, str(start_ts), str(end_ts), step_arg],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_s,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"query-range.sh failed: {exc}") from None
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"query-range.sh exited {proc.returncode}: {err}")
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise RuntimeError(f"query-range.sh output is not JSON: {exc}") from None
        if not isinstance(payload, dict) or payload.get("status") != "ok" or not isinstance(payload.get("series"), list):
            raise RuntimeError("query-range.sh output does not match {'status': 'ok', 'series': [...]}")

        for item in payload["series"]:
            if len(collected) >= max_series:
                break
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("points"), list):
                raise RuntimeError("query-range.sh series item must have string 'name' and list 'points'")
            points: list[list[float]] = []
            for pt in item["points"]:
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    raise RuntimeError("query-range.sh point must be a [ts, val] pair")
                ts_val = _as_number(pt[0])
                val_num = _as_number(pt[1])
                if ts_val is None or val_num is None:
                    raise RuntimeError("query-range.sh point values must be finite numbers")
                points.append([ts_val, val_num])
            collected.append({"name": item["name"], "points": points[-max_points:]})

    return {"series": collected}
