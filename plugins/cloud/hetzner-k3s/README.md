# Hetzner Cloud k3s + Argo Rollouts Delivery Plugin

External Delivery plugin (`DELIVERY=outside/hetzner-k3s`) targeting a Hetzner Cloud k3s cluster running Argo Rollouts via `kubectl` and scoped ephemeral OIDC kubeconfigs.

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/hetzner-k3s ADAPTERS_PATH=...`.

## Setup

Stage Terraform and OIDC templates into `.crew/cloud/hetzner-k3s/`:

```sh
./go cloud-setup CLOUD=hetzner-k3s
```

Review `infra/main.tf`, `infra/variables.tf`, and `infra/outputs.tf`, set your allowed API CIDRs, OIDC issuer (or Vault OIDC broker), and monthly EUR budget cap, then apply with Terraform.

## Required environment variables

- `HETZNER_CLUSTER` (cluster identifier in your Hetzner Cloud project)
- `HETZNER_KUBECONFIG` (or `KUBECONFIG`, path to an ephemeral OIDC-backed kubeconfig outside `$WORKLOAD`)

Optional: `HETZNER_NAMESPACE` (default `lights-out`), `HETZNER_ROLLOUT` (default `backend`), `HETZNER_CANARY_INGRESS` (default `backend-canary`), `HETZNER_HEALTH_PORT` (default `8000`), `HETZNER_REGISTRY_HOST` (default `ghcr.io`), `READY_TIMEOUT` (default `180`), `APP_URL`, `CANARY_URL`.

## Authentication and security

- Hetzner Cloud does not provide a native cloud-API OIDC STS equivalent to AWS/GCP/Azure IAM. Keyless delivery therefore configures k3s `kube-apiserver` OIDC JWT verification (or a Vault OIDC token broker) and binds CI OIDC subjects strictly to a single namespace `Role` for Rollouts, Pods, Services, and Ingresses (no cluster-admin).
- `env.sh` refuses empty config, refuses kubeconfigs stored inside `$WORKLOAD`, and refuses static credential files (`*.pem`, `hcloud-token*`, `*kubeconfig*`, service-account JSONs, `.aws/credentials`) inside `$WORKLOAD`.
- Terraform enforces `monthly_budget_eur` validation against cost-capped server types and restricts API/SSH access via `hcloud_firewall`. Pair this with Hetzner Cloud Console project billing alerts since the Hetzner provider exposes no native billing budget API resource.

## Conformance

See [ADR 0104](../../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md) and [ADR 0058](../../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md). Run live against your cluster:

```sh
./go delivery-conformance DELIVERY=outside/hetzner-k3s ADAPTERS_PATH=/abs/path/to/plugins/cloud
```
