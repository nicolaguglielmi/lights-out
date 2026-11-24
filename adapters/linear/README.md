# Linear Tracker Adapter (`TRACKER=linear`)

GraphQL client implementing the `Tracker` port (`ready.sh`, `get.sh`, `put.sh`, `sync.sh`) against Linear (ADR 0116).

> Tested locally in CI against a stateful Python GraphQL HTTP server (`ci/tests/test_tracker_adapters.py`) and verified by `CONFORMANCE=1 TRACKER=linear sh gates/tracker-conformance.sh`; live workspaces require your own `LINEAR_TOKEN` and `LINEAR_TEAM_ID`.

## Required configuration

- `LINEAR_TOKEN`: Linear personal API key (`lin_api_*`) or OAuth bearer token. Passed to `curl` exclusively via stdin (`-H @-`), never in process `argv` or URLs.
- `LINEAR_TEAM_ID`: target Linear team identifier or UUID.

## Optional configuration

- `LINEAR_API_URL`: GraphQL endpoint URL (default `api.linear.app` `/graphql` over HTTPS; must start with `http://` or `https://` without embedded userinfo).
- `READY_TIMEOUT`: readiness poll ceiling in seconds (default `60`).

## Port semantics

- `put.sh <title> <origin>`: searches team issues containing `- Origin: <origin>` (`first: 50`) with exact line matching in `jq`; returns the existing `.identifier // .id` without creating a duplicate, or runs `issueCreate` and prints the created identifier.
- `get.sh <id>`: queries `issue(id: $id)` and renders canonical Markdown (`# <id> — <title>`, `- Status:`, `- Origin:`, optional `- PR:` and `- ADR:`, and description).
- `sync.sh <id> <state> [pr_url] [adr_ref]`: maps `<state>` to the team's matching Linear workflow `stateId` when available, updates `- Status:`, `- PR:`, and `- ADR:` in the issue description via `issueUpdate`, and records a deduplicated sync comment via `commentCreate`.
