# Managed Cloud Firestore StateStore Adapter (`STATE_STORE=firestore`)

`StateStore` port implementation targeting Cloud Firestore REST v1 (`${FIRESTORE_API_URL:-https://firestore.googleapis.com}/v1/projects/$GCP_PROJECT/databases/${FIRESTORE_DATABASE:-(default)}/documents`).

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) via `CONFORMANCE=1 STATE_STORE=firestore sh gates/statestore-conformance.sh` against an ephemeral Python HTTP Firestore v1 mock server; live GCP conformance requires your own project and keyless OIDC / ADC credentials.

## Configuration

- `GCP_PROJECT` (or `GOOGLE_CLOUD_PROJECT`, `FIRESTORE_PROJECT`): target GCP project identifier (must not be empty or `replace-me`).
- `FIRESTORE_API_URL` (optional, default `https://firestore.googleapis.com`): REST API base URL (overridable for local mock conformance servers).
- `FIRESTORE_DATABASE` (optional, default `(default)`): Firestore database ID.
- `FIRESTORE_ACCESS_TOKEN` / `FIRESTORE_AUTH_CMD` (optional; falls back to `GCP_ACCESS_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `gcloud auth application-default print-access-token`, or GCE metadata server): Bearer token sent exclusively to `curl` via stdin (`-H @-`), never in `argv`.
- `READY_TIMEOUT` (optional, default `60`): readiness poll ceiling in seconds.

## Verbs

- `ready.sh`: verifies token resolution and Firestore REST reachability (`200` or `404` on probe document).
- `get.sh <collection> <id>`: decodes typed Firestore fields (`stringValue`, `integerValue`, `doubleValue`, `booleanValue`, `nullValue`, `arrayValue`, `mapValue`) to plain JSON on stdout (`exit 1` silently on 404, `exit 2` on error).
- `put.sh <collection> <id>`: reads a single JSON object from stdin, encodes typed Firestore fields, and replaces the document via `PATCH` (`--data-binary` file, UTF-8 and 64 KiB safe).
- `delete.sh <collection> <id>`: removes the document idempotently (`200` or `404`).
