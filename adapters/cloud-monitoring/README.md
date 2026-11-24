# Cloud Monitoring Charts Adapter (`CHARTS=cloud-monitoring`)

`Charts` port implementation querying `MONITORING_API_URL` (`projects.timeSeries.query`) and returning normalized time-series JSON.

> Tested locally in CI against a Python HTTP mock server (`ci/tests/test_obs_charts_e2e.py`) and verified by `CONFORMANCE=1 CHARTS=cloud-monitoring sh gates/charts-conformance.sh`; live Google Cloud Monitoring endpoints require your own `MONITORING_API_URL`, project, and bearer credentials.

## Configuration

- `MONITORING_API_URL` (overridden by `CHARTS_URL` during conformance probes): base `http://` or `https://` URL (no embedded userinfo in URL; posts JSON to `<base>/v3/projects/<project>/timeSeries:query`).
- `MONITORING_PROJECT` (optional, falls back to `GOOGLE_CLOUD_PROJECT`, `GCP_PROJECT`, or `default`): target project identifier.
- `MONITORING_ACCESS_TOKEN` (optional, falls back to `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `MONITORING_TOKEN`, or `MONITORING_AUTH_HEADER`): bearer token sent exclusively on `curl` stdin via `-H @-`, never in `argv` or URLs.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds for `ready.sh`.

## Verbs

- `ready.sh`: probes `projects.timeSeries.query`, exiting `0` silently on success or `1` on timeout.
- `query-range.sh <query> <start> <end> <step_s>`: validates non-empty query and `step_s > 0`, posts `projects.timeSeries.query`, and prints `{"status":"ok","series":[{"name":...,"points":[[ts,val],...]}]}`.
