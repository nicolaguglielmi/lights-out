# ADR 0128 — Managed L2/L3 Cloud Adapters and Nightly Live Cloud Conformance

- Status: accepted (reconciles M8 and M10; builds on [ADR 0019](0019-event-bus-and-tower.md), [ADR 0025](0025-artifact-signing-and-promotion.md), [ADR 0044](0044-incident-in-postmortem-out.md), [ADR 0057](0057-the-stepped-way-in.md), [ADR 0060](0060-state-and-bus-conformance.md), [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md), and [ADR 0107](0107-cloud-registry-flags-and-proxy-slos.md))
- Date: 2026-11-24
- Milestone: M88 (`swarm/m88-managed-cloud-adapters`)

## Context

`profiles/l2.env` and `profiles/l3.env` declare six managed GCP adapters that had no in-repository implementations under `adapters/`:

1. `EVENT_BUS=pubsub` (`adapters/pubsub/`) for Google Cloud Pub/Sub REST v1.
2. `STATE_STORE=firestore` (`adapters/firestore/`) for Cloud Firestore REST v1.
3. `REGISTRY=artifact-registry` (`adapters/artifact-registry/`) for Google Artifact Registry (`*-docker.pkg.dev`).
4. `METRICS=managed-prometheus` (`adapters/managed-prometheus/`) for Google Cloud Managed Service for Prometheus, alongside `core/gates/readiness.py` which previously accepted only `METRICS=promql`.
5. `DELIVERY=cloud-deploy` (`adapters/cloud-deploy/`) for native Cloud Deploy and Cloud Run traffic management, reusing `plugins/cloud/gcp-cloud-deploy/`.
6. `ORCHESTRATOR=cloud-workflows` (`adapters/cloud-workflows/`) for L3 Google Cloud Workflows step execution dispatch.

At the same time, `.github/workflows/` lacked a keyless OIDC nightly cloud conformance workflow exercising the L2/L3 adapters, `oci-registry`, `aws`, and `azure` plugins when enabled without breaking forks or unconfigured pull-request runners.

## Decision

### 1. Managed L2/L3 Adapter Suite (`adapters/`)

Implement all six adapters as POSIX `#!/bin/sh` (`set -eu`) scripts passing `shellcheck -s sh` (`ci/shell.sh`), free of static credential files in `$WORKLOAD`, and strict on Bearer token hygiene:

- **HTTP timeouts & stdin auth hygiene**: Every REST `curl` call in `pubsub`, `firestore`, `managed-prometheus`, and `cloud-workflows` uses `--connect-timeout 5` and `--max-time 30` (matching the 30s lock/request bound in `adapters/spool/spool.py` while accommodating WAN TLS + REST latency over the local emulator 2s/10s settings). Bearer headers are passed to `curl` exclusively on stdin (`-H @-`) and request payloads via `--data-binary "@$tmpfile"`, never in `argv` where `ps` could observe tokens or hit `ARG_MAX` on 65,536-byte StateStore payloads (ADR 0060 §4).
- **Token resolution order**: Each managed GCP REST adapter resolves tokens from explicit adapter/GCP environment tokens, an explicit `*_AUTH_CMD` / `GCP_AUTH_CMD`, `gcloud` (`application-default print-access-token` / `print-access-token`), and finally the GCE metadata server (`http://metadata.google.internal/...` or `GCP_METADATA_URL`). When an explicit local HTTP override URL (`PUBSUB_API_URL`, `FIRESTORE_API_URL`, `METRICS_URL`, `WORKFLOWS_API_URL`) is pointed at an unauthenticated local test server with no token configured, auth headers are omitted rather than failing on `metadata.google.internal`; HTTPS `googleapis.com` targets fail closed when no token is resolved.
- **`adapters/pubsub/` (`lib.sh`, `publish.sh`, `pull.sh`, `ack.sh`, `ready.sh`)**: Targets `${PUBSUB_API_URL:-https://pubsub.googleapis.com}/v1/projects/$GCP_PROJECT`. Auto-provisions `PUBSUB_TOPIC` (`tower`) and `PUBSUB_SUBSCRIPTION` (`tower-inproc`) idempotently (`200`/`409`) when `${PUBSUB_AUTO_CREATE:-1} = 1`, defaulting `PUBSUB_ACK_DEADLINE` to `${CONFORMANCE_ACK_DEADLINE:-60}` seconds (60s default matching `adapters/spool` / Pub/Sub subscription defaults) and passing all cases of `gates/eventbus-conformance.sh`.
- **`adapters/firestore/` (`lib.sh`, `get.sh`, `put.sh`, `delete.sh`, `ready.sh`)**: Targets `${FIRESTORE_API_URL:-https://firestore.googleapis.com}/v1/projects/$GCP_PROJECT/databases/${FIRESTORE_DATABASE:-(default)}/documents`. Reuses `adapters/firestore-emulator/` typed JSON encoding (`stringValue`, `integerValue`, `doubleValue`, `booleanValue`, `nullValue`, `arrayValue`, `mapValue`), UTF-8 safe, replacing documents via `PATCH` without an update mask, treating `404` on `get.sh` as exit `1` (silent) vs exit `2` on store failures, and passing all 8 cases of `gates/statestore-conformance.sh`.
- **`adapters/artifact-registry/` (`env.sh`, `publish.sh`, `verify.sh`, `sbom.sh`, `ready.sh`)**: Resolves OIDC / ADC Bearer tokens (`ARTIFACT_REGISTRY_TOKEN`, `ARTIFACT_REGISTRY_AUTH_CMD`, `gcloud auth print-access-token`, or GCE metadata) into `OCI_REGISTRY_USER=oauth2accesstoken` and `OCI_REGISTRY_TOKEN`, optionally deriving `OCI_REGISTRY_REPO` from `GCP_PROJECT` / `GCP_REGION` / `ARTIFACT_REGISTRY_REPOSITORY`, and delegates to `adapters/oci-registry/{publish,verify,sbom,ready}.sh`, passing all 6 cases of `gates/registry-conformance.sh`.
- **`adapters/managed-prometheus/` (`lib.sh`, `query.sh`, `ready.sh`) and `core/gates/readiness.py`**: Queries `${METRICS_URL:-https://monitoring.googleapis.com/v1/projects/$GCP_PROJECT/location/${GCP_PROMETHEUS_LOCATION:-global}/prometheus}/api/v1/query` with stdin `-H @-` auth, matching `adapters/promql/query.sh`'s raw `/api/v1/query` JSON contract (`verify/slo.sh` and `core/gates/readiness.py`). `core/gates/readiness.py` recognizes `METRICS=managed-prometheus` alongside `promql` while preserving `ci/vendor-free.sh` purity.
- **`adapters/cloud-deploy/` (`env.sh`, `deploy.sh`, `shift.sh`, `rollback.sh`, `status.sh`, `canary-url.sh`, `ready.sh`, `slo.promql`)**: Provides native `DELIVERY=cloud-deploy` delegating cleanly to `plugins/cloud/gcp-cloud-deploy/` while keeping `plugins/cloud/gcp-cloud-deploy/` symlink-free for `outside/*` hashing (`core/adapters.py`). `ci/package.sh`'s `--level l2|l3` check is updated since `adapters/cloud-deploy/` now exists on disk (while `workbench/cloud/Containerfile` and `engine` remain checked).
- **`adapters/cloud-workflows/` (`run.sh`, `ready.sh`, `workflow.yaml`)**: Implements `ORCHESTRATOR=cloud-workflows`, draining `EVENT_BUS` (or dispatching a single `<ticket> <step>`) via Google Cloud Workflows Executions REST v1 with bounded polling (`WORKFLOW_POLL_INTERVAL_S=2`, `WORKFLOW_POLL_TIMEOUT_S=300`).

### 2. Keyless OIDC Nightly Cloud Conformance (`.github/workflows/cloud-conformance.yml`)

Add `.github/workflows/cloud-conformance.yml` triggered on nightly `schedule` and `workflow_dispatch` with workflow-level `permissions: contents: read` and `id-token: write` scoped strictly to the `conformance` job (and update `ci/package.sh` to permit `id-token: write` for that job alongside `package.yml`). When `vars.CLOUD_CONFORMANCE_ENABLED != 'true'`, a preflight gate job writes a clear skip notice to `$GITHUB_STEP_SUMMARY` and exits `0` cleanly (fork-safe). When enabled, a four-target matrix (`gcp` L2/L3, `oci-registry`, `aws`, `azure`) authenticates via keyless OIDC and runs the conformance suites.

## Alternatives rejected

- **Duplicating `oci-registry` or `gcp-cloud-deploy` logic in `artifact-registry` and `cloud-deploy`**: Rejected — wrapping/delegating keeps one canonical `cosign` + SBOM digest-first path (`adapters/oci-registry/`) and one canonical Cloud Deploy + Cloud Run rollout implementation (`plugins/cloud/gcp-cloud-deploy/`) without copy-paste drift.
- **Passing Bearer tokens via `curl -H "Authorization: Bearer ..."` or `-d "$body"`**: Rejected — command-line arguments are readable via `ps` / `/proc/*/cmdline`, and large 65,536-byte Firestore documents risk hitting `ARG_MAX` limits; stdin `-H @-` paired with `--data-binary "@$file"` avoids both.
- **Replacing `plugins/cloud/` with `adapters/cloud-deploy/` only**: Rejected — external `outside/*` plugins (`aws-ecs-codedeploy`, `azure-container-apps`, `hetzner-k3s`, `gcp-cloud-deploy`) remain installable via `./go cloud-setup` and `ADAPTERS_PATH` (ADR 0104).

## Consequences

- `profiles/l2.env` and `profiles/l3.env` now resolve every adapter directory under `adapters/`.
- `core/gates/readiness.py` supports both `METRICS=promql` and `METRICS=managed-prometheus` without introducing any vendor names under `core/`.
- Local CI verifies real `gates/eventbus-conformance.sh`, `gates/statestore-conformance.sh`, `gates/registry-conformance.sh`, `managed-prometheus`, `cloud-deploy`, and `cloud-workflows` against HTTP and CLI mocks in Podman while asserting zero `argv` token leakage.

## Provenance

| Numeric threshold | Where it comes from |
|---|---|
| `5` s connect timeout and `30` s max HTTP timeout (`--connect-timeout 5 --max-time 30`) | Decision: matches `LOCK_TIMEOUT=30` in `adapters/spool/spool.py` ([ADR 0060](0060-state-and-bus-conformance.md)) and accommodates WAN TLS + REST round-trips over local emulator `2` s / `10` s bounds (`adapters/pubsub-emulator/lib.sh`, `adapters/firestore-emulator/lib.sh`). |
| `60` s default Pub/Sub ack deadline (`PUBSUB_ACK_DEADLINE=60`, API bounds `10`–`600` s) | External / decision: Google Cloud Pub/Sub subscription default is `60` s (`10` s minimum, `600` s maximum matching `adapters/pubsub-emulator/lib.sh` and `adapters/spool/spool.py`); conformance runs override via `CONFORMANCE_ACK_DEADLINE`. |
| `2` s Cloud Workflows poll interval (`WORKFLOW_POLL_INTERVAL_S=2`) and `300` s ceiling (`WORKFLOW_POLL_TIMEOUT_S=300`) | Decision: caps polling traffic at `<= 0.5` req/s while bounding single-step orchestration waiting below CI job ceilings. |
| `60` s default `READY_TIMEOUT` (`pubsub`, `firestore`, `artifact-registry`, `managed-prometheus`, `cloud-workflows`) and `180` s (`cloud-deploy`) | External to this ADR: matches `adapters/oci-registry/env.sh` (`60` s), `adapters/cloud-monitoring/env.sh` (`60` s), and `plugins/cloud/gcp-cloud-deploy/env.sh` (`180` s, [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md)). |
| `65536` bytes (`64` KiB) StateStore payload, `8` StateStore cases, `6` EventBus cases, `6` Registry cases, `5`/`25`/`100`% canary weights | External to this ADR: contract invariants of [ADR 0060](0060-state-and-bus-conformance.md) §4, [ADR 0107](0107-cloud-registry-flags-and-proxy-slos.md), and [ADR 0013](0013-canary-controller-and-verdict.md) / [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md). |
| `41 3 * * *` nightly cron schedule in `.github/workflows/cloud-conformance.yml` | Decision: daily off-hour UTC run staggered from `.github/workflows/nightly-github.yml` (`23 3 * * *`). |
