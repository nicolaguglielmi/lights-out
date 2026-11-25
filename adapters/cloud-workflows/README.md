# Google Cloud Workflows Orchestrator Adapter (`ORCHESTRATOR=cloud-workflows`)

`Orchestrator` port implementation (`profiles/l3.env`) dispatching tower step executions to Google Cloud Workflows Executions REST v1 (`https://workflowexecutions.googleapis.com`).

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) against an ephemeral Python HTTP Cloud Workflows Executions v1 mock server (`ACTIVE` -> `SUCCEEDED`, failure states, and bounded timeout); live L3 orchestration requires your own GCP project, deployed `workflow.yaml`, and keyless OIDC / ADC credentials.

## Configuration

- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`): target GCP project identifier (must not be empty or `replace-me`).
- `GCP_WORKFLOW_LOCATION` (optional, falls back to `GCP_REGION`, `GOOGLE_CLOUD_LOCATION`, or `us-central1`): Cloud Workflows region.
- `CLOUD_WORKFLOW_NAME` (optional, default `lights-out-tower`): deployed workflow name (`workflow.yaml`).
- `WORKFLOWS_API_URL` (optional, default `https://workflowexecutions.googleapis.com`): Executions API base URL.
- `WORKFLOWS_ADMIN_API_URL` (optional, defaults to `$WORKFLOWS_API_URL` when overridden or `https://workflows.googleapis.com`): Workflows admin API base URL for `ready.sh`.
- `WORKFLOW_POLL_INTERVAL_S` (optional, default `2`): execution status poll interval in seconds.
- `WORKFLOW_POLL_TIMEOUT_S` (optional, default `300`): maximum execution wait ceiling in seconds.
- `WORKFLOWS_ACCESS_TOKEN` / `WORKFLOWS_AUTH_CMD` (optional; falls back to `GCP_ACCESS_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `gcloud`, or GCE metadata server): Bearer token passed exclusively to `curl` via stdin (`-H @-`), never in `argv`.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds.

## Verbs

- `ready.sh`: checks workflow existence and `ACTIVE` state via Workflows REST v1.
- `run.sh [<ticket> <step>]`: with no arguments, drains `EVENT_BUS` (`adapters/$EVENT_BUS/{pull,ack}.sh`) dispatching each `{ticket, step}` message to Cloud Workflows and polling until completion; with `<ticket> <step>`, dispatches a single workflow execution.
