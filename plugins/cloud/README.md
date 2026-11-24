# Cloud Delivery Plugins (`outside/*`)

Four external Delivery plugins implementing the `lights-out` Delivery port (`ready.sh`, `deploy.sh`, `shift.sh`, `rollback.sh`, `status.sh`, `canary-url.sh`, `slo.promql`) via [ADR 0058](../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md) (`ADAPTERS_PATH` and `DELIVERY=outside/<provider>`) and [ADR 0104](../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md).

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/<provider> ADAPTERS_PATH=...`.

## Available providers

| Provider | Target platform | CLIs used | Guide |
|---|---|---|---|
| `gcp-cloud-deploy` | Google Cloud Run + Cloud Deploy | `gcloud`, `jq`, `curl` | [gcp-cloud-deploy/README.md](gcp-cloud-deploy/README.md) |
| `aws-ecs-codedeploy` | AWS ECS Fargate + CodeDeploy + ALB | `aws`, `jq`, `curl` | [aws-ecs-codedeploy/README.md](aws-ecs-codedeploy/README.md) |
| `azure-container-apps` | Azure Container Apps (multi-revision) | `az`, `jq`, `curl` | [azure-container-apps/README.md](azure-container-apps/README.md) |
| `hetzner-k3s` | Hetzner Cloud k3s + Argo Rollouts | `kubectl`, `hcloud`, `jq`, `curl` | [hetzner-k3s/README.md](hetzner-k3s/README.md) |

## Quick start

Stage the Terraform + OIDC starter files for a provider into `.crew/cloud/<provider>/` without touching your workload repository:

```sh
./go cloud-setup CLOUD=gcp-cloud-deploy
```

Then provision infrastructure with Terraform, configure OIDC federation in your CI provider (`ci/github-oidc.yml` or `ci/gitlab-oidc.yml`), and verify conformance against your real cloud account:

```sh
./go delivery-conformance DELIVERY=outside/gcp-cloud-deploy ADAPTERS_PATH=/abs/path/to/plugins/cloud
```

## Security boundary

- No static credentials: every plugin's `env.sh` refuses empty target configuration and refuses static credential environment variables or static credential files inside `$WORKLOAD`.
- Keyless OIDC: each provider ships GitHub Actions and GitLab CI OIDC templates and least-privilege Terraform IAM/budget definitions.
