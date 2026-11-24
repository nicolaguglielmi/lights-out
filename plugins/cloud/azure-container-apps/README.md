# Azure Container Apps Delivery Plugin

External Delivery plugin (`DELIVERY=outside/azure-container-apps`) driving Azure Container Apps in Multiple Revision Mode with labeled revisions (`stable`, `canary`) and ingress traffic splitting via `az`.

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/azure-container-apps ADAPTERS_PATH=...`.

## Setup

Stage Terraform and OIDC templates into `.crew/cloud/azure-container-apps/`:

```sh
./go cloud-setup CLOUD=azure-container-apps
```

## Required environment variables

- `AZURE_SUBSCRIPTION_ID`
- `AZURE_RESOURCE_GROUP`
- `AZURE_CONTAINER_APP`

Optional: `AZURE_CONTAINER_NAME` (default `backend`), `READY_TIMEOUT` (default `180`), `APP_URL`, `CANARY_URL`.

## Authentication and security

- Keyless OIDC only via User-Assigned Managed Identity + Federated Identity Credentials (`ci/github-oidc.yml`, `ci/gitlab-oidc.yml`).
- `env.sh` refuses static `ARM_CLIENT_SECRET`/`AZURE_CLIENT_SECRET` environment variables and refuses static credential files (`*.json` service account / SP credentials, `*.pem`, `.aws/credentials`) inside `$WORKLOAD`.
- Least-privilege Terraform role assignment grants Contributor scoped strictly to the Container App resource group, paired with an `azurerm_consumption_budget_resource_group` (default `50` USD, alerts at `50%` and `100%`).

## Conformance

See [ADR 0104](../../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md) and [ADR 0058](../../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md). Run live against your Azure subscription:

```sh
./go delivery-conformance DELIVERY=outside/azure-container-apps ADAPTERS_PATH=/abs/path/to/plugins/cloud
```
