# ADR 0109 — Continuous orchestration, rollback auto-incident, and authenticated alert webhook

- Status: accepted
- Date: 2026-11-24
- Milestone: P3 (Continuous Ops Loop)

## Context

`lights-out` already provides the core building blocks for governed delivery and incident response:

- `core/releases.py` records canary outcomes (`held`, `rolled-back`, `in-progress`) to `RELEASE_RECORD` (default `.crew/release.json`) and `RELEASES_LOG` (default `.crew/releases.jsonl`).
- `core/incident.py` (`./go incident`) deterministically builds `.crew/incidents/<name>.json` documents (`kind: "incident"`) from a completed release record, an explicit UTC window, optional log lines, and an optional `STATE_STORE` chain (`ticket`, `pr`, `merge`).
- `core/triage.sh` / `core/triage.py` (`./go triage`) runs the `ops-triage` role against an alert JSON carrying at least `version`, producing ranked hypotheses and a `rollback` or `hold` proposal.
- `core/postmortem.sh` / `core/postmortem.py` (`./go postmortem`) runs `ops-triage` against an incident document, validates anchors against the document, and files action-item tickets via `TRACKER`.
- `adapters/inproc/run.sh` drives one-shot queue draining over `adapters/$EVENT_BUS/{pull,ack}.sh` and `core/tower.sh step <ticket> <step>`.

Three operational gaps prevent closing the continuous ops loop without manual invocation:

1. **Rollback-to-incident gap**: When a canary rolls back via `core/releases.py record("rolled-back", ...)`, no incident document is automatically captured unless a person runs `./go incident` manually.
2. **External alert ingestion gap**: Monitoring systems emit heterogeneous JSON webhook envelopes (array-of-alerts envelopes with `labels`/`annotations`, nested `incident` envelopes with `summary`/`condition_name`/`state`, or flat alert objects) that cannot be handed directly to a unified schema or authenticated HTTP receiver.
3. **Continuous orchestration gap**: `adapters/inproc/run.sh` drains once and exits without lockfile protection, polling cadence, signal handling, or structured status logging, and repositories lack event-driven CI orchestrator recipes (`surfaces/github-actions/`, `surfaces/gitlab-ci/`).

## Decision

### 1. Deterministic Rollback Auto-Incident (`core/ops_loop.py` and `core/releases.py`)

- `core/ops_loop.py` (standard library only, strictly vendor-free per `ci/vendor-free.sh`, app-free per `ci/app-free.sh`, and model-free per `ci/model-free.sh`) provides `on_rollback(record_path, env=None)`:
  - Reuses `core/incident.py` (`release_record`, `incident`, and `chain` when a ticket and `STATE_STORE` are present) to validate that `record_path` holds a `rolled-back` release record and never fabricates log lines, release metadata, or audit chains.
  - Derives a deterministic slug (`rollback-<slug>-<stamp>`) matching `^[a-z0-9][a-z0-9-]*$` from `record["version"]` and the incident window end timestamp (`FROM`/`TO` from `env`, else earliest/latest `started_at`/`judged_at` stamps in `record["stages"]`, else `INCIDENT_AT` or current UTC second).
  - Writes `.crew/incidents/rollback-<slug>-<stamp>.json` (or under `INCIDENT_OUT` when set) in `core/incident.py`'s canonical JSON format.
  - Only when `AUTO_POSTMORTEM_ON_ROLLBACK=on`, invokes `core/postmortem.sh` (`INCIDENT=<path>`) as a subprocess. When a replay recording is absent (or `AGENT_HARNESS`/`TRACKER` are unconfigured), `on_rollback` reports the reason clearly on `stderr` and returns a non-failing skipped status rather than failing the caller or breaking the rollback path.
- `core/releases.py` gains a minimal opt-in hook inside `record()` that imports `ops_loop` lazily and calls `ops_loop.on_rollback(path, env)` **only** when `verdict == "rolled-back"` and `env.get("AUTO_INCIDENT_ON_ROLLBACK") == "on"` (default `off`, leaving every existing test, `./go demo`, `./go demo-rollback`, and `./go e2e` untouched). `core/launchpad.sh` is untouched so the `ci/shell-boundary.sh` ceiling stays unchanged.

### 2. Alert Normalization & Authenticated Webhook Receiver (`core/webhook.py`)

- `core/webhook.py` (standard library only, strictly vendor-free, app-free, and model-free) normalizes structural alert JSON payloads without coupling `core/` to any monitoring vendor name:
  - `normalize_payload(data, received_at=None)` accepts three structural shapes:
    1. **Alerts-array envelope**: `{"alerts": [{"labels": ..., "annotations": ..., "status": ...}, ...]}` (carrying optional `commonLabels`/`commonAnnotations` fallback).
    2. **Incident envelope**: `{"incident": {"summary": ..., "condition_name": ..., "state": ..., ...}}`.
    3. **Flat alert object**: `{"headline" | "alert" | "title" | "summary" | "version", ...}` (preserving `stage` and `chain` when present for `core/triage.py`).
  - Emits unified `lights-out.alert/1` JSON:
    `{"schema": "lights-out.alert/1", "received_at": "<utc>", "alerts": [{"headline", "severity", "status", "version", "stable", "summary", "labels", "starts_at", ...}]}`.
- CLI modes:
  - `--normalize <file> --out <dir>`: normalizes a payload file and writes the `lights-out.alert/1` JSON document under `<dir>`.
  - `--serve --bind 127.0.0.1 --port 8097 [--out .crew/alerts] [--dispatch]`: starts an HTTP `ThreadingHTTPServer` listening on `bind:port` (default `127.0.0.1:8097`):
    - **Fail-closed non-loopback check**: refuses to start (exit `1`) if `--bind` is not a loopback address (`127.0.0.1`, `::1`, `localhost`) and `ALERT_WEBHOOK_TOKEN` is unset or empty.
    - **Bearer authentication**: whenever `ALERT_WEBHOOK_TOKEN` is set, every `POST` request must carry `Authorization: Bearer <token>` verified in constant time with `hmac.compare_digest`; missing or mismatched credentials receive HTTP `401`.
    - **Payload size cap**: rejects bodies over `PAYLOAD_MAX_BYTES = 65536` (64 KiB) with HTTP `413` before reading unbounded input; invalid JSON or unrecognized envelopes receive HTTP `400`; non-JSON `Content-Type` receives HTTP `415`.
    - **Persistence & optional dispatch**: writes normalized JSON files under `ALERT_WEBHOOK_OUT` (default `.crew/alerts/`), responds HTTP `200`, and only when `--dispatch` is passed dispatches alerts carrying a `version` into `core/triage.sh` (logging clearly without crashing the server if replay recording is absent).

### 3. Continuous Orchestrator Runner (`core/runner.py`)

- `core/runner.py` (standard library only, strictly vendor-free, app-free, and model-free) drains and polls `adapters/$EVENT_BUS/` using the exact `pull.sh` -> `core/tower.sh step <ticket> <step>` -> `ack.sh` protocol of `adapters/inproc/run.sh`:
  - `--once`: drains all currently queued messages until `pull.sh` returns empty, then exits (`0` if all applied, `1` if any step/bus error occurred).
  - `--poll-s <seconds>` (default `5.0`): continuously drains available messages and waits on a `threading.Event` between cycles.
  - `--lockfile <path>` (default `.crew/runner.lock`): acquires a non-blocking exclusive `fcntl.flock` lock on startup and fails cleanly if another runner already holds it.
  - Registers `SIGTERM` and `SIGINT` handlers so an in-flight `step` + `ack` completes before exiting cleanly rather than leaving an unacknowledged message mid-step.
  - Emits structured JSON status lines to `stderr` (`ts`, `event`, `bus`, `ticket`, `step`, `rc`, `processed`, `failed`, `pid`).

### 4. CI Orchestrator & Alert Recipes (`surfaces/`)

- `surfaces/github-actions/tower-orchestrator.yml`: event-driven GitHub Actions recipe triggered on `issues`, `issue_comment`, and `pull_request`, guarded by `if: github.server_url == 'https://github.com'`, least-privilege `permissions`, SHA-pinned actions (`actions/checkout`, `actions/upload-artifact`), safe env-var binding of event payload fields (no inline expression interpolation in `run:` scripts), and clean under `actionlint`.
- `surfaces/gitlab-ci/tower-orchestrator.yml`: GitLab CI pipeline recipe using `resource_group` concurrency protection and `core/runner.py --once --lockfile .crew/runner.lock`.
- `surfaces/alerts/README.md`: operator guide documenting how to route Prometheus Alertmanager, cloud monitoring alert webhooks, and `core/webhook.py` into `./go triage` and `./go postmortem`, honoring `ci/docs-drift.sh`.

## Alternatives rejected

- **Calling `on_rollback` directly from `core/launchpad.sh`**: `core/launchpad.sh` is already at the `ci/shell-boundary.sh` 70-code-line debt ceiling; putting rollback hook dispatch inside `core/releases.py` keeps all JSON/incident logic in Python and respects `ci/shell-boundary.sh`.
- **Vendor-specific parsers inside `core/webhook.py`**: violates `ci/vendor-free.sh` and couples `core/` to monitoring vendors. Switching on structural JSON keys (`alerts[]`, `incident{}`, flat object) handles every common alert envelope with zero vendor coupling in `core/`.
- **Deleting lockfiles on exit or using PID files alone in `core/runner.py`**: PID files suffer stale-PID races and unlink races; holding an open file descriptor with `fcntl.flock(LOCK_EX | LOCK_NB)` is kernel-enforced and race-free across crashes.
- **Placing `tower-orchestrator.yml` under `.github/workflows/`**: `.github/workflows/` triggers `ci/plan.sh` workflow-gating checks meant for repository CI; `surfaces/` is the repository's home for reusable CI surface recipes (ADR 0058, ADR 0095).

## Consequences

- Default `./go demo`, `./go demo-rollback`, `./go e2e`, and existing `core/releases.py` callers remain 100% unchanged because `AUTO_INCIDENT_ON_ROLLBACK` defaults to `off` and `AUTO_POSTMORTEM_ON_ROLLBACK` defaults to `off`.
- Teams adopting continuous ops can opt in with `AUTO_INCIDENT_ON_ROLLBACK=on`, run `python3 core/webhook.py --serve`, and run `python3 core/runner.py --poll-s 5`.
- Because `core/webhook.py` binds to `127.0.0.1` by default and fails closed when bound non-loopback without `ALERT_WEBHOOK_TOKEN`, accidental unauthenticated exposure is prevented at startup.

## Golden-rule check

`git clone && ./go demo` and `./go ci` are unchanged and require no API keys, accounts, or running cloud services. All new modules under `core/` use the Python standard library only, run cold inside workbench, and pass `ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`, and `sast.sh core`.

## Provenance

| Item | Value | Provenance |
|---|---|---|
| `PAYLOAD_MAX_BYTES` | `65536` (64 KiB) | Decision: matches `core/tower.py` `PATCH_CAP = 65536` and 4x `console/serve.py` `DRAFT_MAX = 16384`; bounds unparsed webhook payloads in memory |
| Default webhook address | `127.0.0.1:8097` | Decision: loopback-only default adjacent to `console/serve.py` (`8098`, ADR 0047) and `board` (`8099`, ADR 0035) |
| Default `runner.py` poll interval | `5.0` seconds | Decision: aligns with L0 scrape/response cadence without tight-looping `pull.sh` |
| `actions/checkout` SHA pin | `3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`) | Matches `.github/workflows/ci.yml` and `.github/workflows/corpus.yml` |
| `actions/upload-artifact` SHA pin | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` (`v7.0.2`) | Matches `.github/workflows/corpus.yml` and `action.yml` |

## Tests

Written from this ADR before implementation:

- `core/tests/test_ops_loop.py` (strictly vendor-free, app-free, model-free):
  - Deterministic `on_rollback` incident creation reusing `core/incident.py`, ticket `chain` lookup, non-rollback refusal, and `AUTO_INCIDENT_ON_ROLLBACK=on|off` hook behavior in `core/releases.py`.
  - `AUTO_POSTMORTEM_ON_ROLLBACK=on` invocation vs graceful reporting when replay recording or harness/tracker is absent.
  - `core/webhook.py` normalization across all three structural envelopes, error rejection, `--normalize`, fail-closed non-loopback startup without token, bearer auth (`hmac.compare_digest`) rejections and 200 acceptance, and 64 KiB payload size cap (`413`).
  - `core/runner.py` `--once` drain against stub `EVENT_BUS`, invalid message drop+ack, failing step ack+exit 1, `--lockfile` contention refusal, and graceful `SIGTERM` exit.
- `ci/tests/test_ops_loop_e2e.py`:
  - Live `ThreadingHTTPServer` HTTP E2E tests (fail-closed non-loopback startup, 401 unauthorized, 413 oversized payload, 200 valid bearer POST writing `.crew/alerts/`).
  - Subprocess E2E rollback -> auto-incident test via `core/releases.py` in a temporary workspace.
  - Subprocess E2E `core/runner.py --once` against a stub `EVENT_BUS` + `STATE_STORE` and `SIGTERM` shutdown in `--poll-s` mode.
  - Recipe structure checks for `surfaces/gitlab-ci/tower-orchestrator.yml`, `surfaces/alerts/README.md`, and `actionlint` validation of `surfaces/github-actions/tower-orchestrator.yml`.
