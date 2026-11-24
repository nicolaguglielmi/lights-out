# ADR 0122 — Enterprise Observability: Synthetic Stage Probes, Extended SLO Signals, W3C Trace Anchors, and Charts Port

- Status: accepted
- Date: 2026-11-24
- Milestone: M82 (Enterprise Observability, Synthetic Stage Probes & `Charts` Port)
- Relates to:
  - [ADR 0013](0013-launchpad-canary-verify-rollback-at-l0.md) (launchpad canary hold and SLO gate)
  - [ADR 0023](0023-console-at-m11.md) & [ADR 0047](0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) (`Charts` port origin and deferral)
  - [ADR 0044](0044-post-mortem-incident-and-promql-metrics.md) (incident document and anchored post-mortem)
  - [ADR 0066](0066-a-canary-that-cannot-be-judged.md) (fail-closed `cannot judge` semantics)
  - [ADR 0084](0084-gates-under-the-shell-boundary.md) (30-line shell boundary and `core/gates/*.py`)
  - [ADR 0121](0121-closed-deterministic-self-healing-loop.md) (deterministic rollback evidence capture)

## Context

`verify/slo.sh` evaluates three PromQL signals (`traffic`, `error_rate`, `latency_p95`) once at the end of a `STAGE_SECONDS` sleep in `core/launchpad.sh`. Three observability gaps limit enterprise deployments:

1. **Silent regressions during low-traffic holds.** Sleeping blindly for `STAGE_SECONDS` detects neither deterministic synthetic smoke failures nor early canary breakage until the entire stage window elapses.
2. **Narrow signal surface.** Error-rate and p95 latency deltas alone miss resource saturation (CPU/memory throttling approaching limits), container crash-loop restarts relative to stable, and sustained multi-window error-budget burn rates.
3. **Trace blind spots and missing time-series port.** Incident documents capture log lines and alerts, yet cannot anchor post-mortem timeline entries to W3C `traceparent` / `trace_id` exemplars present in those logs and alerts. Meanwhile, no `Charts` port conformance suite or range-query adapters exist for dashboards/telemetry consumers.

All new capabilities must remain opt-in or additive so existing minimal workloads, `ci/postmortem.sh` byte-for-byte incident checks, and all meta-gates (`ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`, `ci/shell-boundary.sh`, `ci/contract.sh`) stay green.

## Decision

### 1. Observability Core Module (`core/obs.py`)

Implemented in Python standard library only, strictly vendor-free, app-free, and model-free:

- **`hold_stage_with_probes(stage_seconds, smoke_cmd=None, interval_s=0, max_consecutive_failures=2)`**:
  - When `interval_s <= 0` or `smoke_cmd` is unset/empty, sleeps `stage_seconds` and returns `GO` with `probes_run=0`.
  - When `interval_s > 0` and `smoke_cmd` is set, invokes `smoke_cmd` via an explicit `subprocess.run` argument list (never `shell=True`) at `t=0` and every `interval_s` step across `stage_seconds`.
  - A transient non-zero probe exit increments `failures` and `consecutive_failures`; a zero exit resets `consecutive_failures` to `0`.
  - Reaching `max_consecutive_failures` consecutive non-zero exits aborts the hold immediately with `verdict: "NO-GO"`, elapsed time, counts, and a failure reason.
  - Returns `{"verdict": "GO"|"NO-GO", "probes_run": int, "failures": int, "consecutive_failures": int, "elapsed_s": float, "reason": str|None}`.

- **`evaluate_extended_signals(metrics_map, canary_v, stable_v, policy)`**:
  - Optional checks for `saturation_max` (CPU/memory utilization ratio ceiling on canary across `saturation`, `cpu_saturation`, `memory_saturation`, `mem_saturation`), `restarts_max_delta` (canary minus stable restarts across `restarts`, `container_restarts`, `pod_restarts`), and multi-window error-budget burn rates (`burn_rate_fast_max` for `burn_rate_fast`/`burn_rate_1h`; `burn_rate_slow_max` for `burn_rate_slow`/`burn_rate_6h`).
  - Silently skips any signal absent from both `policy` and `metrics_map` so minimal `{traffic, error_rate, latency_p95}` setups never regress.
  - Fails closed (`cannot judge` / missing data finding) if a signal threshold is explicitly configured in `policy` while the metric lacks canary data, and uses documented default limits when a metric is supplied in `metrics_map` without an explicit `policy` override.
  - Returns `(ok: bool, findings: list[str])`.

- **`extract_trace_exemplars(lines, max_traces=20)`**:
  - Extracts W3C `traceparent=00-<32hex>-<16hex>-..` (or colon/JSON variants) and `trace_id[=:]\s*([0-9a-f]{32})` (plus optional `span_id[=:]\s*([0-9a-f]{16})`) from log strings and alert structures.
  - Rejects all-zero W3C trace/span IDs (`0` * 32 / `0` * 16) and guards against 40-hex commit SHAs via negative hex lookahead.
  - Normalizes hex to lowercase, deduplicates by `trace_id` preserving first-seen order (backfilling `span_id` if a later line supplies one for an already-seen `trace_id`), and caps output at `max_traces`.

- **`query_charts_series(adapter_dir, queries, start_ts, end_ts, step_s=15)`**:
  - Validates non-empty `queries`, valid `start_ts`/`end_ts`, and `step_s > 0`.
  - Invokes `<adapter_dir>/query-range.sh <query> <start_ts> <end_ts> <step_s>` via explicit argv `subprocess.run` (never `shell=True`).
  - Parses adapter JSON `{"status": "ok", "series": [...]}` and returns bounded `{"series": [...]}` capped at `MAX_CHART_SERIES` series and `MAX_CHART_POINTS` points per series.

### 2. W3C Trace Exemplars and Anchors (`core/incident.py`, `core/postmortem.py`)

- `core/incident.py` calls `extract_trace_exemplars` over `logs` and `alerts` fields. To preserve byte-for-byte compatibility with `ci/postmortem.sh` and existing `core/tests/test_incident.py` / `core/tests/test_ops_loop.py` key checks, `"traces"` is added to the incident document **only** when one or more traces are extracted; when none are found, the key set remains `["kind", "name", "window", "release", "alerts", "logs", "chain"]`.
- `core/postmortem.py` validates optional `"traces"` (must be a `list` if present) and extends `anchor_evidence()` to accept `trace:<1-based-index>` or `trace:<32hex>` when `doc.get("traces")` contains that index or trace ID, refusing fabricated or malformed trace anchors cleanly.

### 3. `Charts` Port Contract & Conformance Gate (`gates/charts-conformance.sh`, `core/gates/charts.py`)

- Port verb contract:
  - `query-range.sh <query> <start> <end> <step_s>` exits `0` and writes one JSON object `{"status":"ok","series":[{"name":"...","points":[[ts,val],...]}]}` on stdout; on refusal/failure it writes diagnostics to stderr, leaves stdout empty, and exits non-zero.
  - `ready.sh` polls endpoint readiness up to `READY_TIMEOUT` seconds, exiting `0` silently on success or `1` with one stderr line on timeout.
- `gates/charts-conformance.sh` (<= 15 code lines, `+x`) fails cold with `NO-GO: charts-conformance — set CONFORMANCE=1: ...` unless `CONFORMANCE=1`, then delegates to `verdict core.gates.charts`.
- `core/gates/charts.py` runs five deterministic conformance cases:
  1. `valid-range`: non-empty query returns `status == "ok"` and non-empty `series` with numeric `[ts, val]` points.
  2. `empty-series`: unmatched query returns `status == "ok"` and `series == []`.
  3. `invalid-step-refused`: `0`, negative, and non-numeric `step_s` exit non-zero with empty stdout.
  4. `empty-query-refused`: empty query `""` exits non-zero with empty stdout.
  5. `unreachable-endpoint-refused`: overriding `CHARTS_URL` to a closed `127.0.0.1` ephemeral port exits non-zero with empty stdout (keeping `core/` strictly vendor-free).

### 4. `Charts` Port Adapters (`adapters/grafana/`, `adapters/cloud-monitoring/`)

- `adapters/grafana/`: queries `$GRAFANA_BASE_URL/api/v1/query_range` (from `CHARTS_URL` > `GRAFANA_URL` > `METRICS_URL`), authenticating via optional `GRAFANA_TOKEN` or `METRICS_AUTH_HEADER` passed exclusively through `curl -H @-` on stdin, and normalizes Prometheus matrix responses into `{"status":"ok","series":[...]}`.
- `adapters/cloud-monitoring/`: queries `$CM_BASE_URL/v3/projects/<project>/timeSeries:query` (from `CHARTS_URL` > `MONITORING_API_URL`), authenticating via optional `MONITORING_ACCESS_TOKEN` / `MONITORING_AUTH_HEADER` passed exclusively through `curl -H @-` on stdin, and normalizes `timeSeriesData` / `timeSeries` responses into `{"status":"ok","series":[...]}`.

## Provenance

| Threshold / Bound | Value | Provenance |
| --- | --- | --- |
| `max_consecutive_failures` default | `2` | Decision: tolerates a single transient probe hiccup while aborting early on persistent stage breakage (`1` would fail on a single blip; `>=3` delays rollback). |
| `probe_timeout_s` default | `30.0` | Derived: half of `READY_TIMEOUT` (`60s` across `adapters/*/env.sh`), ensuring a stalled smoke script fails within a standard `STAGE_SECONDS=60` stage. |
| `saturation_max` default | `0.90` | External / Decision: 90% CPU/memory limit ratio leaves 10% headroom for runtime GC and burst scheduling before kernel throttling or OOM kill. |
| `restarts_max_delta` default | `0` | Decision: canary must introduce zero additional container restarts compared to stable over the verification window. |
| `burn_rate_fast_max` default | `14.4` | External: Google SRE Workbook, Ch. 5 ("Alerting on SLOs") — 2% of a 30-day error budget burned in 1 hour (`(0.02 * 720) / 1 = 14.4`). |
| `burn_rate_slow_max` default | `6.0` | External: Google SRE Workbook, Ch. 5 ("Alerting on SLOs") — 5% of a 30-day error budget burned in 6 hours (`(0.05 * 720) / 6 = 6.0`). |
| `max_traces` default | `20` | Decision: bounds incident document size for `ops-triage` prompts and the 64 KiB webhook budget of ADR 0109 (`20` ~120-byte objects < 3 KiB). |
| W3C `trace_id` / `span_id` hex length | `32` / `16` hex | External: W3C Trace Context Level 1 specification (`16-byte` trace-id and `8-byte` parent-id, all-zero values invalid). |
| `step_s` default | `15` | Derived: `3x` the `5s` `scrape_interval` configured in `adapters/prometheus/prometheus.yml`, guaranteeing multiple raw samples per step bucket. |
| `MAX_CHART_SERIES` / `MAX_CHART_POINTS` | `50` / `500` | Decision: bounds series cardinality (`50`) and point resolution (`500` steps = ~2.1 hours at `15s` step) for deterministic logbook/console serialization. |
| `gates/charts-conformance.sh` line limit | `<= 30` (`7` code lines) | External: ADR 0052 §4 and ADR 0084 §5 enforced by `ci/shell-boundary.sh`. |
| Adapter HTTP timeouts (`--connect-timeout` / `--max-time` / `READY_TIMEOUT`) | `2s` / `5s` / `60s` | Derived: matches `adapters/flags-http/env.sh` (`2s`/`5s`/`60s`) and `adapters/prometheus/ready.sh` (`60s`). |

## Consequences

- Stage holds can run continuous synthetic probes and fail fast without altering `core/launchpad.sh` or `verify/slo.sh` yet; Wave B integration wires CLI/console/Makefile consumers later.
- Existing `core/incident.py` and `ci/postmortem.sh` runs stay byte-identical when logs/alerts have no trace IDs, while traced runs gain verifiable `trace:` anchors.
- `core/` remains 100% vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), model-free (`ci/model-free.sh`), and under the 30-line shell boundary (`ci/shell-boundary.sh`).

## Alternatives considered

- **Always include `"traces": []` in `core/incident.py`.** Rejected: breaks existing exact-key assertions in `core/tests/test_incident.py`, `core/tests/test_ops_loop.py`, and `ci/postmortem.sh` without adding information when no traces exist.
- **Pass endpoint URL overrides per vendor in `core/gates/charts.py` (e.g. `GRAFANA_URL` / `MONITORING_API_URL`).** Rejected: leaks adapter vendor identifiers into `core/`, violating ADR 0024 / `ci/vendor-free.sh`. A single port-level `CHARTS_URL` precedence rule keeps `core/` completely vendor-agnostic.
- **Run `smoke_cmd` via `shell=True`.** Rejected: introduces shell-injection risk and violates ADR 0084 / `bandit` (`B602`) SAST checks.
