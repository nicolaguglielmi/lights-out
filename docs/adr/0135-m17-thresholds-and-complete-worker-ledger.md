# ADR 0135 — Threshold Drift & Override Governance, Complete Sub-Worker and Release-Brief Spend Ledger, TUI View 6, and Console `#thresholds`

- Status: accepted
- Date: 2026-11-25
- Milestone: M17 Final Slice / M93
- Amends: ADR 0037 §5 (visibility into effective policy overrides and provenance drift), ADR 0083 §3 (release brief runs enter the spend ledger), ADR 0087 §1 (partial and budget-stopped network sub-worker runs enter the ledger)

## Context

Three governance and accounting gaps remained open across the platform:

1. Platform defaults across `policy/merge.env`, `policy/rework.env`, `policy/mainline.env`, and `roles/*/role.env` are overridden across workload contracts, `.crew/start-plan.env`, and runtime environment variables, yet operators had no single command or view comparing platform defaults against active values, showing provenance classifications (`measurement`, `external`, `decision`), and surfacing recent commit history on those policy files.
2. In `adapters/network/run.sh`, if any sub-worker stopped on `TOKEN_BUDGET` or failed after executing at least one member, the script refused before aggregating `$OUT/run.json`, dropping sibling and partial member token spend from `core/spend.py` (noted in ADR 0087 §1 and sprint follow-ups). Likewise, `core/promote_cmd.py` ran the `release` role to draft promotion briefs without recording sanitized `brief_run` telemetry into `.crew/releases.jsonl`, leaving `release` role spend uncounted.
3. Both the terminal cockpit (`./go top`) and the web console needed unified visibility into threshold drift, per-ticket cross-provider review `jury` decisions, and signed provenance receipt completeness.

## Decision

### 1. Threshold Governance CLI and Module (`core/thresholds.py`, `core/thresholds.sh`)

- `core/thresholds.py` reads platform defaults (`policy/merge.env`, `policy/rework.env`, `policy/mainline.env`, `roles/*/role.env`), extracts adjacent `# Provenance, (measurement|external|decision): ...` comments (including contiguous shared blocks and colon/header fallbacks), merges workload overrides (`workload.env`, `policy.env`), `.crew/start-plan.env`, and active environment overrides, and optionally attaches up to 5 recent git commits per policy file (`git log -n 5`, 100% deterministic for a fixed commit, gracefully omitted outside git checkouts).
- `core/thresholds.sh` stays at 25 code lines or fewer for `ci/shell-boundary.sh` compliance: emitting `GO: thresholds — <n> keys (<k> overridden)` on stdout with tabular details on stderr by default, and structured JSON on stdout when passed `--json` or `JSON=1`.

### 2. Complete Sub-Worker and Release-Brief Spend Ledger

- `adapters/network/run.sh`: once member runs start, any member failure, `TOKEN_BUDGET` stop, check failure, or merge conflict triggers `write_run_json` before exiting nonzero. Failed or stopped members carry `"failed": true` (and optional `stop`) inside `$OUT/run.json`'s `members` list while preserving all recorded token counts from completed or budget-stopped siblings and propagating any `$m/out/stop` marker to `$OUT/stop`. `core/spend.py` normalizes both flattened and raw member objects and ignores failed uninitialized empty placeholders (`failed=True`/`harness="unknown"` with no tokens/model/provider) so sibling member costs are never discarded.
- `core/promote_cmd.py`: sanitizes non-secret run telemetry (`harness`, `provider`, `model`, `thinking`, `turns`, `tokens`, `duration_s`, `input_sha256`, `replayed`, `failed`, `stop`, `at`) from `promote_dir/out/run.json` into `brief_run` (saving `brief_run.json` and updating the matching `kind == "release"` or fallback `kind == "answer"` entry in `.crew/releases.jsonl` under `ReleaseTrainLock`). No 7th role or ticket state transition is added.
- `core/spend.py` and `core/logbook.py`: extract `release_ledger(...)` entries from `.crew/releases.jsonl` and fold them into `report`, `burn`, `lines`, `roles`, and matching `tickets` totals.

### 3. Logbook Additive Enrichment, TUI View 6, and Console `#thresholds`

- `core/logbook.py`: preserves `SCHEMA = "lights-out/logbook/1.8"`, preserves byte-for-byte golden stub output (`fixtures/logbook/golden-1.8.json`), and on active workspaces enriches `thresholds` entries with `default`, `active`, `changed`, `source`, `scope`, `role`, `provenance_kind`, `last_commit`, and `history` while adding optional per-ticket `provenance_summary` (from `.crew/provenance/<subject>.intoto.json` and `.bundle`) and `jury` metadata with zero wall-clock reads.
- `core/tui.py`: adds view 6 (`thresholds`, `Thresholds & Provenance`), keeping tab bar labels within 80 columns and footer/body clipping compliant at `80x24` and `NO_COLOR=1`.
- `console/src/`: upgrades `#thresholds` with override/drift KPIs, interactive filters, search, `Guided` (`junior`) and `Cockpit` (`senior`) views (including policy commit history), and displays cross-provider `jury` badges plus provenance receipt summaries on ticket detail views (`#ticket/<id>` and card drawer).

## Alternatives

1. Adding a separate ticket state or 7th role entry for `release` brief runs — rejected because ADR 0079 §2 prohibits adding ticket write paths when `.crew/releases.jsonl` already records the release train atomically.
2. Emitting a brand-new `1.9` logbook schema — rejected so existing `1.8` logbook consumers, console snapshots, and golden fixtures remain valid with additive optional fields only.

## Consequences

- Budget-stopped and partially failed network executions no longer lose sub-worker token spend.
- Release brief costs from non-replay harnesses appear in `./go spend` and the logbook under role `release` and the corresponding ticket when linked.
- Existing `1.8` logbook readers and legacy `golden-1.8.json` fixtures remain backwards-compatible in both the console and TUI.

## Provenance

- 25 (code-line ceiling for `core/thresholds.sh`): decision — `ci/shell-boundary.sh` / ADR 0052 establishes the 30-line ceiling for `core/*.sh` wrappers, tightened to 25 lines here by milestone constraint.
- 5 (maximum git commits retrieved per policy file via `git log -n 5`): decision — owner's slice bound keeping per-file history concise in terminal and table views without scanning unbounded repository history.
- 6 (TUI view index for `Thresholds & Provenance`): decision — extends the existing 5-view layout of `core/tui.py` cleanly as view `6` within single-digit keyboard navigation (`1-6`).
- 80x24 (minimum terminal dimensions enforced in `core/tui.py` view 6): external — ECMA-48 / DEC VT100 standard terminal dimensions preserved from `core/tui.py`.
- 14 (maximum threshold rows displayed inline before truncation summary in compact TUI view): decision — fits comfortably inside a 24-row viewport alongside header (3 rows), section headers, jury/provenance summary, status, and footer without overflow.
