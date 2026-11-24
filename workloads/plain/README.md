# Plain example workload (ADR 0098)

An uninstrumented Python `http.server` workload with zero OpenTelemetry,
zero `prometheus_client`, zero OpenFeature/flagd dependencies, no `/metrics`,
no JSON `/healthz`, and an empty `WORKLOAD_KILL_SWITCH`.

- Health probe: `WORKLOAD_HEALTH_PROBE=http:/ok`
- Happy path: `GET /` (`200`)
- Broken canary fixture: `.lights-out/fixtures/canary/broken.patch` (`GET /` -> `500`)

Proven via `./go demo-seam-plain`.
