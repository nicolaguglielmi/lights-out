# Native Cloud Deploy + Cloud Run Delivery Adapter (`DELIVERY=cloud-deploy`)

Native L2/L3 `Delivery` port adapter (`profiles/l2.env`, `profiles/l3.env`) delegating Cloud Deploy (`customCanaryDeployment`) and Cloud Run revision management to [gcp-cloud-deploy](../../plugins/cloud/gcp-cloud-deploy/README.md).

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) against a stateful mock `gcloud` CLI and local HTTP `/healthz`; live Cloud Deploy rollouts require your own GCP project, keyless OIDC identity, and `./go delivery-conformance PROFILE=l2`.

## Required configuration

- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`, must not be empty or `replace-me`)
- `GCP_REGION` (or `GOOGLE_CLOUD_LOCATION`)
- `CLOUD_DEPLOY_PIPELINE`

Optional: `CLOUD_DEPLOY_TARGET` (default `prod`), `CLOUD_RUN_SERVICE` (default `backend`), `GCP_AR_HOST`, `READY_TIMEOUT` (default `180`), `APP_URL`, `CANARY_URL`, `WORKLOAD_HEALTH_PROBE` (default `http:/healthz`).

## Verbs

- `ready.sh`: verifies Cloud Deploy target, Cloud Run service readiness, and HTTP health probe.
- `status.sh`: emits exact 3-key JSON `{"stable": "...", "canary": ... , "weight": 0..100}`.
- `deploy.sh <version> <image>`: creates a Cloud Deploy release staging `<image>` with `<version>` at weight `0`.
- `shift.sh <5|25|100>`: advances the Cloud Deploy rollout phase and verifies Cloud Run canary weight.
- `canary-url.sh`: prints the direct canary revision tag URL.
- `rollback.sh`: idempotently returns `100%` traffic to stable.
- `slo.promql`: OTel app SLO queries (`traffic`, `error_rate`, `latency_p95`) with `target_info`.
