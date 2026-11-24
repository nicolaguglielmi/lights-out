# AWS ECS + CodeDeploy Delivery Plugin

External Delivery plugin (`DELIVERY=outside/aws-ecs-codedeploy`) managing ECS Fargate task definitions, CodeDeploy blue/green deployments, and weighted ALB target group routing via `aws`.

> Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/aws-ecs-codedeploy ADAPTERS_PATH=...`.

## Setup

Stage Terraform and OIDC templates into `.crew/cloud/aws-ecs-codedeploy/`:

```sh
./go cloud-setup CLOUD=aws-ecs-codedeploy
```

## Required environment variables

- `AWS_REGION` (or `AWS_DEFAULT_REGION`)
- `ECS_CLUSTER`
- `ECS_SERVICE`
- `CODEDEPLOY_APP`
- `CODEDEPLOY_GROUP`

Optional: `ECS_CONTAINER_NAME` (default `backend`), `READY_TIMEOUT` (default `180`), `APP_URL`, `CANARY_URL`.

## Authentication and security

- Keyless OIDC only via IAM OIDC Identity Provider + STS `AssumeRoleWithWebIdentity` (`ci/github-oidc.yml`, `ci/gitlab-oidc.yml`).
- `env.sh` refuses static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` without an OIDC web-identity or STS session token, and refuses `.aws/credentials`, credential JSON files, and `*.pem` keys inside `$WORKLOAD`.
- Least-privilege Terraform IAM role scoped to the declared ECS cluster/service, CodeDeploy application/group, and ALB listener rule, paired with an `aws_budgets_budget` (default `50` USD, alerts at `50%` and `100%`).

## Conformance

See [ADR 0104](../../../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md) and [ADR 0058](../../../docs/adr/0058-outside-adapters-and-other-cis-as-surfaces.md). Run live against your AWS account:

```sh
./go delivery-conformance DELIVERY=outside/aws-ecs-codedeploy ADAPTERS_PATH=/abs/path/to/plugins/cloud
```
