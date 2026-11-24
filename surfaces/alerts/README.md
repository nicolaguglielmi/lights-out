# Alert Webhooks and Continuous Ops Loop

This guide explains how to connect external alert sources (Prometheus Alertmanager and cloud monitoring webhooks) to `core/webhook.py`, `core/ops_loop.py`, `core/runner.py`, `./go triage`, and `./go postmortem` (ADR 0109).

## 1. Alert Webhook Receiver (`core/webhook.py`)

`core/webhook.py` accepts JSON alert webhooks over HTTP, authenticates callers via `ALERT_WEBHOOK_TOKEN` (`hmac.compare_digest` on `Authorization: Bearer <token>`), enforces a 64 KiB (`65536` bytes) payload cap, and normalizes payloads into unified `lights-out.alert/1` files under `.crew/alerts/`.

By default it binds loopback-only (`127.0.0.1:8097`) and **fails closed at startup** if bound to a non-loopback address without `ALERT_WEBHOOK_TOKEN`:

```sh
ALERT_WEBHOOK_TOKEN=replace-me python3 core/webhook.py --serve --bind 127.0.0.1 --port 8097
```

Add `--dispatch` to automatically pass normalized alerts that name a `version` into `core/triage.sh`:

```sh
ALERT_WEBHOOK_TOKEN=replace-me python3 core/webhook.py --serve --bind 127.0.0.1 --port 8097 --dispatch
```

You can also normalize a saved payload file offline without starting the HTTP server:

```sh
python3 core/webhook.py --normalize payload.json --out .crew/alerts
```

### Supported Webhook Envelope Shapes

`normalize_payload()` inspects structural JSON keys rather than hardcoding vendor code in `core/`:

1. **Prometheus Alertmanager (`alerts[]` envelope)**: parses `alerts[].labels`, `alerts[].annotations`, `alerts[].status`, `commonLabels`, and `commonAnnotations`.
2. **Cloud Monitoring (`incident{}` envelope)**: parses `incident.summary`, `incident.condition_name`, `incident.state`, `incident.severity`, `incident.started_at`, and `incident.metadata.user_labels` / `incident.resource.labels`.
3. **Flat alert object**: parses `headline` / `alert` / `title`, `severity`, `version`, `stable`, `summary`, `stage`, and `chain`.

### Prometheus Alertmanager Configuration

Point an Alertmanager receiver's `webhook_configs` at `core/webhook.py`:

```yaml
receivers:
  - name: lights-out-ops
    webhook_configs:
      - url: http://127.0.0.1:8097/webhook
        send_resolved: true
        http_config:
          authorization:
            type: Bearer
            credentials: replace-me
```

Include a `version` label (matching your canary version) on Prometheus alert rules so `--dispatch` and `./go triage` can link the alert to a release candidate.

### Cloud Monitoring Webhook Configuration

In Google Cloud Monitoring (or any cloud monitoring service that emits `{"incident": {...}}` JSON webhooks):

1. Create a Webhook notification channel pointing to your authenticated endpoint (`https://<host>/webhook` behind TLS termination or `http://127.0.0.1:8097/webhook` locally).
2. Send an `Authorization: Bearer <token>` header matching `ALERT_WEBHOOK_TOKEN`.
3. Attach `version` (and optional `stable`) labels to the alerting policy or resource metadata.

### Routing Normalized Alerts into `./go triage`

When running manually (or with `--dispatch` disabled), pass an individual alert object carrying `version` to `./go triage`:

```sh
./go triage ALERT=alert.json
```

`core/triage.sh` writes ranked hypotheses and a rollback or hold proposal under `.crew/triage/`.

## 2. Rollback Auto-Incident (`core/ops_loop.py` and `core/releases.py`)

When `AUTO_INCIDENT_ON_ROLLBACK=on` is set during a release (`./go release` or `./go e2e`), `core/releases.py` calls `core/ops_loop.py` `on_rollback()` whenever a canary verdict is `rolled-back`.

- It reuses `core/incident.py` to build a deterministic incident document at `.crew/incidents/rollback-<slug>-<stamp>.json` from the release record, real log lines (if present), and `STATE_STORE` chain (when a ticket is present), without fabricating logs or releases.
- Setting `AUTO_POSTMORTEM_ON_ROLLBACK=on` optionally runs `core/postmortem.sh` on the newly created incident document (reporting clearly on `stderr` without failing the rollback if no replay recording exists at L0).
- Manually, you can still run `./go incident` and `./go postmortem`:

```sh
./go incident NAME=rollback-incident FROM=2026-11-24T00:00:00Z TO=2026-11-24T00:05:00Z
./go postmortem INCIDENT=.crew/incidents/rollback-incident.json
```

## 3. Continuous Orchestrator Runner (`core/runner.py`)

Instead of calling `adapters/inproc/run.sh` manually for one-shot queue drains, `core/runner.py` provides a lock-protected continuous loop (`--poll-s`) and a CI-friendly single-pass mode (`--once`):

```sh
EVENT_BUS=spool STATE_STORE=sqlite python3 core/runner.py --poll-s 5 --lockfile .crew/runner.lock
```

For CI workflows (see [surfaces/github-actions/tower-orchestrator.yml](../github-actions/tower-orchestrator.yml) and [surfaces/gitlab-ci/tower-orchestrator.yml](../gitlab-ci/tower-orchestrator.yml)), use `--once`:

```sh
EVENT_BUS=spool STATE_STORE=sqlite python3 core/runner.py --once --lockfile .crew/runner.lock
```
