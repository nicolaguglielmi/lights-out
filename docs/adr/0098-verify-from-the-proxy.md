# 0098 — Verify from the proxy

- **Date**: 2026-11-23
- **Status**: Accepted (amends [ADR 0011](0011-slo-queries-and-metric-shape-gate-at-m1.md), [ADR 0013](0013-launchpad-canary-verify-rollback-at-l0.md), [ADR 0030 §3](0030-workload-port.md), [ADR 0066](0066-a-canary-that-cannot-be-judged.md))

## Context

Under ADR 0030 §3, attaching a workload to `lights-out` required every service to expose Prometheus `/metrics` with `http_server_request_duration_seconds_*` and `target_info{service_version=...}`, `/healthz` returning JSON `{"version": ...}`, and a runtime feature-flag kill switch (`WORKLOAD_KILL_SWITCH`). That contract forced application code to bundle OpenTelemetry, `prometheus_client`, and OpenFeature SDKs just to cross the release gate — blocking plain HTTP or TCP services with zero observability or flag dependencies.

On L0 compose, Envoy sits in front of `backend` (`stable`) and `backend-canary` (`canary`) and already records request completions, 5xx responses, and upstream latency per cluster at `:9901/stats/prometheus`. Reading SLOs and container versions from the proxy and container engine removes instrumentation from the L0 Workload port.

## Decision

1. **Envoy-only `verify/slo.promql`**: `verify/slo.promql` queries Envoy cluster metrics grouped by `envoy_cluster_name`:
   - `traffic`: `sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_completed[1m]))` (counts all completed requests including Envoy-generated 503s when an upstream refuses connections, unlike `envoy_cluster_upstream_rq_total`).
   - `error_rate`: `sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{envoy_response_code_class="5"}[1m])) / sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_completed[1m]))`
   - `latency_p95`: `histogram_quantile(0.95, sum by (envoy_cluster_name, le) (rate(envoy_cluster_upstream_rq_time_bucket[1m]))) / 1000` (converts Envoy's millisecond buckets `0.5`..`3600000` to seconds so `LATENCY_P95_MAX_DELTA=0.25` stays unchanged).
   - **Amendment to ADR 0030 §3, 0011, 0013, 0066**: `/metrics` and `target_info` leave the Workload port on the Envoy path (`app/` may retain them; no Envoy-path gate reads them).

2. **Envoy flush interval & Prometheus target**: `adapters/envoy-canary/envoy.yaml` sets `stats_flush_interval: 1s`, smaller than Prometheus's `5s` `scrape_interval`, so histograms flush within a scrape window. `adapters/prometheus/prometheus.yml` scrapes `job_name: envoy` (`metrics_path: /stats/prometheus`, target `envoy:9901`) and drops `backend:8000` and `backend-canary:8000`. `adapters/prometheus/ready.sh` polls `up{job="envoy"}`.

3. **Optional `WORKLOAD_HEALTH_PROBE` in `workload.env`**: Existing 12-key contracts stay valid; when unset or empty, the default is `http:/healthz`. Valid values are empty, `http:/<path>` (requiring 2xx–3xx), or `tcp` / `tcp:<port>` (default port `8000`). `ci/workloads.sh` validates the syntax, and `core/init.py` preserves `WORKLOAD_HEALTH_PROBE` when present in a committed `workload.env`.

4. **Version from contract/engine, never `/healthz` JSON**:
   - `compose.yaml` defines a `probe` service (`profiles: ["probe"]`, `WORKBENCH_IMAGE`, `networks: [default]`).
   - `adapters/envoy-canary/deploy.sh` sets label `lights-out.version=<version>` on `backend-canary` via a temporary Compose override (`COMPOSE_FILE`) and runs the health probe against `backend-canary` via `probe` inside the Compose network (no `python3` in the workload image, no host port leak, no Envoy stats noise).
   - `adapters/envoy-canary/status.sh` inspects running `backend` and `backend-canary` containers over `$DOCKER_HOST` unix socket (`lights-out.version` label -> `APP_VERSION` env -> image tag/digest) identically on Podman's compat API and Docker.
   - `adapters/envoy-canary/ready.sh` honors `WORKLOAD_HEALTH_PROBE` (`http:/<path>` through `$APP_URL`, or Envoy `/ready` plus TCP connect to `backend:<port>` via `probe`).
   - `core/launchpad.sh` and `Makefile` demo targets read `STABLE_VERSION` from `adapters/$DELIVERY/status.sh` instead of curling `$APP_URL/healthz`.

5. **Optional `WORKLOAD_KILL_SWITCH`**: `WORKLOAD_KILL_SWITCH` may be empty even when `compose.yaml` exists. When empty, `ci/workloads.sh` requires only `release-freeze` in `flags/flags.json`, `verify` reports that rollback returns traffic to stable without a kill switch, and `./go kill-switch` refuses cleanly with that message. `app/` keeps `WORKLOAD_KILL_SWITCH=waitlist`.

6. **Argo Rollouts & Flagger scope limit**: Kubernetes adapters (`adapters/argo-rollouts/slo.promql` and `adapters/flagger/slo.promql`, plus `backend-k8s-stable`/`backend-k8s-canary` in `prometheus.yml` and Flagger's `MetricTemplate`) keep reading application `/metrics` and `target_info` until a Kubernetes proxy/gateway adapter replaces them. `verify/slo.sh` selects `$SLO_PROMQL`, then `adapters/$DELIVERY/slo.promql`, then `verify/slo.promql`, mapping either `envoy_cluster_name` or `service_version`.

7. **Vendor-free derived readiness & metric-shape checks**: `core/gates/readiness.py` derives its readiness query from the active `slo.promql` (`count(target_info)` when `target_info` appears, else `count(<traffic metric>)`). `verify/metric-shape.sh` verifies scraped Prometheus series and per-metric selector labels against the active `slo.promql` instead of curling `$APP_URL/metrics` on the proxy path.

8. **Uninstrumented proof**: `workloads/plain/` (minimal Python stdlib `http.server`, zero OTel/prometheus_client/OpenFeature, `WORKLOAD_KILL_SWITCH=`, `WORKLOAD_HEALTH_PROBE=http:/ok`) proves autonomous canary rollback without app instrumentation via `./go demo-seam-plain`.

## Alternatives considered

- **Keep app `/metrics` mandatory and inject sidecars into attached repos**: Rejected — sidecar auto-injection reinvents Kubernetes in Compose and adds container sprawl.
- **Query Envoy admin `/stats?format=prometheus` directly from `verify/slo.sh` without Prometheus**: Rejected — Envoy's raw Prometheus endpoint exposes cumulative counters without `[1m]` rate evaluation or shared storage with Kubernetes adapters.
- **Migrate Argo Rollouts and Flagger in the same milestone**: Rejected — Kubernetes L2/L3 stacks have no Envoy edge proxy container; separating them preserves a small, verifiable diff and documents the boundary explicitly.

## Consequences

- **Positive**: Any HTTP or TCP service in a container attaches cleanly via a `Containerfile`, `compose.yaml`, and `workload.env` — zero metrics library, zero `/healthz` JSON schema, and optional feature flags.
- **Trade-offs & edge conditions**:
  - A fresh Envoy process emits `envoy_cluster_upstream_rq_xx` and `envoy_cluster_upstream_rq_time_bucket` only after its first upstream request (`envoy_cluster_upstream_rq_completed` starts at `0`), and `envoy_response_code_class="5"` appears only after a 5xx; smoke traffic must run before readiness/metric-shape gates, and label checks in `metric-shape.sh` must bind selectors to their own metric rather than checking existence of the `"5"` label value pre-fault.
  - 100% connection-refused upstream 503s omit `rq_time` buckets; `verify/slo.sh` evaluates `traffic`, then `error_rate`, then `latency_p95` in order so broken upstreams trip `error_rate` before `latency_p95` runs.
  - Proxy latency includes L0 hop overhead; it remains small on the bridge network relative to `LATENCY_P95_MAX_DELTA=0.25`.

## Golden-rule check

Does not increase L0 demo runtime (eliminates app OTel/prometheus dependencies in `workloads/plain/`; keeps `STAGE_SECONDS=60` and `stats_flush_interval: 1s`). Measured timings recorded under `## After`.

## Provenance

| Parameter / Query | Value | Provenance |
|---|---|---|
| Envoy image | `envoyproxy/envoy:v1.39.1` | Empirical check on `:9901/stats/prometheus` (2026-11-23) |
| Volume metric | `envoy_cluster_upstream_rq_completed` | Empirical check: includes Envoy 503s on unreachable upstream |
| Error metric | `envoy_cluster_upstream_rq_xx{envoy_response_code_class="5"}` | Empirical check on v1.39.1 |
| Latency buckets | `envoy_cluster_upstream_rq_time_bucket` (`0.5`..`3600000` ms) `/ 1000` | Empirical check + ADR 0013 `0.25` s threshold unchanged |
| `stats_flush_interval` | `1s` | Decision: `<` Prometheus `5s` scrape interval |
| Argo Rollouts / Flagger `slo.promql` | `http_server_request_duration_seconds_*` + `target_info` | Retained per owner decision; tracked limit in adapter READMEs |

## After

Measured in Podman after all M65 edits (2026-11-23):

- `./go demo-seam-plain`: `GO: demo-seam-plain` in **137 s** (`workloads/plain/`, canary `0.2.0-broken` rolled back at 5% with `error_rate canary=1.000 stable=0.000 delta=1.000 max=0.010`, `WORKLOAD_KILL_SWITCH=` empty, `WORKLOAD_HEALTH_PROBE=http:/ok`).
- `./go demo-seam`: `GO: demo-seam` in **182 s** (`workloads/minimal/`, `error_rate` rollback + `demo-gates`).
- `./go demo`: `GO: demo, milestone M9, 517s to rollback (budget 900s, plan §2)` (total wall time **518 s**; `[4/7]` rollback 63 s with `error_rate canary(0.2.0-waitlist)=0.994 stable(0.1.0-m2)=0.000`; `[5/7]` ticket 0001 end-to-end).
- `./go delivery-conformance`: `GO: delivery-conformance` (cases 1–6 on `envoy-canary` all `GO`).
- `./go ci`: `GO: ci` (`0` NO-GOs; `core-tests` 1807 + 25 + 61, `evals` 82).
