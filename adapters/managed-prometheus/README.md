# Google Cloud Managed Service for Prometheus Adapter (`METRICS=managed-prometheus`)

`Metrics` port implementation querying Google Cloud Managed Service for Prometheus (`${METRICS_URL:-https://monitoring.googleapis.com/v1/projects/$GCP_PROJECT/location/${GCP_PROMETHEUS_LOCATION:-global}/prometheus}/api/v1/query`).

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) against a Python HTTP Prometheus v1 mock server alongside `core/gates/readiness.py`; live Managed Prometheus queries require your own `GCP_PROJECT` and keyless OIDC / ADC credentials.

## Configuration

- `METRICS_URL` (optional when `GCP_PROJECT` is set): base URL to which `/api/v1/query` is appended. Defaults to `https://monitoring.googleapis.com/v1/projects/$GCP_PROJECT/location/${GCP_PROMETHEUS_LOCATION:-global}/prometheus`.
- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`): target GCP project identifier when `METRICS_URL` is omitted.
- `GCP_PROMETHEUS_LOCATION` (optional, default `global`): Managed Prometheus scope location.
- `METRICS_AUTH_HEADER` / `MANAGED_PROMETHEUS_ACCESS_TOKEN` / `MANAGED_PROMETHEUS_AUTH_CMD` (optional; falls back to `GCP_ACCESS_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `gcloud`, or GCE metadata server): sent exclusively to `curl` via stdin (`-H @-`), never in `argv`.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds.

## Verbs

- `ready.sh`: probes `vector(1)` on `/api/v1/query` and checks `.status == "success"`.
- `query.sh '<promql>'`: executes an instant PromQL query and emits raw `/api/v1/query` JSON on stdout (matching `adapters/promql/query.sh`).
