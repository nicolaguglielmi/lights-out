# GCP Cloud Deploy + Cloud Run Delivery Plugin

External Delivery plugin (`DELIVERY=outside/gcp-cloud-deploy`) driving Google Cloud Deploy (`customCanaryDeployment`) and Cloud Run traffic splits via `gcloud`.

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/gcp-cloud-deploy ADAPTERS_PATH=...`.

## Setup

Stage Terraform and OIDC templates into `.crew/cloud/gcp-cloud-deploy/`:

```sh
./go cloud-setup CLOUD=gcp-cloud-deploy
```

Review `infra/main.tf`, `infra/variables.tf`, and `infra/outputs.tf`, then apply with your `billing_account` and repository identity.

## SLO modes & Cloud E2E bridge

- **Proxy SLO mode (default, `SLO_MODE=proxy`)**: point `SLO_PROMQL=plugins/cloud/gcp-cloud-deploy/slo-ingress.promql` at Envoy / Cloud Service Mesh upstream cluster metrics (`envoy_cluster_upstream_rq_*` grouped by `envoy_cluster_name`); requires no app `/metrics` or `target_info`.
- **OTel SLO mode (`SLO_MODE=otel`)**: point `SLO_PROMQL=plugins/cloud/gcp-cloud-deploy/slo.promql` at app-emitted OpenTelemetry HTTP metrics with `target_info`.
- **Remote Registry & Flags**: pair with `REGISTRY=oci-registry` (`OCI_REGISTRY_REPO=europe-west1-docker.pkg.dev/<project>/<repo>/<service>`, see [oci-registry](../../../adapters/oci-registry/README.md)) and daemonless `FLAGS=flags-file` ([flags-file](../../../adapters/flags-file/README.md)) or `FLAGS=flags-http` ([flags-http](../../../adapters/flags-http/README.md)). See [ADR 0107](../../../docs/adr/0107-cloud-registry-flags-and-proxy-slos.md).

## Required environment variables

- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`, must not be empty or `replace-me`)
- `GCP_REGION` (or `GOOGLE_CLOUD_LOCATION`)
- `CLOUD_DEPLOY_PIPELINE`

Optional: `CLOUD_DEPLOY_TARGET` (default `prod`), `CLOUD_RUN_SERVICE` (default `backend`), `GCP_AR_HOST`, `READY_TIMEOUT` (default `180`), `APP_URL`, `CANARY_URL`.

## Authentication and security

- Keyless OIDC only via Workload Identity Federation (`ci/github-oidc.yml`, `ci/gitlab-oidc.yml`).
- `env.sh` refuses `GOOGLE_APPLICATION_CREDENTIALS` and refuses static credential files (`*.json` service account keys, `*.pem`, `.aws/credentials`) inside `$WORKLOAD`.
- Least-privilege Terraform IAM grants only Cloud Deploy releaser, Cloud Run developer, Artifact Registry writer, and scoped runtime service account user permissions, paired with a monthly `google_billing_budget` (default `50` USD, alerts at `50%` and `100%`).

## Conformance

See [ADR 0104](../../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md) and [ADR 0058](../../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md). Run live against your GCP project:

```sh
./go delivery-conformance DELIVERY=outside/gcp-cloud-deploy ADAPTERS_PATH=/abs/path/to/plugins/cloud
```
