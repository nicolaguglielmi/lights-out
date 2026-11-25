# ADR 0119 — Multi-Ticket Epics, `depends_on` DAG Scheduling, and Multi-Service Monorepos

- Status: accepted (extends [ADR 0019](0019-control-plane-state-machine.md) §1/§2, [ADR 0053](0053-perimeters-never-overlap.md) §5, [ADR 0055](0055-the-spec-before-the-ticket.md) §4/§7, [ADR 0056](0056-mainline-without-a-queue.md) §3/§5, and [ADR 0110](0110-stateful-monorepo-rust-java-and-js-managers.md) §4)
- Date: 2026-11-24
- Milestone: M79

## Context

`lights-out` coordinates individual tickets through `core/tower.py` with perimeter conflict detection (`core/assign.py`, ADR 0053 §5) and queue-free mainline verification (`core/mainline.py`, ADR 0056), and drafts single-service monorepo contracts via `INIT_SERVICE` (`core/stacks.py`, ADR 0110 §4). Three capabilities were missing for multi-PR enterprise features and multi-service monorepos:

1. **No declarative ticket dependencies or grouping**: Ticket headers only parsed `- Feature:` and `- Priority:` (`core/spec.py`, ADR 0055 §7). Teams could not express ordered feature rollouts (for example: expand migration → backend service → frontend client → cleanup) with deterministic validation against malformed IDs or dependency cycles.
2. **No `epics` collection or automated downstream unblocking**: `core/tower.py` scheduled every `opened` ticket immediately on `assign`, failing with a generic step error or running out of order rather than parking blocked tickets cleanly and waking them automatically when upstream tickets land at `main-green`.
3. **No multi-service monorepo coordinator**: `core/init.py` refused when `core.stacks.discover_workspace_services` found multiple services without `INIT_SERVICE`, leaving no `workspace.json` manifest drafting per-service contracts or mapping changed shared files to affected services.

## Decision

### 1. Optional `- Depends-On:` and `- Epic:` Spec Headers (`core/spec.py`)

- Extend header parsing (`_HEADER` regex matching hyphenated keys like `Depends-On`) while preserving `spec.fields(text)` untouched (`{feature, priority}`).
- Add `dependencies(text, own_id=None) -> (depends_on, epic)`, `depends_on(text, own_id=None)`, `epic(text)`, and `ticket_id(text)`:
  - `- Depends-On: <id[, id ...]>`: optional comma-separated list of 4-digit ticket IDs (`^\d{4}$`), deduplicated in declaration order, allowed at most `1` header line, and rejected if empty, malformed, or naming the ticket's own ID when known.
  - `- Epic: <id-or-slug>`: optional identifier (`^[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*$`), allowed at most `1` header line.
- Enforce both rules inside `spec.check(text)` (`gates/spec.sh`) so invalid dependency declarations fail deterministically before any worker sees the ticket.

### 2. Epic DAG Documents & Wave Scheduler (`core/epics.py`, `core/epics.sh`)

- **Separate `epics` collection**: Epic documents are stored exclusively in `STATE_STORE` collection `"epics"` (never inside `"tickets"`, so `core/logbook.py` and `core/tower.py` ticket scans remain isolated).
- **Validated DAG & topological waves**: `create_epic(epic_id, title, tickets_dir, ticket_ids, state_store_dir=None)` reads each ticket spec (`Depends-On`, `Feature`, `Perimeter`), rejects missing tickets, mismatched `- Epic:` headers, unknown dependency IDs, and dependency cycles (naming the exact cycle path `A -> B -> A` via deterministic DFS).
- **Perimeter-disjoint sub-waves**: `compute_waves(dag)` assigns each ticket to its longest-dependency topological wave; `partition_disjoint_waves(waves, perimeters)` reuses `core.assign.overlaps` to split any wave containing overlapping globs into sequential perimeter-disjoint execution batches without crossing topological wave boundaries.
- **Automated `main-green` unblocking**: `on_main_green(completed_ticket_id, ...)` scans tickets and parent epics in `STATE_STORE`, updates `wait.blocking` on tickets waiting at `opened` (`wait.kind == "depends_on"`), and whenever all dependencies of a waiting ticket have landed on green main (`state == "main-green"`, or passed `mainline` green before `release`), clears `wait` and publishes `{"ticket": id, "step": "assign"}` on `EVENT_BUS` — strictly respecting `BLOCKED_BY_RED_MAIN` (`mainline/main` `state == "red"`) — while updating parent epic state (`open` | `in-progress` | `completed`).
- **Thin wrapper**: `core/epics.sh` (`+x`, `10` code lines, under the `25`-line cap and `30`-line shell boundary) delegates `create`, `status`, `unblock`, and `workspace` subcommands to `core/epics.py`.

### 3. Dependency Gating in the Tower (`core/tower.py`)

- Keep `PRECONDITION` and `NEXT` immutable (`ci/board.sh` and `ci/logbook.sh` assert exact equality).
- Populate optional `depends_on` and `epic` on ticket documents during `open`, `request`, `intake-confirm`, and `spec` transitions when declared in the ticket `.md`.
- Before running `assign` in `Tower.step("assign")`, inspect each dependency in `doc.get("depends_on", [])`: if any dependency has not yet landed on `main-green`, record `wait: {"kind": "depends_on", "blocking": [...]}` on the ticket document, keep `state == "opened"`, record no failed history entry, launch no worker, and return `0` idempotently.
- After `Tower.step("mainline")` persists `main-green` (and before publishing `release` so FIFO event-bus drains run downstream `assign` while upstream holds `main-green`), invoke `epics.on_main_green` safely.

### 4. Multi-Service Monorepo Coordinator (`core/epics.py`)

- `build_workspace_manifest(repo_dir, out_dir=None, stacks_dir="stacks", today=None, baseline_mode="incremental")`:
  - Discovers services via `core.stacks.discover_workspace_services(repo_dir)` (falling back to single root service when no workspace sub-services exist).
  - Invokes `core.init.write_draft(..., service=svc, baseline_mode=baseline_mode)` per service into `<out_dir>/services/<svc>/`.
  - Scans workspace root manifests/lockfiles and non-service shared code directories into `shared_paths`, and writes `<out_dir>/workspace.json` (`schema: "lights-out.workspace/1"`).
- `affected_services(manifest, changed_paths)`: maps repo-relative changed paths to matching service IDs (including shared files matching `shared_paths`, root lockfiles, and transitive service `depends_on` edges), returning a deterministic ordered list.

## Alternatives rejected

- **Adding a `"blocked"` state to `PRECONDITION` / `NEXT` in `core/tower.py`**: Rejected — `ci/board.sh` and `ci/logbook.sh` assert exact equality against embedded console/board transition tables; keeping the ticket in `opened` with `wait: {"kind": "depends_on", "blocking": [...]}` preserves 100% compatibility with existing schemas and UI columns.
- **Storing epic documents in the `"tickets"` collection**: Rejected — `core/logbook.py` and `core/tower.py` treat documents in `"tickets"` as ticket/intake records; isolating epics in `"epics"` prevents polluting ticket lists, spend ledgers, and DORA metrics.
- **Duplicating glob overlap logic inside `core/epics.py`**: Rejected — reusing `core.assign.overlaps` guarantees epic sub-waves enforce the exact same perimeter exclusion semantics as runtime `assign`.

## Consequences

- `core/` stays 100% Python stdlib-only, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), and model-free (`ci/model-free.sh`).
- Existing `core/init.py`, `board/`, and `console/` remain untouched here; The integration commit wires `--workspace` into `core/init.py`, `Makefile`, `./go`, and `core/cli.py`.
- Monorepo PRs and multi-ticket epics gain deterministic wave planning, automatic `main-green` downstream dispatch, and shared-path impact analysis.

## Provenance

| Numeric threshold | Where it comes from |
|---|---|
| `4`-digit ticket IDs (`^\d{4}$`) in `- Depends-On:` | External to this ADR: [ADR 0044](0044-incident-in-postmortem-out.md) §6, [ADR 0059](0059-new-requests-from-the-console-and-the-adrs-behind-it.md) §4, and `core/spec.py` `_TICKET_FILE`. |
| `1` maximum `- Depends-On:` header line and `1` maximum `- Epic:` header line | External to this ADR: [ADR 0055](0055-the-spec-before-the-ticket.md) §7 (`core/spec.py` rejects duplicate optional header lines). |
| `1` minimum ticket per epic in `create_epic` | Decision: an empty epic has no DAG nodes or deliverable work to schedule. |
| `30` code-line limit (`ci/shell-boundary.sh`) and `<= 25` code-line ceiling (`10` actual code lines) on `core/epics.sh` | External to this ADR / decision: [ADR 0052](0052-where-shell-is-allowed.md) §4 and [ADR 0084](0084-gates-under-the-shell-boundary.md) §5 set `limit=30`; keeping `core/epics.sh` thin (`<= 25`) leaves margin under the ceiling. |
| Schema identifier `lights-out.workspace/1` (`1`) | Decision: initial version `1` of the workspace manifest schema, matching `lights-out.cli/1` ([ADR 0112](0112-unified-cli-grammar-json-and-completions.md)). |

## Golden-rule check

`git clone && ./go demo` is unaffected: tickets without `- Depends-On:` or `- Epic:` follow the exact existing `core/spec.py` and `core/tower.py` paths (`ci/tower.sh` and `ci/board.sh` stay `GO`), and all unit and end-to-end tests execute offline against local directories and loopback/file-backed adapters.
