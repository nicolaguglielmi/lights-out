# Cloud Delivery Plugins (`outside/*`) & Managed L2/L3 Adapters

Four external Delivery plugins implementing the `lights-out` Delivery port (`ready.sh`, `deploy.sh`, `shift.sh`, `rollback.sh`, `status.sh`, `canary-url.sh`, `slo.promql`) via [ADR 0058](../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md) (`ADAPTERS_PATH` and `DELIVERY=outside/<provider>`) and [ADR 0104](../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md), paired with native managed L2/L3 GCP adapters ([ADR 0128](../../docs/adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)) matching [profiles/l2.env](../../profiles/l2.env) and [profiles/l3.env](../../profiles/l3.env).

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/<provider> ADAPTERS_PATH=...`.

## Available external plugins (`outside/*`)

| Provider | Target platform | CLIs used | Guide |
|---|---|---|---|
| `gcp-cloud-deploy` | Google Cloud Run + Cloud Deploy | `gcloud`, `jq`, `curl` | [gcp-cloud-deploy/README.md](gcp-cloud-deploy/README.md) |
| `aws-ecs-codedeploy` | AWS ECS Fargate + CodeDeploy + ALB | `aws`, `jq`, `curl` | [aws-ecs-codedeploy/README.md](aws-ecs-codedeploy/README.md) |
| `azure-container-apps` | Azure Container Apps (multi-revision) | `az`, `jq`, `curl` | [azure-container-apps/README.md](azure-container-apps/README.md) |
| `hetzner-k3s` | Hetzner Cloud k3s + Argo Rollouts | `kubectl`, `hcloud`, `jq`, `curl` | [hetzner-k3s/README.md](hetzner-k3s/README.md) |

## Native L2/L3 managed adapters (`PROFILE=l2`, `PROFILE=l3`)

| Port | Value | Implementation | Guide |
|---|---|---|---|
| `EVENT_BUS` | `pubsub` | Google Cloud Pub/Sub REST v1 | [adapters/pubsub/README.md](../../adapters/pubsub/README.md) |
| `STATE_STORE` | `firestore` | Cloud Firestore REST v1 | [adapters/firestore/README.md](../../adapters/firestore/README.md) |
| `REGISTRY` | `artifact-registry` | Artifact Registry (`*-docker.pkg.dev`) -> `oci-registry` | [adapters/artifact-registry/README.md](../../adapters/artifact-registry/README.md) |
| `METRICS` | `managed-prometheus` | Managed Service for Prometheus `/api/v1/query` | [adapters/managed-prometheus/README.md](../../adapters/managed-prometheus/README.md) |
| `DELIVERY` | `cloud-deploy` | Native wrapper over `gcp-cloud-deploy` | [adapters/cloud-deploy/README.md](../../adapters/cloud-deploy/README.md) |
| `ORCHESTRATOR` | `cloud-workflows` | L3 Google Cloud Workflows Executions REST v1 | [adapters/cloud-workflows/README.md](../../adapters/cloud-workflows/README.md) |

## Quick start

Stage the Terraform + OIDC starter files for a provider into `.crew/cloud/<provider>/` without touching your workload repository (`SLO_MODE=proxy` is the default, selecting `slo-ingress.promql`; pass `SLO_MODE=otel` or `--slo-mode otel` for `slo.promql` when your app exports `/metrics` and `target_info`):

```sh
./go cloud-setup CLOUD=gcp-cloud-deploy
```

Then provision infrastructure with Terraform, configure OIDC federation in your CI provider (`ci/github-oidc.yml` or `ci/gitlab-oidc.yml`), pair with the remote Registry (`REGISTRY=oci-registry`, see [oci-registry](../../adapters/oci-registry/README.md), or `REGISTRY=artifact-registry` at L2/L3) and daemonless Flags (`FLAGS=flags-file` in [flags-file](../../adapters/flags-file/README.md) or `FLAGS=flags-http` in [flags-http](../../adapters/flags-http/README.md)), and verify conformance against your real cloud account:

```sh
./go delivery-conformance DELIVERY=outside/gcp-cloud-deploy ADAPTERS_PATH=/abs/path/to/plugins/cloud
CONFORMANCE=1 REGISTRY=oci-registry sh gates/registry-conformance.sh
CONFORMANCE=1 FLAGS=flags-file sh gates/flags-conformance.sh
```

## Nightly keyless OIDC conformance workflow

[.github/workflows/cloud-conformance.yml](../../.github/workflows/cloud-conformance.yml) runs on a nightly schedule and via `workflow_dispatch` with `id-token: write` permissions. Forks and unconfigured repositories skip cleanly; set repository variable `CLOUD_CONFORMANCE_ENABLED=true` together with your OIDC target variables (`GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`, `GCP_PROJECT`, `GCP_REGION`, `AWS_DEPLOY_ROLE_ARN`, `AZURE_CLIENT_ID`, `OCI_REGISTRY_REPO`) to enable the live matrix (`gcp`, `oci-registry`, `aws`, `azure`).

## SLO modes (`proxy` vs `otel`)

- **Proxy / Ingress SLO mode (`slo-ingress.promql`, default)**: reads upstream proxy/ingress counters and latency buckets (`traffic`, `error_rate`, `latency_p95`) with no app `/metrics` or `target_info` required (ADR 0098, ADR 0107).
- **OTel app SLO mode (`slo.promql`)**: queries app-emitted `http_server_request_duration_seconds_*` joined with `target_info{service_version=...}`.

## Security boundary

- No static credentials: every plugin and managed adapter refuses static credential files inside `$WORKLOAD`, refuses static environment keys, and passes Bearer tokens to `curl` only on stdin (`-H @-` or `-H @file`), never in `argv`.
- Keyless OIDC: each provider ships GitHub Actions and GitLab CI OIDC templates and least-privilege Terraform IAM/budget definitions.
