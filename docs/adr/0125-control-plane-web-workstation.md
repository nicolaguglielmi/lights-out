# ADR 0125 — Full Control-Plane Web Workstation

- Status: accepted
- Date: 2026-11-24
- Milestone: M85 (`console-workstation`)
- Relates to:
  - [ADR 0023](0023-console-at-m11.md) & [ADR 0047](0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) (read-only projection vs explicit POST intents)
  - [ADR 0058](0058-adapters-outside-the-repository.md) (port resolution for `outside/` adapters)
  - [ADR 0059](0059-asking-the-person-through-the-console.md) & [ADR 0090](0090-pm-intake-before-the-request.md) (press lock, Host/Origin checks, `DRAFT_MAX`, and `make` targets)
  - [ADR 0084](0084-gates-under-the-shell-boundary.md) (thin shell entrypoint boundary)
  - [ADR 0121](0121-closed-deterministic-self-healing-loop.md) (kill-switch + `DELIVERY` `rollback.sh` + append-only audit log)
  - [ADR 0122](0122-enterprise-observability-probes-and-charts-port.md) (`CHARTS` port `query_charts_series` bounds)
  - [ADR 0124](0124-modular-console-build-and-sse-streaming.md) (modular `console/src/` assembly via `console/build.py`)

## Context

Prior milestones turned the web console into a modular SSE-backed logbook viewer (`ADR 0124`) supporting `go-for-launch`, spec answers, spec checks, ticket requests, and PM intake submissions. Four operational workflows still force engineers back to CLI terminals and raw JSON files:

1. **Command navigation and operator interventions.** Moving across screens, tickets, ADRs, intakes, and actions requires scrolling or manual CLI commands (`make assign`, `make resume`, `make watch ONCE=1`, `make freeze`, emergency rollback).
2. **Release-critical emergency actions without UI friction.** Operators need authenticated, reason-audited rollback (`WORKLOAD_KILL_SWITCH` + `DELIVERY` `rollback.sh`) and release-freeze (`FLAGS` `set.sh`) endpoints that obey all snapshot/public/RBAC/Origin/Content-Type invariants and never touch adapters or `.crew/` directly from browser assets.
3. **Engineering artifact inspection.** Dependency structure (`epics`, `depends_on`), ADR cross-links and governed file scopes, unified PR patches (`tasks[].patch`) alongside gate/SAST/mutant details (`ci.why`, `rework[].why`), and canary SLO/time-series metrics (`slo`, `CHARTS`) remain plain text dumps or ungraphed tables.

## Decision

### 1. Control-Plane Operator Bridge (`core/console_ops.py` and `core/console_ops.sh`)

- `core/console_ops.sh` is an executable POSIX `sh` wrapper of at most `25` non-blank/non-comment code lines invoking `python3 -B core/console_ops.py "$@"`.
- `core/console_ops.py` stays strictly stdlib-only and exposes three subcommands:
  - `rollback --reason <text> [--by <who>]`: refuses empty reasons; if `WORKLOAD_KILL_SWITCH` is configured (valid plain flag name) and `FLAGS_ADAPTER` or `FLAGS` resolves to a flags adapter, executes `<flags_dir>/set.sh <WORKLOAD_KILL_SWITCH> on`; executes `<delivery_dir>/rollback.sh`; appends a structured audit record to `CONSOLE_OPS_LOG` (defaulting under `.crew/console-ops.jsonl`, outside `console/`); prints one `GO:` line on stdout (`NO-GO:` and non-zero exit on failure).
  - `freeze --state on|off --reason <text> [--by <who>]`: refuses invalid state or empty reason; resolves `FLAGS_ADAPTER` or `FLAGS`; executes `<flags_dir>/set.sh <flag> <on|off>` where `<flag>` comes from `RELEASE_FREEZE_FLAG` or `"release-freeze"`; appends to the audit JSONL log; prints one `GO:` line on stdout (`NO-GO:` on failure).
  - `charts [--window-s <n>] [--step-s <n>]`: if `CHARTS_ADAPTER` / `CHARTS` is unset, prints `{"ok": false, "error": "CHARTS is not configured"}` cleanly; otherwise resolves `<charts_dir>`, clamps `window_s` to `[60, 86400]` (default `900`) and `step_s` to `[1, 3600]` (default `15`), calls `core.obs.query_charts_series` with default bounds (`MAX_CHART_SERIES = 50`, `MAX_CHART_POINTS = 500`, timeout `30` seconds), and emits JSON on stdout.
- All adapter and runtime state paths remain exclusively inside `core/console_ops.py`; no file under `console/` references adapters or `.crew/` internals directly.

### 2. HTTP Console Routes (`console/serve.py`)

- `GET /v1/charts.json`:
  - Enforces `identity(self.headers)`.
  - In `snapshot` mode, returns HTTP `200` with `{"ok": false, "error": "snapshot mode: no live charts"}` without spawning any subprocess.
  - In live mode, runs `["sh", "core/console_ops.sh", "charts"]` with bounded timeout and returns its JSON payload.
- Five operator write routes: `/v1/assign`, `/v1/resume`, `/v1/watch-once`, `/v1/rollback`, `/v1/freeze`.
  - Every write route validates `identity()`, `Content-Type: application/json` (`415`), `Host` and `Origin` (`403`), request body bounded by `16384` bytes, and refuses `snapshot` or `public` auth modes with `403` before acquiring the `press` lock or spawning any subprocess.
  - Every write route acquires the `press` lock (`409` on contention).
  - `/v1/assign` verifies a valid `ticket` in `state["book"]` has state `"opened"` (`409` otherwise) and runs `["make", "--no-print-directory", "assign", f"TICKET={ticket}"]`.
  - `/v1/resume` verifies a valid `ticket` in `state["book"]` is resumable (latest history entry carries an `error` or `outcome.verdict == "stranded"`; `409` otherwise) and runs `["make", "--no-print-directory", "resume", f"TICKET={ticket}"]`.
  - `/v1/watch-once` accepts `{}` and runs `["make", "--no-print-directory", "watch", "ONCE=1"]`.
  - `/v1/rollback` checks `may_launch(who)` (`403` on denial), validates non-empty `reason` within `512` characters (`400` on invalid input), and runs `["sh", "core/console_ops.sh", "rollback", "--reason", reason, "--by", who]`.
  - `/v1/freeze` checks `may_launch(who)` (`403` on denial), validates `state in ("on", "off")` and non-empty `reason` within `512` characters (`400` on invalid input), and runs `["sh", "core/console_ops.sh", "freeze", "--state", st, "--reason", reason, "--by", who]`.
- `identity()` and `may_launch()` retain clean signatures so role-based policy checks (`M87`) plug in without rewriting route handlers.

### 3. Workstation UI (`console/src/*` and `console/index.html`)

- **`Cmd+K` / `Ctrl+K` Command Palette (`<dialog id="cmd-palette">`)**: modal `<dialog>` with `closedby="any"` (and light-dismiss fallback), IME-safe keyboard filtering and arrow selection, bounded at `40` visible matches across screens, tickets, ADRs, intakes, and operator actions.
- **Operator POST Controls**: live-mode actions (`Assign`, `Resume`, `Watch once`, `Emergency Rollback`, `Release Freeze`) wired via literal `fetch("/v1/...", {method: "POST", ...})` calls with reason confirmation dialogs for privileged actions and disabled states in snapshot/unprivileged sessions.
- **Engineering Inspection Panels**:
  - **Epics & `depends_on` DAG**: inline accessible SVG graph and tabular fallback connecting epics, tickets, and `depends_on` edges cleanly across historical logbooks (`1.2`–`1.8`).
  - **ADR Explorer & Link Inspector**: filterable ADR search, status chips, governed file index, and ticket/ADR cross-reference inspection preserving `#adr-q`, `#adr-results`, and `.adr[data-number]`.
  - **Syntax-Highlighted PR Patch & Gate/Mutant Explorer**: line-tokenized unified diff rendering (`+`, `-`, `@@`, file headers) up to `2000` lines preserving raw `textContent`, paired with structured gate/SAST/mutant findings (`ci.why`, `rework[].why`, SAST/mutant summaries).
  - **Canary SLO & Time-Series Charts**: inline SVG delta visualization alongside existing `#canary` table markers plus live `/v1/charts.json` series charts fetched only when `mode === "live"`.

## Alternatives considered

- **Running adapter scripts directly inside `console/serve.py`:** Rejected because `ci/logbook.sh` AST checks intentionally forbid adapter/`STATE_STORE`/`.crew/` coupling inside `console/`, keeping a single auditable boundary through `core/console_ops.sh` and `make`.
- **Single-purpose HTML fragments outside `console/build.py`:** Rejected because modular source under `console/src/` compiled into self-contained `console/index.html` guarantees zero runtime external dependencies and keeps strict static verification intact.
- **Client-side simulation of `charts.json` in snapshot mode:** Rejected because snapshot consoles have no live telemetry plane; failing transparently with `{"ok": false, "error": "snapshot mode: no live charts"}` and zero client fetches avoids misleading operators.

## Consequences

- Operators gain single-keystroke navigation, inspected engineering context, live canary trend visibility, and audited recovery controls directly in the workstation while snapshot/public/unprivileged boundaries remain strict and zero-subprocess.
- `ci/logbook.sh` `SCRIPTS`, `MAKES`, `GETS`, and `WRITES` expand cleanly in lockstep with `console/serve.py` and `console/index.html`.
- `identity()` and `may_launch()` stay cleanly factored for `M87` RBAC integration without duplicating route validation.

## Provenance

| Threshold / Bound | Value | Provenance |
| --- | --- | --- |
| `core/console_ops.sh` code line limit | `<= 25` | Decision: stricter than the `30`-line shell boundary enforced by `ci/shell-boundary.sh` (ADR 0052, ADR 0084). |
| JSON POST body size ceiling | `16384` bytes | Derived: matches `DRAFT_MAX = 16384` established in `console/serve.py` (ADR 0059, ADR 0090). |
| Operator `reason` length limit | `512` characters | Decision: bounds human audit rationale to a single readable log entry within append-only JSONL lines (ADR 0121). |
| `console_ops` subprocess timeout | `30` seconds | Derived: matches `DEFAULT_PROBE_TIMEOUT_S = 30.0` (half of `READY_TIMEOUT = 60` seconds) in ADR 0122. |
| `charts` default `window_s` & range | `900` (`60`..`86400` seconds) | Decision: `15`-minute default equals `60` buckets at `15`s step (`900 / 15 = 60`), clamped between 1 minute and 24 hours. |
| `charts` default `step_s` & range | `15` (`1`..`3600` seconds) | Derived: default `15`s comes from ADR 0122 (`3x` the `5`s scrape interval), bounded between 1s and 1h. |
| Charts cardinality & point caps | `50` series / `500` points | Derived: inherited verbatim from `MAX_CHART_SERIES` and `MAX_CHART_POINTS` in `core/obs.py` (ADR 0122). |
| `make` action timeout | `900` seconds | Derived: matches existing `run_make` `timeout=900` in `console/serve.py` for end-to-end ticket steps. |
| Command palette visible match cap | `40` items | Decision: bounds palette DOM nodes and keyboard traversal distance while showing top screens, actions, tickets, and ADRs. |
| Diff highlighter line cap | `2000` lines | Decision: caps per-line DOM `<span>` creation on oversized patches (`<= 2000` lines), falling back cleanly to plain `<pre>` rendering. |
