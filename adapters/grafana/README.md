# Grafana Charts Adapter (`CHARTS=grafana`)

`Charts` port implementation querying Grafana / Prometheus-compatible HTTP `/api/v1/query_range` endpoints and returning normalized time-series JSON.

> Tested locally in CI against a Python HTTP mock server (`ci/tests/test_obs_charts_e2e.py`) and verified by `CONFORMANCE=1 CHARTS=grafana sh gates/charts-conformance.sh`; live Grafana endpoints require your own `GRAFANA_URL` (or `METRICS_URL`) and credentials.

## Configuration

- `GRAFANA_URL` (or fallback `METRICS_URL`, overridden by `CHARTS_URL` during conformance probes): base `http://` or `https://` URL (no embedded userinfo in URL; calls `GET <base>/api/v1/query_range`).
- `GRAFANA_TOKEN` (optional): bearer token sent exclusively on `curl` stdin via `-H @-`, never in `argv` or URLs.
- `METRICS_AUTH_HEADER` (optional fallback if `GRAFANA_TOKEN` is unset): single header line passed via `curl -H @-`.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds for `ready.sh`.

## Verbs

- `ready.sh`: polls `/api/health` (falling back to `/api/v1/query?query=up`), exiting `0` silently on success or `1` on timeout.
- `query-range.sh <query> <start> <end> <step_s>`: validates non-empty query and `step_s > 0`, queries `/api/v1/query_range`, and prints `{"status":"ok","series":[{"name":...,"points":[[ts,val],...]}]}`.
