# GitHub Issues Tracker Adapter (`TRACKER=github-issues`)

REST client implementing the `Tracker` port (`ready.sh`, `get.sh`, `put.sh`, `sync.sh`) against GitHub Issues (ADR 0116).

> Tested locally in CI against a stateful Python HTTP REST server (`ci/tests/test_tracker_adapters.py`) and verified by `CONFORMANCE=1 TRACKER=github-issues sh gates/tracker-conformance.sh`; live repositories require your own `GITHUB_TOKEN` and `GITHUB_REPO`.

## Required configuration

- `GITHUB_TOKEN`: personal access token or installation token with issue read/write permission. Passed to `curl` exclusively via stdin (`-H @-`), never in process `argv` or URLs.
- `GITHUB_REPO`: repository `<owner>/<name>`.

## Optional configuration

- `GITHUB_API_URL`: REST API base URL (default `https://api.github.com`; must start with `http://` or `https://` without embedded userinfo).
- `READY_TIMEOUT`: readiness poll ceiling in seconds (default `60`).

## Port semantics

- `put.sh <title> <origin>`: paginates repository issues (`per_page=100`, up to `50` pages) looking for an exact `- Origin: <origin>` line; returns the existing issue number without creating a duplicate, or creates a new issue and prints its number.
- `get.sh <id>`: fetches the issue and renders canonical Markdown (`# <id> — <title>`, `- Status:`, `- Origin:`, optional `- PR:` and `- ADR:`, and body).
- `sync.sh <id> <state> [pr_url] [adr_ref]`: maps terminal states (`closed`, `done`, `resolved`, `merged`) to issue state `closed` (`open` otherwise), maintains a single `status:<state>` label, updates metadata lines in the issue body, and records a deduplicated sync comment idempotently.
