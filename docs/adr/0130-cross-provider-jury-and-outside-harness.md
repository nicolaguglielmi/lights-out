# ADR 0130 — Cross-Provider Jury, Outside `AgentHarness`, and Orchestrator Gate Recipes

- Status: accepted (amends [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §1, §5 [`AGENT_HARNESS=outside/*` is now exercised cold through `fixtures/outside-adapters/file-harness/` and `ci/outside.sh`]; builds on [ADR 0021](0021-review-and-architect-roles.md), [ADR 0041](0041-harnesses-as-plugins.md), [ADR 0103](0103-guided-end-to-end-experience.md), [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md), and [ADR 0117](0117-signed-agent-provenance-attestations.md))
- Date: 2026-11-25
- Milestone: M37

## Context

Three gaps remained in reviewer independence, outside-adapter parity, and external orchestrator integration:

1. **Same-vendor review bias on medium- and high-risk PRs (`M37` / K5).** `core/steps/review.sh` defaults `test-reviewer` and `architect` to the profile's single `AGENT_HARNESS` and `MODEL_PROVIDER`. When a workstation or CI runner already configures keys for two or more model vendors in `.env` (for example `GEMINI_API_KEY` and `ANTHROPIC_API_KEY`), using the same provider that authored the PR weakens independent critique. Conversely, at L0 (`replay`), on `low`-risk changes, or when fewer than `2` vendor keys are configured, reviewer selection must degrade gracefully without breaking offline deterministic execution.
2. **Incomplete `outside/*` port coverage for `AGENT_HARNESS`.** `core/adapters.py` already maps `AGENT_HARNESS` to `harness-conformance`, while `ci/outside.sh` tested `7` verb-based ports (`STATE_STORE`, `DELIVERY`, `CODE_HOST`, `REGISTRY`, `FLAGS`, `TRACKER`, `EVENT_BUS`) and still asserted a stub refusal for `AGENT_HARNESS=outside/json-files` with no `fixtures/outside-adapters/file-harness/` fixture.
3. **No documented PR verification recipes for external coding orchestrators.** Teams running OpenHands, Paperclip, or Gas Town need copy-ready recipes showing how an external orchestrator remains a caller/surface while invoking `lights-out gates` (full container gates) or `lights-out-gates --light` (zero-container light tier) as the deterministic verification gate.

## Decision

### 1. Cross-Provider Jury Selection (`core/jury.py` and `core/jury.sh`)

Add a pure-stdlib, vendor-free, app-free, and model-free module `core/jury.py` with a thin POSIX `sh` wrapper `core/jury.sh` (`<= 20` non-blank, non-comment lines to preserve margin under `ci/shell-boundary.sh`'s `30`-line limit):

- **Constants and mode configuration.**
  - `JURY_MIN_PROVIDERS = 2`: cross-vendor jury routing requires at least `2` distinct configured provider keys (author + distinct reviewer vendor).
  - `JURY_DEFAULT_MODE = "auto"`: `JURY_CROSS_PROVIDER` accepts `off`, `auto` (default), or `strict`; any other value raises `Refused` (CLI exit `1`).
  - Reads `JURY_PROVIDER` and `MODEL_PROVIDER` defaults cleanly alongside `profiles/providers.json` (`default_model`, `default_harness`, `env_var`).
- **Boolean secret presence only.** `select_reviewer(author_provider, author_harness, risk, providers_cfg, env)` inspects only boolean key presence (`bool(val)` after filtering empty/placeholder values such as `replace-me`, `changeme`, `todo`) for the configured provider key variables in `profiles/providers.json` (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ZAI_API_KEY`). Secret values are never logged, stored, or returned.
- **Deterministic cross-vendor selection (`test-reviewer` / `architect`).**
  - For `medium` or `high` risk PRs when `mode` is `auto` or `strict`, not in `replay` mode at L0, and at least `JURY_MIN_PROVIDERS = 2` provider keys are configured including an alternate vendor different from `author_provider`, `select_reviewer` deterministically selects the preferred configured alternate vendor (`JURY_PROVIDER` if set, valid, different from `author_provider`, and present; otherwise the first configured alternate in `profiles/providers.json` order) and returns its `(provider, model, harness)` with `cross_vendor: true`.
  - Under `off` mode, `cross_vendor` is `false` and the default reviewer is kept with an explicit `reason`.
  - Under `low` risk, fewer than `2` configured vendor keys (or no alternate vendor key), or `replay` mode at L0:
    - Under `auto`: keeps the default reviewer (`cross_vendor: false`) with an explicit `reason`.
    - Under `strict`: raises `Refused` (CLI exit `1`) so strict policy never silently falls back to single-vendor or replay review.
- **CLI subcommands (`select-env` and `inspect`).** Both subcommands read optional `.crew/<ticket>/out/run.json` and/or `$DOC_FILE` (plus explicit flags and environment variables) and emit structured JSON metadata:
  `{"mode", "cross_vendor", "author_provider", "reviewer_provider", "reviewer_model", "reviewer_harness", "reason"}`.

### 2. Additive Signed Provenance Linkage (`core/provenance.py`)

Extend `core/provenance.py` (`statement()`) additively:

- When `jury` metadata exists on the ticket document (`doc["review"]["jury"]`, `doc["architect"]["jury"]`, `doc["jury"]`) or on the ticket's `.crew/<ticket>/review/out/run.json` / `.crew/<ticket>/out/run.json`, include the normalized `jury` summary inside the corresponding `predicate.agents[]` entry and on `predicate.review` in the in-toto `Statement/v1` attestation.
- When `jury` metadata is absent on the ticket and run, omit `jury` fields and `predicate.review` completely so existing provenance statements, `missing` counts, and signature payloads remain 100% backward compatible.
- Existing recursive secret scrubbing (`scrub_secrets` and `_has_unredacted_secret`) continues to cover every field including `jury`.

### 3. `outside/*` `AgentHarness` Support (`fixtures/outside-adapters/file-harness/` and `ci/outside.sh`)

- Add `fixtures/outside-adapters/file-harness/` (`run.sh`, `version.sh`, `README.md`), written in `shellcheck -s sh`-clean POSIX `sh` (`set -eu`), implementing a cold local scripted/file `AgentHarness` satisfying `harness-conformance` contract expectations:
  - Enforces required role/ticket/workspace/budget env vars, writes `$OUT/hooks.log` (`ALLOW`/`DENY` records), enforces `MAX_TURNS` and `TOKEN_BUDGET` stop markers without leaving `pr.json` on over-budget stops, denies unsafe/secret paths (`..`, `.env`, `*.key`, `*.pem`, `git push`), commits under `lights-out-crew` when acting as a writer role, and emits `$OUT/final.md`, `$OUT/pr.json`, `$OUT/patch`, and `$OUT/run.json`.
  - Exposes `version.sh` returning deterministic harness version metadata.
- Update `ci/outside.sh` to replace the old stub `AGENT_HARNESS=outside/json-files` refusal with the full `AGENT_HARNESS=outside/file-harness` admission lifecycle matching the other `7` ports (bringing verified `outside/*` ports to `8`): unproven refusal -> `trial AGENT_HARNESS` -> refusal without `ADAPTERS_TRIAL` -> admission with `ADAPTERS_TRIAL=AGENT_HARNESS` -> running `run.sh` and `version.sh` -> `register AGENT_HARNESS harness-conformance` -> admitted cleanly -> `1`-byte tamper refused -> symlink refused.

### 4. External Orchestrator Integration Recipes (`surfaces/orchestrators/`)

Add `surfaces/orchestrators/README.md`, `surfaces/orchestrators/openhands.md`, `surfaces/orchestrators/paperclip.md`, and `surfaces/orchestrators/gastown.md` documenting how OpenHands, Paperclip, and Gas Town invoke `lights-out gates` and `lights-out-gates --light` as deterministic PR verification gates (never as judges replacing the tower, per ADR 0033 and ADR 0058 §6). All cited paths, commands, and `KEY=` assignments are checked continuously by `ci/docs-drift.sh`.

## Alternatives

- **Hardcoding vendor API key names or model IDs inside `core/jury.py`:** Rejected. `ci/model-free.sh` forbids model IDs and `*_API_KEY` literals inside `core/` and `core/tests/`. Reading `env_var`, `default_model`, and `default_harness` dynamically from `profiles/providers.json` keeps `core/` cleanly decoupled from vendor model churn.
- **Rewriting `core/steps/review.sh` to invoke `core/jury.sh` inside this slice:** Deferred. `core/steps/review.sh` is an existing shell-boundary debt script (`71` lines in `ci/shell-boundary.sh`) frozen outside this slice's boundaries; shipping `core/jury.py`, `core/jury.sh`, and additive `core/provenance.py` support delivers the selection/inspection engine and attestation linkage cleanly without touching frozen step scripts.
- **Running live container builds inside cold `ci/outside.sh` for `AGENT_HARNESS`:** Rejected. `ci/outside.sh` is guaranteed cold and engine-free (`ADR 0058` §5); a local scripted/file `AgentHarness` fixture exercises the exact `core/adapters.py` admission, trial, verb execution, register, tamper, and symlink guarantees parity-matched to `file-delivery`, `file-codehost`, `file-registry`, `file-flags`, `file-tracker`, and `file-eventbus`.
- **Emitting an empty `jury: null` field on every legacy provenance statement:** Rejected. Existing ADR 0117 provenance tests and signed evidence packs expect unchanged predicates when jury metadata is absent; additive inclusion only when `jury` exists guarantees zero regression.

## Consequences

- `core/jury.py` and `core/jury.sh` provide deterministic, secret-safe cross-provider reviewer selection and inspection for `test-reviewer` and `architect`, ready for tower integration and CLI queries.
- Signed SLSA / in-toto provenance attestations (`core/provenance.py`) carry `jury` lineage automatically whenever jury metadata is recorded on a ticket or run, with zero impact on legacy tickets.
- All `8` conformance-gated `outside/*` ports (`STATE_STORE`, `DELIVERY`, `CODE_HOST`, `REGISTRY`, `FLAGS`, `TRACKER`, `EVENT_BUS`, `AGENT_HARNESS`) now have cold end-to-end fixtures and admission tests in `ci/outside.sh`.
- External orchestrator users (OpenHands, Paperclip, Gas Town) have drift-checked recipes in `surfaces/orchestrators/` using `lights-out gates` and `lights-out-gates --light` without coupling `core/` to any external orchestrator.

## Provenance

| Threshold / Constant | Value | Provenance |
|---|---|---|
| `JURY_MIN_PROVIDERS` | `2` | Decision (`M37` / K5): minimum distinct vendor keys (author + independent alternate reviewer) required to form a cross-provider jury. |
| `JURY_DEFAULT_MODE` | `"auto"` | Decision (`M37` / K5): enables cross-provider selection automatically on `medium`/`high` risk when `>= 2` vendor keys exist while degrading safely to default under `low` risk, `< 2` keys, or L0 `replay`. |
| `core/jury.sh` code lines | `<= 20` lines (platform ceiling `30`) | Derived from `ci/shell-boundary.sh` (`limit=30`, ADR 0052 §4), keeping a `10`-line margin beneath the platform limit. |
| Verified `outside/*` ports in `ci/outside.sh` | `8` ports (`1`-byte tamper check) | Derived from `core/adapters.py` `SUITES` and `ADR 0058` / `ADR 0104` / `ADR 0129` (`7` prior ports + `AGENT_HARNESS`), testing `1`-byte mutation refusal per ADR 0058 §2. |
| CLI exit codes | `0` (success/JSON), `1` (`Refused` / `NO-GO`), `2` (usage error) | Derived from `core/provenance.py` and `core/adapters.py` (`ADR 0058`, `ADR 0112`, `ADR 0117`). |
