# ADR 0137 — Labeled-Diff Reviewer & Deterministic Gate Catch-Rate Evaluation Suite

- Status: accepted
- Date: 2026-11-25
- Milestone: M95 (`swarm/reviewer-eval`)
- Builds on: [ADR 0015](0015-pr-gates-at-m4.md), [ADR 0016](0016-risk-model-and-merge-policy.md), [ADR 0021](0021-review-roles-at-m9.md), [ADR 0052](0052-where-shell-is-allowed.md), [ADR 0053](0053-tests-that-prove-and-tickets-that-wait.md), [ADR 0054](0054-the-project-map.md), [ADR 0123](0123-database-expand-contract-migration-gate.md), [ADR 0130](0130-cross-provider-jury-and-outside-harness.md)

## Context

Lights-out gates every pull request with deterministic checks (`perimeter`, `test-weakening`, `migration-safety`, `risk`, `secrets`, plus engine-backed `deps`, `sast`, `test`, `mutation`, and `test-proof`) alongside two LLM review roles (`test-reviewer` and `architect`, ADR 0021). Previously, the repository lacked a reproducible, self-contained labeled-diff benchmark measuring:

1. How many real defect classes deterministic diff checks catch alone versus an LLM reviewer alone versus both combined, and at what false-positive rate on clean changes.
2. How `MAP_BRIEF=off` versus `MAP_BRIEF=on` (ADR 0054 §5) affects `architect` review accuracy, prompt footprint, API tokens, latency, and citation grounding across repository tickets and patches.
3. A single L0-offline (`--mode replay`, zero API keys) and L1-live (`--mode live`) evaluation harness producing verifiable precision, recall, F1, false-positive rate, and per-class catch-rate telemetry without violating `core/`'s hexagonal purity (`ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`, `ci/shell-boundary.sh`).

## Decision

1. **Add a 12-case labeled diff evaluation suite under `fixtures/reviewer-eval/`:**
   - `fixtures/reviewer-eval/manifest.json` declares `12` self-contained synthetic cases using neutral module names (`src/`, `tests/`, `migrations/`):
     - `6` defective cases covering distinct defect/gate classes: `01-tautological-assertion` (`assert fee == fee` / `assert True`), `02-silent-exception-swallow` (`except Exception: pass`), `03-perimeter-escape` (editing an undeclared file outside `## Perimeter`), `04-off-by-one-boundary` (`<=` instead of `<` quota boundary), `05-missing-regression-test` (test only exercises non-boundary timestamp), and `06-unsafe-sql-migration` (`ALTER TABLE ... DROP COLUMN` without `Contract-After:`).
     - `6` clean cases (`07-clean-discount-fix`, `08-clean-retry-backoff`, `09-clean-expand-migration`, `10-clean-pagination-bounds`, `11-clean-error-propagation`, and `12-clean-contract-migration` declaring `Contract-After: 0001_init.sql` against a `base/` migration).
   - Each case stores `ticket.md`, `diff.patch`, `base/`, `head/`, optional `policy.env`, and recorded L0 `replay.json` verdicts keyed by prompt `input_sha256`.
   - `fixtures/reviewer-eval/map_brief_compare_manifest.json` and `fixtures/reviewer-eval/map_brief_replay.json` store the `6`-case repository `architect` comparison inputs and recorded L0 replay outputs.
2. **Add `core/reviewer_eval.py` and thin wrapper `core/reviewer_eval.sh`:**
   - `core/reviewer_eval.sh` stays within `25` non-blank, non-comment code lines (under the `30`-line `ci/shell-boundary.sh` limit), supports `LIGHTS_OUT_CLI_ARGS` splitting, and emits the single-line stdout contract `GO: reviewer-eval — <caught>/<defective> defects caught, <fp>/<clean> false positives (<mode>)` (or `--json` when requested).
   - `core/reviewer_eval.py` remains stdlib-only at L0 (`--mode replay`, default), strictly `vendor-free`, `app-free`, and `model-free` (resolving provider/model/credential env var names dynamically from `profiles/l1.env`, `profiles/providers.json`, and `roles/*/role.env` without printing or storing any secret value).
   - Deterministic evaluation reuses `core/map.py` (`perimeter`), `core/testdiff.py` (`weakening`, `declared`), `core/gates/migration.py` (`scan_sql`, `scan_python`, `scan_ruby`, `declarations`), `core/risk.py` (`classify`, `parse_policy`), and `gitleaks stdin` (with structural secret-assignment/PEM checks).
   - Live evaluation (`--mode live`) supports pluggable `reviewer_fn` injection and built-in `google.genai` / `litellm` execution with a `120` s HTTP timeout, single-attempt transport (`attempts=1`) so HTTP `429` quota exhaustion halts immediately without hidden retry loops (`status: "not_run_quota_exhausted"`), a single bounded retry on empty `0`-output-token backend responses, per-case `LiveCallError` isolation, and `--record-replay`.
   - `compare_map_brief(...)` builds or opens the `core/map.py` SQLite index at `HEAD`, constructs both `MAP_BRIEF=off` and `MAP_BRIEF=on` prompts for `architect`, and measures accuracy, prompt bytes, API tokens, latency, and slice ADR/page citations.
3. **Publish live Gemini L1 results and honest multi-provider scoreboard:**
   - `docs/scoreboard/reviewer-catch-rate.json` and `docs/plans/live-validation-v1.0.0.md` record the measured Gemini L1 (`gemini-3.8-flash`) results inside Podman (`deterministic` alone `2/6` caught / `0/6` FP; `gemini-3.8-flash` reviewer alone `5/6` caught / `0/6` FP; combined `6/6` caught / `0/6` FP; `MAP_BRIEF=off` vs `on` `6/6` vs `6/6` accuracy, `+7,646` total tokens across `6` cases), while honestly reporting `anthropic` as billing-blocked and `openai` / `zai` as `not_run`.

## Alternatives rejected

- **Measuring catch rate using only LLM review without deterministic gates:** Rejected. Live Gemini L1 evaluation demonstrated empirically that `architect` missed `06-unsafe-sql-migration` (`APPROVED` after `10,400` thought tokens), whereas `migration-safety` caught `[drop-column]` deterministically; combining gates and reviewer raised catch rate from `5/6` (`83.3%`) to `6/6` (`100.0%`).
- **Hardcoding model IDs, provider names, or sample workload paths inside `core/reviewer_eval.py`:** Rejected. Breaks `ci/model-free.sh`, `ci/vendor-free.sh`, and `ci/app-free.sh`; dynamic lookup from `profiles/` and neutral synthetic fixtures preserve hexagonal boundaries.
- **Fabricating or extrapolating missing providers (`anthropic`, `openai`, `zai`):** Rejected by `CLAUDE.md` ("niente mock nascosti"): only live runs executed inside Podman with real keys are reported as executed.

## Consequences

- `sh core/reviewer_eval.sh` runs offline at L0 (`--mode replay`) in seconds with zero API keys, verifying deterministic gates, replay SHA alignment, precision, recall, F1, and per-class catch rates.
- Operators and CI evaluators can re-run `--mode live` against any configured provider or benchmark new models/prompts on the same `12` labeled cases and `MAP_BRIEF` comparison manifest.
- Unit tests in `core/tests/test_reviewer_eval.py` verify every deterministic gate clause, replay verification, metrics math, HTTP `429` quota stop behavior, `MAP_BRIEF` comparison, and secret non-leakage without network calls.

## Provenance

| Constant / Threshold / Measurement | Value | Provenance |
|---|---|---|
| Labeled diff suite size | `12` cases (`6` defective, `6` clean) | Decision (`M95`): `1` case per target defect/gate class (`6` classes) balanced with `6` clean bugfix and safe migration controls. |
| `core/reviewer_eval.sh` wrapper budget | `<= 25` code lines (platform limit `30`) | Derived from ADR 0052 §4 / `ci/shell-boundary.sh` (`LIMIT = 30`), matching ADR 0131/0135 wrapper margin (`25` lines). |
| Deterministic checks evaluated | `5` checks (`4` pass/fail + `risk` routing) | Derived from ADR 0015, ADR 0016, ADR 0021, ADR 0053, and ADR 0123 (`perimeter`, `test-weakening`, `migration-safety`, `secrets`, `risk`). |
| Live HTTP request timeout | `120` s (`120000` ms) | Measured during live Gemini L1 tuning: `90` s hit `504 DEADLINE_EXCEEDED` on `06-unsafe-sql-migration` (`architect`, `thinking=high`); `120` s accommodated both `06` (`74.099` s) and `10` (`118.862` s). |
| Live HTTP retry attempts | `1` HTTP attempt (`attempts=1`) + `1` empty-response retry | Decision (`M95`): prevents `google.genai` default `5`-attempt backoff from masking HTTP `429` quota stops while recovering transient `0`-output-token replies (`0001-honest`). |
| `HEAD` context cap in prompt builder | `24000` bytes (`~6000` tokens) | Decision (`M95`): bounds attached post-diff file context cleanly well below model context limits. |
| `MAP_BRIEF` comparison slice count | `6` repository tickets (`0001`, `0005`, `0012`, `0020`, `0022`, `0001-sast-probe`) | Decision (`M95`): exercises existing committed tickets/patches with populated `core/map.py` slices (`5`–`9` ADRs each) plus a perimeter/SAST negative control. |
| Live Gemini L1 `12`-case results | `det=2/6`, `rev=5/6`, `combined=6/6`, `fp=0/6`, `69,675` tokens (`$0.189212` list) | Measured directly in Podman (`localhost/lights-out-workbench:4150781479`) on `2026-11-25` using `gemini-3.8-flash` (`profiles/prices.json` list rates). |
| Live Gemini L1 `MAP_BRIEF` results | `off=6/6` (`35,449` tok, `139.713` s) vs `on=6/6` (`43,095` tok, `134.818` s) | Measured directly in Podman (`localhost/lights-out-workbench:4150781479`) on `2026-11-25` across `6` repository tickets using `architect` (`gemini-3.8-flash`). |
