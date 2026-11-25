# infra

Infrastructure guidance and managed L2/L3 GCP configuration ([ADR 0104](../docs/adr/0104-cloud-delivery-plugins-and-outside-conformance.md), [ADR 0128](../docs/adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)).

## Provisioning L2/L3 infrastructure

Everything cloud-managed is optional: `PROFILE=l0` and `PROFILE=l1` run completely locally and need no GCP account (ADR 0022). `PROFILE=l2` and `PROFILE=l3` switch the plane adapters to managed GCP services:

- **Terraform & OIDC staging**: run `./go cloud-setup CLOUD=gcp-cloud-deploy` to stage [plugins/cloud/gcp-cloud-deploy/infra/main.tf](../plugins/cloud/gcp-cloud-deploy/infra/main.tf), `variables.tf`, `outputs.tf`, and keyless OIDC templates into `.crew/cloud/gcp-cloud-deploy/` without modifying your workload repository.
- **L2 managed adapters** ([profiles/l2.env](../profiles/l2.env)): `EVENT_BUS=pubsub`, `STATE_STORE=firestore`, `REGISTRY=artifact-registry`, `METRICS=managed-prometheus`, and `DELIVERY=cloud-deploy`.
- **L3 managed orchestration** ([profiles/l3.env](../profiles/l3.env)): adds `ORCHESTRATOR=cloud-workflows` ([adapters/cloud-workflows/workflow.yaml](../adapters/cloud-workflows/workflow.yaml)).
- **Nightly keyless OIDC conformance**: configure `CLOUD_CONFORMANCE_ENABLED=true` and your WIF/OIDC repository variables to run [.github/workflows/cloud-conformance.yml](../.github/workflows/cloud-conformance.yml) automatically.

## Before you `terraform apply`

**Cloud SQL is the one fixed cost.** Every other L2 service (Cloud Run, Cloud Deploy, Pub/Sub, Firestore, Artifact Registry, Managed Prometheus, Cloud Workflows) bills on use and idles at zero. Cloud SQL bills per instance-hour while the instance is running, whether or not anything connects. Between test runs, stop or delete it:

```sh
./go down PROFILE=l2                       # stops the instance
gcloud sql instances patch <name> --activation-policy NEVER   # stop by hand
gcloud sql instances delete <name>         # or delete it; terraform apply recreates it
```

Locally the database is a Postgres container and this problem does not exist.
