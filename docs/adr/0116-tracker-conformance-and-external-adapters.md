# ADR 0116 — `Tracker` Port Conformance Suite, `sync.sh` Verb, and External Adapters (`github-issues` & `linear`)

- Status: accepted (amends [ADR 0017](0017-agent-runtime-at-m5.md) §4, [ADR 0044](0044-incident-in-postmortem-out.md) §6, and [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §1/§5)
- Date: 2026-11-24
- Milestone: M76

## Context

`lights-out` defines the `Tracker` port (`PLAN §4`) around `adapters/$TRACKER/{get,put}.sh` (ADR 0017 §4, ADR 0044 §6), but until now only the local `file` adapter existed. Three gaps blocked enterprise adoption and third-party tracker plugins:

1. **No `Tracker` conformance suite or `outside/*` admission**: `core/adapters.py` still listed `TRACKER` in `NO_SUITE`, so external tracker adapters (`TRACKER=outside/<name>`) could not prove conformance or be admitted via `core/adapters.py`, and `ci/outside.sh` treated `TRACKER=outside/*` as a port with no suite.
2. **No bidirectional status/PR/ADR synchronization verb**: `adapters/file/` implemented `get.sh` and `put.sh`, which refused to modify a ticket once filed, leaving no port verb to reflect ticket state transitions, PR links, or ADR references back into the tracker.
3. **No remote issue-tracker adapters**: Teams tracking work in GitHub Issues or Linear lacked `TRACKER=github-issues` and `TRACKER=linear` adapters matching the existing `curl` stdin-header secret discipline (`-H @-`, ADR 0043 / ADR 0044 §8) and deterministic `- Origin: <origin>` idempotency.

## Decision

### 1. `Tracker` Conformance Suite (`gates/tracker-conformance.sh` → `core/gates/tracker.py`)

- **`gates/tracker-conformance.sh`** (executable, under the 30 code-line shell boundary of ADR 0052 §4 / ADR 0084 §5): returns cold `NO-GO: tracker-conformance — set CONFORMANCE=1: ...` unless `CONFORMANCE=1`, then delegates to `verdict core.gates.tracker`.
- **`core/gates/tracker.py`** (Python stdlib only, strictly vendor-free, app-free, and model-free per `ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`): resolves `TRACKER` either under `adapters/<name>` (for in-repo adapters and `core/adapters.py` trial/admitted symlinks) or directly under `$ADAPTERS_PATH/<name>` when `TRACKER` is `outside/<name>` (validating `<name>` against `[a-z0-9][a-z0-9-]*`). Requires `put.sh` and `get.sh`, runs optional `ready.sh` as a preflight check (requiring exit 0 and empty stdout), and runs six deterministic conformance cases in order:
  1. `case1 put-create`: `put.sh <title> <origin>` with a Markdown body on stdin exits 0 and prints a single non-empty, whitespace-free ticket ID on stdout.
  2. `case2 get-roundtrip`: `get.sh <id>` exits 0 and prints Markdown containing the title, an exact `- Origin: <origin>` line, and the body text.
  3. `case3 put-idempotent`: calling `put.sh <title> <origin>` a second time with the same `<origin>` exits 0 and returns the exact same ticket ID.
  4. `case4 sync-metadata`: if `sync.sh` exists, calling `sync.sh <id> <state> [pr_url] [adr_ref]` updates `- Status:`, `- PR:`, and `- ADR:` metadata idempotently (verified across repeated calls with identical and updated states without duplicating metadata lines) and `get.sh <id>` reflects the updated metadata; if `sync.sh` is absent, this step reports skipped while still running cases 5 and 6.
  5. `case5 invalid-put-refused`: `put.sh "" <origin>` and `put.sh <title> ""` both exit non-zero with empty stdout.
  6. `case6 missing-get-refused`: `get.sh` on a non-existent ticket ID (and on `""`) exits non-zero with empty stdout.

### 2. File Tracker `sync.sh` (`adapters/file/sync.sh`)

- Add executable `adapters/file/sync.sh <id> <state> [pr_url] [adr_ref]` without altering `adapters/file/get.sh` or `adapters/file/put.sh`.
- Resolves `TICKETS_DIR` identically to `get.sh` and `put.sh` (default `tickets` relative to repo root).
- Fails closed (exit 1, one line on stderr, empty stdout) when `<id>` or `<state>` is empty, `<id>` contains path traversal, or `TICKETS_DIR/<id>.md` does not exist.
- Atomically rewrites `TICKETS_DIR/<id>.md` via a temporary file in the same directory and `mv`, updating `- Status: <state>` in place and upserting `- PR: <pr_url>` and `- ADR: <adr_ref>` (when non-empty) inside the top metadata header block without duplicating lines across repeated runs.

### 3. External Tracker Adapters (`adapters/github-issues/` & `adapters/linear/`)

- **`adapters/github-issues/`** (`env.sh`, `ready.sh`, `get.sh`, `put.sh`, `sync.sh`, `README.md`; all `.sh` POSIX `sh` and executable):
  - Configuration: `GITHUB_TOKEN` (required, non-empty, not `replace-me`), `GITHUB_REPO` (`owner/repo`), `GITHUB_API_URL` (optional, default `https://api.github.com`, must be `http://` or `https://` with no embedded userinfo), `READY_TIMEOUT` (default `60`).
  - Secret handling: passes `Authorization: Bearer <GITHUB_TOKEN>` exclusively to `curl` via `-H @-` on stdin (payload JSON written to a `0600` temp file and passed via `--data-binary @<file>`), never placing the token in `curl` `argv` or URLs.
  - `ready.sh`: polls `GET /repos/<owner>/<repo>` until HTTP 200 (or fails immediately on 401/403/404 or after `READY_TIMEOUT`).
  - `put.sh <title> <origin>`: paginates `GET /repos/<owner>/<repo>/issues?state=all&per_page=100` (matching an exact `- Origin: <origin>` line in the issue body) for idempotency; if absent, creates an issue via `POST /repos/<owner>/<repo>/issues` with `- Status: open` and `- Origin: <origin>` metadata and prints the issue number.
  - `get.sh <id>`: fetches `GET /repos/<owner>/<repo>/issues/<id>` (and falls back to comments only if metadata is absent from the body), rendering canonical Markdown (`# <id> — <title>`, `- Status:`, `- Origin:`, optional `- PR:` / `- ADR:`, and body).
  - `sync.sh <id> <state> [pr_url] [adr_ref]`: maps terminal states (`closed`, `done`, `resolved`, `merged`) to GitHub state `closed` (others to `open`), maintains a single `status:<state>` label alongside existing non-status labels, atomically updates metadata lines in the issue body via `PATCH`, and posts a deduplicated sync comment (`<!-- lights-out-sync: ... -->`) only if not already present.

- **`adapters/linear/`** (`env.sh`, `ready.sh`, `get.sh`, `put.sh`, `sync.sh`, `README.md`; all `.sh` POSIX `sh` and executable):
  - Configuration: `LINEAR_TOKEN` (required, non-empty, not `replace-me`), `LINEAR_TEAM_ID` (required, non-empty), `LINEAR_API_URL` (optional, default `https://api.linear.app/graphql`, `http://` or `https://`, no embedded userinfo), `READY_TIMEOUT` (default `60`).
  - Secret handling: passes `Authorization` header exclusively via `curl -H @-` stdin (`Authorization: <token>` for `lin_api_*` personal keys; `Authorization: Bearer <token>` otherwise) and sends GraphQL JSON via `--data-binary @<file>`. Checks both HTTP status and GraphQL `.errors`.
  - `ready.sh`: queries `viewer { id }` and `team(id: $teamId) { id name }` up to `READY_TIMEOUT`.
  - `put.sh <title> <origin>`: queries team issues filtering by description substring and verifies exact `- Origin: <origin>` line match in `jq`; if absent, runs `issueCreate` mutation with `- Status: open` and `- Origin: <origin>` metadata and prints `.identifier // .id`.
  - `get.sh <id>`: queries `issue(id: $id)` and renders canonical Markdown (`# <identifier> — <title>`, `- Status:`, `- Origin:`, optional `- PR:` / `- ADR:`, and body).
  - `sync.sh <id> <state> [pr_url] [adr_ref]`: queries the issue and team workflow `states`, maps `<state>` to a matching `stateId` when present, updates `- Status:`, `- PR:`, and `- ADR:` in description via `issueUpdate`, and appends a deduplicated `commentCreate` sync note when not already recorded.

### 4. `outside/file-tracker` Fixture, `core/adapters.py`, and `ci/outside.sh`

- **`fixtures/outside-adapters/file-tracker/`** (`get.sh`, `put.sh`, `sync.sh`, all `+x`): self-contained external `TRACKER` adapter storing tickets in `FILE_TRACKER_DIR` (default `.crew/file-tracker`) without any relative `../..` reference into the `lights-out` checkout.
- **`core/adapters.py`**: adds `"TRACKER": "tracker-conformance"` to `SUITES` and shrinks `NO_SUITE` to `("ORCHESTRATOR", "METRICS")`.
- **`ci/outside.sh`**: moves the no-suite negative assertion from `TRACKER=outside/json-files` to `ORCHESTRATOR=outside/json-files`, and adds an end-to-end `TRACKER=outside/file-tracker` section verifying refusal before proof, `trial`, tamper refusal, broken-copy NO-GO, `tracker-conformance` GO + registration, admission symlink, 1-byte tamper refusal, and symlink-in-tree refusal.

## Alternatives rejected

- **Passing `GITHUB_TOKEN` / `LINEAR_TOKEN` in `curl -H "Authorization: ..."`**: Rejected — exposes credentials in process `argv` (`ps`); passing the header on stdin via `-H @-` and the JSON payload via `--data-binary @<file>` keeps credentials out of `argv` and logs.
- **Amending `adapters/file/put.sh` to mutate existing tickets**: Rejected — ADR 0044 §6 specifies `put.sh` never rewrites an existing ticket; keeping `put.sh` immutable and adding an explicit optional `sync.sh` verb preserves backward compatibility while supporting bidirectional state/PR/ADR sync.
- **Requiring every `Tracker` adapter to implement `sync.sh`**: Rejected — existing minimal external trackers that only implement `get.sh` and `put.sh` can still pass conformance; when `sync.sh` exists, case 4 exercises it strictly.
- **Listing vendor names inside `core/gates/tracker.py` for secret scanning**: Rejected — `ci/vendor-free.sh` forbids vendor names anywhere in `core/`; secret-in-argv and HTTP header enforcement belong in adapter code and `ci/tests/test_tracker_adapters.py`.

## Consequences

- `TRACKER` joins `CODE_HOST`, `DELIVERY`, `STATE_STORE`, `EVENT_BUS`, `REGISTRY`, and `FLAGS` as a conformance-gated port supporting `outside/*` plugins. Only `ORCHESTRATOR` and `METRICS` remain in `NO_SUITE` (pending the integration commit of `Charts` and `Makefile` wiring).
- **Honest live vs mock boundary**: `ci/tests/test_tracker_adapters.py` validates `github-issues` and `linear` end-to-end against local `ThreadingHTTPServer` mock REST and GraphQL servers inside Podman with real `curl` and `jq`, proving stdin-only auth and all 6 conformance cases. Running against live GitHub or Linear workspaces requires operator credentials and `CONFORMANCE=1`.

## Provenance

| Numeric threshold | Where it comes from |
|---|---|
| `30` code-line limit on `gates/tracker-conformance.sh` (`7` actual code lines) | External to this ADR: [ADR 0052](0052-where-shell-is-allowed.md) §4 and [ADR 0084](0084-gates-under-the-shell-boundary.md) §5 (`ci/shell-boundary.sh`, `limit=30`). |
| `6` conformance cases in `core/gates/tracker.py` | Decision: one case per required/optional `Tracker` port invariant (create, read roundtrip, put idempotency, sync idempotency, empty-arg refusal, absent-ID refusal). |
| `4`-digit zero-padded IDs (`%04d`, `0001`+) in `file-tracker` | External to this ADR: [ADR 0044](0044-incident-in-postmortem-out.md) §6 and `adapters/file/put.sh`. |
| `60` s default `READY_TIMEOUT` and `5` s poll interval in `ready.sh` | External to this ADR: [ADR 0043](0043-github-and-replay.md) §5 (`adapters/github/ready.sh`). |
| `5` s `curl --connect-timeout` and `30` s `curl --max-time` | External to this ADR: [ADR 0043](0043-github-and-replay.md) (`adapters/github/env.sh`). |
| `100` items per page (`per_page=100`, max `50` pages) in `github-issues`; `50` nodes (`first: 50`) in `linear` | External / decision: REST v3 `per_page` maximum is `100` (50-page ceiling bounds runaway pagination to 5,000 issues); `first: 50` bounds single-roundtrip GraphQL complexity. |
| `12` hex chars of suite SHA-256 in `core/adapters.py` register | External to this ADR: [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §3 (`suite_id()`). |

## Golden-rule check

`git clone && ./go demo` and local `file` tracking remain untouched: `adapters/file/get.sh` and `adapters/file/put.sh` are unmodified, `gates/tracker-conformance.sh` refuses cold in one line unless `CONFORMANCE` enables the suite (provenance: ADR 0026 §4), and all unit/integration tests run offline against local temp files and loopback mock servers.
