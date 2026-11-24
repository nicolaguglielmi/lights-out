# ADR 0103 — Dual-mode Junior + Expert Guided End-to-End Experience (`./go start`, `./go next`, and Console lifecycle guide)

- Status: proposed
- Date: 2026-11-24
- Milestone: Track 4 — Dual-mode Guided End-to-End Experience
- Amends: ADR 0059 (adds `section#journey` after existing `#overview` panels while preserving `#your-turn` first); ADR 0097 (connects the seven-level path to live local repository state via `./go start` and `./go next`)

## Context

`lights-out` ships a full governed autonomous delivery lifecycle — contract initialization (`./go init`, `./go attach`, `./go try`), provider/harness profiles and spend caps (`profiles/`, `policy/`), intake interviews and spec gates (`./go intake`, `./go request`, `./go answer`), eight PR gates with bounded rework (`./go gates`, `./go assign`, `./go resume`), canary rollout with SLO verification and human approval (`./go release`, `./go verify`, `./go promote`), and observability/evidence/postmortem (`./go logbook`, `./go dora`, `./go spend`, `./go evidence`, `./go postmortem`).

However, two gaps remain for both newcomers and experienced operators:

1. **No stateful "where am I, what is next?" guide.** Docs list commands statically, so a user connecting a repository or pausing mid-workflow cannot ask the platform what stage they are on and what single command advances them next.
2. **Two audiences need two registers without splitting the platform.** A **junior** developer needs clear explanations of why mutation testing, canary traffic shifts, SLO verification, and automatic rollback protect them, plus safe defaults and an interactive or preset setup wizard. An **expert** operator wants terse commands, structured `--json` output, and direct file pointers (`policy.env`, `roles/*.md`, `mcp.json`, `adapters/`, `plugins/cloud/`) to customize thresholds, reviewer rules, and MCP tools.

At the same time, `core/` must remain strictly vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), model-free (`ci/model-free.sh`), and under the 30-line shell boundary (`ci/shell-boundary.sh`), and no secret credential may ever be persisted outside the user's gitignored `.env`.

## Decision

### 1. Six-stage end-to-end lifecycle model

Every user-facing navigation surface (`./go next`, `./go start`, and the Console `#overview` lifecycle guide) shares one canonical 6-stage lifecycle:

| Stage | ID | Title | Gate / completion signal |
|---|---|---|---|
| 1 | `connect` | Connect & init contract | `WORKLOAD_CONTRACT` has `workload.env` with non-empty `MUTATION_BASELINE` |
| 2 | `provider` | Provider, harness & budget | Non-secret setup plan (`.crew/start-plan.env`) or active tower state exists, adapters exist in `adapters/` or `plugins/cloud/`, and any provider credential key named in `profiles/providers.json` is non-empty (and non-placeholder) in `.env` or environment |
| 3 | `intake` | Intake & spec gate | At least one intake is confirmed or at least one ticket has moved past `requested` / `questions` to `spec-ready` or later |
| 4 | `build` | Agentic build, 8 gates & rework | At least one ticket has passed PR gates/review (`ci-passed`, `merged`, `main-green`, `held`, `rolled-back`) or a release exists |
| 5 | `release` | Canary deploy, SLO verify & human gate | At least one release has reached a decided verdict (`rolled-back` or `held` answered `GO`/`NO-GO`) |
| 6 | `observe` | Observe, DORA, spend, evidence & postmortem | Logbook (`.crew/logbook.json`), DORA report (`.crew/dora.json`), and release evidence pack (`.crew/evidence/<version>.json`) — plus postmortem (`.crew/postmortem/`) when rolled back — exist |

### 2. Provider metadata in `profiles/providers.json` (`core/` stays model-free)

To satisfy `ci/model-free.sh`, `core/start.py` contains zero model IDs and zero provider key variable names (such as `*_API_KEY`). Instead, `profiles/providers.json` maps provider identifiers (`replay`, `gemini`, `anthropic`, `openai`, `zai`) to:

- `label`: display name
- `harnesses`: supported harness directory names under `adapters/`
- `default_harness`: primary harness
- `env_var`: environment variable name checked in `.env` (empty for `replay`)
- `default_model`: default model identifier
- `default_profile`: `l0` for `replay`, `l1` for live providers
- `decide`: Decide port adapter (`replay`, `gemini-decisions`, or `structured`)
- `budget_guidance`: concise cost/quota guidance citing `profiles/prices.json` and `policy/rework.env`

`ci/profiles.sh` inspects only `profiles/*.env`, so `profiles/providers.json` coexists without schema drift.

### 3. `./go next` (`core/start.sh` + `core/start.py`, `<= 30` code lines)

`core/start.py next` inspects:
- `WORKLOAD_CONTRACT` (from environment or `--contract`, never hardcoding repo-local workload or contract paths in `core/`), `workload.env`, `compose.yaml`, `policy.env`, `mcp.json`, and `roles/*.md`
- `.env` (checking **only** boolean presence / non-placeholder status of keys; never storing, returning, or printing secret values)
- `.crew/start-plan.env`, `.crew/state.sqlite` (read-only), `.crew/release.json`, `.crew/releases.jsonl`, `.crew/logbook.json`, `.crew/dora.json`, `.crew/evidence/`, `.crew/postmortem/`, and `TICKETS_DIR`
- Adapters in `adapters/` and cloud delivery plugins in `plugins/cloud/` (discovered dynamically when present)

Output contract:
- **Stdout (default):** exactly one line: `GO: next — stage <N>/6 (<title>): <cmd>` (exit `0`), or `NO-GO: next — <reason>` (exit `1`) on invalid arguments, corrupt state files, or unknown `MODE`/adapter configuration.
- **Stderr (default):** full 6-stage checklist (`[done]` / `[now]` / `[todo]`):
  - `MODE=junior` (default): explains gates, mutation testing, bounded rework, canary shifts, SLO thresholds, automatic rollback, and human approval in plain language.
  - `MODE=expert`: emits terse commands plus direct override pointers (`<contract>/policy.env`, `<contract>/roles/*.md`, `<contract>/mcp.json`, `policy/rework.env`, `profiles/<level>.env`, `adapters/`, `plugins/cloud/`).
- **`--json`:** prints structured JSON on stdout (no secret values, boolean `key_set` only).

### 4. `./go start` (interactive TTY wizard + deterministic `--non-interactive`)

`core/start.py start` supports:
- Interactive TTY wizard when stdin is a terminal and `--non-interactive` is omitted.
- Deterministic non-interactive mode:
  `--non-interactive --preset <junior|strict|custom> --provider <p> --harness <h> --delivery <d>`
  (plus optional `--profile`, `--ticket-budget-usd`, `--day-budget-usd`, `--mode`).
- **Zero secret persistence on disk:** prompts/wizards NEVER ask for or accept secret tokens to store in `.crew/start-plan.env`. Only non-secret configuration (`START_PRESET`, `START_MODE`, `START_PROFILE`, `MODEL_PROVIDER`, `AGENT_HARNESS`, `DELIVERY_ADAPTER`, `DELIVERY_SOURCE`, `PROVIDER_KEY_VAR`, `PROVIDER_KEY_CONFIGURED=yes|no`, `DOCTOR_STATUS`, `WORKLOAD_CONTRACT`) is written to `.crew/start-plan.env`.
- **Doctor validation:** reads pre-computed host doctor results from `./go` (`LIGHTS_OUT_DOCTOR_*`) or invokes `sh core/doctor.sh` with the chosen profile, surfaces diagnostic items on stderr, records `DOCTOR_STATUS`, and outputs exact copy-paste E2E workflow commands (including instructions to export or add `<ENV_VAR>` to gitignored `.env` if unset).

### 5. `Makefile` and `./go` wiring

- `Makefile` adds `start:` and `next:` `.PHONY` targets with `##` help comments (visible to `ci/help.sh` and `ci/docs-drift.sh`) and adds `start next` alongside `init try` in the optional `-include $(WORKLOAD_CONTRACT)/workload.env` guard so `./go next` and `./go start` work before a contract exists.
- `./go` adds help lines for `./go start` and `./go next`, runs host-side `core/doctor.sh` before entering `workbench` for `start`, and forwards CLI flags cleanly without requiring a container engine socket inside `workbench`.

### 6. Console `section#journey` in `console/index.html`

`screenOverview()` appends `section#journey` **after all existing overview panels** so `#your-turn` remains the first `.panel` in `main` (and `section#dora`, `section#spend`, `section#roles` retain their exact relative ordering in `ci/console-ui.py`). `section#journey` computes the 6-stage lifecycle bar purely from existing `lights-out/logbook/1.8` fields (`source`, `roles`, `intakes`, `tickets`, `releases`, `launch`, `dora`, `spend`, `postmortems`, `thresholds`, `agents`), provides a collapsible `<details>` Junior Guide, and includes quick links to `#specs`, `#kanban`, `#network`, `#launch`, `#thresholds`, and `#agents` (with `#agents` routing cleanly to `screenThresholds()` while keeping `h2:text-is('Agents') + div.panel pre` intact). All colors reuse the console's existing WCAG 2.1 AA CSS tokens, text is identical across light/dark/system themes and disabled `localStorage`, and layout avoids horizontal scroll at `1440x900` and `390x844`.

## Provenance

- **6 stages, `junior`/`expert` modes, zero secret persistence, Console `#journey` placement:** architectural decisions anchored to existing platform stages (`core/init.py`, `core/intake.py`, `core/tower.py`, `core/releases.py`, `core/logbook.py`, `core/dora.py`, `core/spend.py`, `core/evidence.py`, `core/postmortem.py`) and DOM contract invariants (`ci/console-ui.py`, `ci/logbook.sh`).
- **`core/start.sh <= 30` code lines:** `ci/shell-boundary.sh` limit (`limit=30`, ADR 0052 §4).
- **Breakpoints (`1440x900` desktop, `720px` CSS media query, `390x844` mobile):** existing `console/index.html` and `ci/console-ui.py` viewport measurements (ADR 0063 §1).

## Alternatives rejected

- **Hardcoding provider names/env keys in `core/start.py`.** Violates `ci/model-free.sh` and duplicates provider metadata already evolving in `profiles/` and `adapters/`.
- **Writing `.env` automatically from `start`.** Risks overwriting user credentials or echoing secrets to disk/stdout; keeping `.crew/start-plan.env` strictly non-secret preserves zero secret persistence.
- **Creating a new logbook schema `1.9` for Console journey state.** Unnecessary and risks snapshot/golden fixture churn (`golden-*.json` must stay untouched); deriving the 6-stage bar from logbook `1.8` works across all existing logbooks and snapshots.
- **Placing `section#journey` above `#your-turn` in `#overview`.** Breaks ADR 0063 §4 and `ci/console-ui.py` (`i_overview`: `#your-turn` must be the first `.panel` in `main`).

## Consequences

- Newcomers get one guided entry point (`./go start`, `./go next`, and Console `#journey`) without learning 40+ Makefile targets upfront.
- Experts keep full deterministic CLI automation (`--non-interactive`, `--json`, `MODE=expert`) and explicit customization pointers.
- `./go demo` and all existing L0/L1/L2 execution paths remain unchanged.

## Golden-rule check

`./go demo` continues to run unmodified and key-free at L0. `./go start --non-interactive --preset junior` defaults to L0 replay with zero secrets required.

## Tests

- `core/tests/test_start.py`: strictly vendor-free, app-free, and model-free unit tests using synthetic temp workspaces, synthetic providers metadata, and synthetic state fixtures covering all 6 stage detections, `next` verdict lines and exit codes, `--json`, `junior` vs `expert` rendering, `--non-interactive` presets (`junior`, `strict`, `custom`), zero secret persistence on disk/output, and `doctor` validation.
- `ci/tests/test_guided_ux.py`: integration tests against the repository's real `profiles/providers.json`, `Makefile` (`make help` / `make next` / `make start`), `./go` help lines, and `console/index.html` DOM/CSS/security invariants (`section#journey`, `#your-turn` first, quick links including `#agents`, `cmd_names` / `cmd_index` hygiene, responsive CSS rules).
