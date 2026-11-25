# Managed Google Cloud Pub/Sub EventBus Adapter (`EVENT_BUS=pubsub`)

`EventBus` port implementation targeting Google Cloud Pub/Sub REST v1 (`${PUBSUB_API_URL:-https://pubsub.googleapis.com}/v1/projects/$GCP_PROJECT/...`).

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) via `CONFORMANCE=1 EVENT_BUS=pubsub sh gates/eventbus-conformance.sh` against an ephemeral Python HTTP Pub/Sub v1 mock server; live GCP conformance requires your own project and keyless OIDC / ADC credentials.

## Configuration

- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`, `PUBSUB_PROJECT`): target GCP project identifier (must not be empty or `replace-me`).
- `PUBSUB_API_URL` (optional, default `https://pubsub.googleapis.com`): REST API base URL (overridable for local mock conformance servers).
- `PUBSUB_TOPIC` (optional, default `tower`): Pub/Sub topic name.
- `PUBSUB_SUBSCRIPTION` (optional, default `tower-inproc`): Pub/Sub pull subscription name.
- `PUBSUB_AUTO_CREATE` (optional, default `1`): idempotently creates the topic and subscription (`200`/`409`) before `publish`, `pull`, and `ready`.
- `PUBSUB_ACK_DEADLINE` (optional, defaults to `CONFORMANCE_ACK_DEADLINE` or `60` seconds): subscription acknowledgement deadline in seconds.
- `PUBSUB_ACCESS_TOKEN` / `PUBSUB_AUTH_CMD` (optional; falls back to `GCP_ACCESS_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `gcloud auth application-default print-access-token`, or GCE metadata server): Bearer token sent exclusively to `curl` via stdin (`-H @-`), never in `argv`.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds.

## Verbs

- `ready.sh`: verifies token resolution and topic/subscription readiness.
- `publish.sh '<json>'`: base64-encodes `<json>` and publishes one message to `topics/$PUBSUB_TOPIC:publish`.
- `pull.sh`: pulls at most one message (`maxMessages: 1`) and prints `<ackId> <data>` or nothing when empty.
- `ack.sh <ackId>`: acknowledges `<ackId>` on `subscriptions/$PUBSUB_SUBSCRIPTION:acknowledge`.
