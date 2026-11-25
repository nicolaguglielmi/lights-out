# Live validation report: the reviewer's catch rate and `MAP_BRIEF`

- **Date:** 2026-11-25
- **Workbench image:** `localhost/lights-out-workbench:4150781479` (Podman, Python `3.13.5`, `google-adk 2.14.0`, `gitleaks 8.30.1`)
- **Predecessor report:** [live-validation-2026-11-25.md](live-validation-2026-11-25.md) (`MAP_BRIEF` footprint & live ADK role evals)
- **Scoreboard artifact:** [reviewer-catch-rate.json](../scoreboard/reviewer-catch-rate.json)
- **Security & Honesty Policy:** Zero `.env` secrets are stored, echoed, or committed (`GEMINI_API_KEY` passed only via `-e` from a silent subshell into ephemeral `podman run --rm`). `docs/scoreboard/m24-tenacity.json` remains untouched. Unexecuted or billing-blocked providers (`anthropic`, `openai`, `zai`) are documented honestly without fabrication.

---

## 1. Labeled-Diff Reviewer & Deterministic Gate Catch-Rate Suite (`core/reviewer_eval.py`)

Evaluated across all `12` self-contained labeled diff cases in `fixtures/reviewer-eval/` (`6` defective cases spanning distinct defect/gate classes + `6` clean bugfix/migration cases):

- **Deterministic checks:** `perimeter`, `test-weakening`, `migration-safety`, `secrets`, plus `risk` routing (`core/testdiff.py`, `core/risk.py`, `core/map.py`, `core/gates/migration.py`, `gitleaks stdin`).
- **Live L1 reviewer:** `gemini-3.8-flash` (`PROFILE=l1`, `google-genai`, role-configured `THINKING=high` from `roles/test-reviewer/role.env` and `roles/architect/role.env`, `120s` timeout, `1` HTTP attempt).
- **L0 offline replay check:** `sh core/reviewer_eval.sh` replays the recorded Gemini responses from `fixtures/reviewer-eval/cases/*/replay.json` with zero API keys:
  `GO: reviewer-eval — 6/6 defects caught, 0/6 false positives (replay)`.

### Aggregate Catch-Rate Comparison

| Evaluation Track | Caught / Defective | False Positives / Clean | Precision | Recall | F1 | Accuracy | Total Tokens | List Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| `deterministic` gates alone (L0) | `2 / 6` | `0 / 6` | `1.0000` | `0.3333` | `0.5000` | `0.6667` | `0` | `$0.000000` |
| `gemini-3.8-flash` reviewer alone (L1) | `5 / 6` | `0 / 6` | `1.0000` | `0.8333` | `0.9091` | `0.9167` | `69,675` | `$0.189212` |
| **Combined (`deterministic` + `gemini-3.8-flash`)** | **`6 / 6`** | **`0 / 6`** | **`1.0000`** | **`1.0000`** | **`1.0000`** | **`1.0000`** | `69,675` | `$0.189212` |

### Per-Case Measured Results

| Case ID | Class | Role | Expected | Det. Gates (`failed`) | Risk | Gemini L1 (`gemini-3.8-flash`) | Combined | Tokens (`in`/`out`/`thoughts`/`total`) | Latency (s) |
|---|---|---|---|---|---|---|---|---|---|
| `01-tautological-assertion` | `tautological-assertion` | `test-reviewer` | `CHANGES-REQUESTED` | `APPROVED` (`[]`) | `low` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` | `1927 / 115 / 1536 / 3578` | `18.161` |
| `02-silent-exception-swallow` | `silent-exception-swallow` | `test-reviewer` | `CHANGES-REQUESTED` | `APPROVED` (`[]`) | `low` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` | `2079 / 147 / 732 / 2958` | `9.894` |
| `03-perimeter-escape` | `perimeter-escape` | `test-reviewer` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` (`perimeter`) | `low` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` | `2002 / 140 / 1336 / 3478` | `14.451` |
| `04-off-by-one-boundary` | `off-by-one-boundary` | `test-reviewer` | `CHANGES-REQUESTED` | `APPROVED` (`[]`) | `low` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` | `1883 / 201 / 1189 / 3273` | `12.913` |
| `05-missing-regression-test` | `missing-regression-test` | `test-reviewer` | `CHANGES-REQUESTED` | `APPROVED` (`[]`) | `low` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` | `2057 / 164 / 1477 / 3698` | `14.992` |
| `06-unsafe-sql-migration` | `unsafe-sql-migration` | `architect` | `CHANGES-REQUESTED` | `CHANGES-REQUESTED` (`migration-safety`) | `high` | `APPROVED` *(missed)* | `CHANGES-REQUESTED` | `1558 / 133 / 10400 / 12091` | `74.099` |
| `07-clean-discount-fix` | `clean-bugfix` | `test-reviewer` | `APPROVED` | `APPROVED` (`[]`) | `low` | `APPROVED` | `APPROVED` | `2370 / 174 / 2759 / 5303` | `23.397` |
| `08-clean-retry-backoff` | `clean-bugfix` | `test-reviewer` | `APPROVED` | `APPROVED` (`[]`) | `low` | `APPROVED` | `APPROVED` | `1982 / 209 / 1349 / 3540` | `14.915` |
| `09-clean-expand-migration` | `clean-expand-migration` | `architect` | `APPROVED` | `APPROVED` (`[]`) | `high` | `APPROVED` | `APPROVED` | `1678 / 91 / 2318 / 4087` | `21.797` |
| `10-clean-pagination-bounds` | `clean-bugfix` | `test-reviewer` | `APPROVED` | `APPROVED` (`[]`) | `low` | `APPROVED` | `APPROVED` | `2372 / 290 / 16373 / 19035` | `118.862` |
| `11-clean-error-propagation` | `clean-bugfix` | `test-reviewer` | `APPROVED` | `APPROVED` (`[]`) | `low` | `APPROVED` | `APPROVED` | `2440 / 178 / 2269 / 4887` | `17.866` |
| `12-clean-contract-migration` | `clean-contract-migration` | `architect` | `APPROVED` | `APPROVED` (`[]`) | `high` | `APPROVED` | `APPROVED` | `1675 / 70 / 2002 / 3747` | `19.756` |

### Key Findings from the 12-Case Run

1. **Complementarity of deterministic gates + LLM review:** Deterministic gates caught `2/6` defects (`03-perimeter-escape` and `06-unsafe-sql-migration`) with `0` tokens and `0` false positives, while missing pure semantic/test defects (`01`, `02`, `04`, `05`). Conversely, `gemini-3.8-flash` caught `5/6` defects (`01`–`05`) with `0/6` false positives (correctly honoring `Contract-After: 0001_init.sql` in `12-clean-contract-migration`), while approving `06-unsafe-sql-migration` because the `architect` prompt focuses on structural cost rather than canary rollback verification. Combined (`gates + reviewer`), the suite caught **`6/6` (`100%`)** with **`0/6` (`0%`) false positives**.
2. **Thinking-budget latency tail (`high` vs `medium`):** Under `THINKING=high`, `06-unsafe-sql-migration` (`architect`) exceeded a `90s` request deadline twice (`HTTP 504 DEADLINE_EXCEEDED`) before completing at `74.099s` (`10,400` thought tokens) under a `120s` timeout, and `10-clean-pagination-bounds` took `118.862s` (`16,373` thought tokens). An isolated probe of `06-unsafe-sql-migration` with `--thinking medium` returned `CHANGES-REQUESTED` in `11.886s` (`2,645` total tokens).

---

## 2. Live `MAP_BRIEF=off` vs `MAP_BRIEF=on` `architect` Comparison

Evaluated `architect` (`roles/architect/prompt.md`, `gemini-3.8-flash`, `thinking=high`) across `6` representative repository tickets and patches (`fixtures/reviewer-eval/map_brief_compare_manifest.json`), injecting deterministic `core/map.py slice` output (`ADRs`, `Pages`, `Tests`, `Tickets`, `Symbols`) when `MAP_BRIEF=on`.

### Aggregate Comparison (`MAP_BRIEF=off` vs `MAP_BRIEF=on`)

| Metric | `MAP_BRIEF=off` | `MAP_BRIEF=on` | Delta (`on - off`) |
|---|---|---|---|
| Cases evaluated | `6 / 6` | `6 / 6` | `0` |
| Verdict accuracy | `6 / 6` (`100.0%`) | `6 / 6` (`100.0%`) | `0.0` |
| Total prompt bytes | `48,069 B` (mean `8,011.5 B`) | `65,618 B` (mean `10,936.3 B`) | `+17,549 B` (`+2,924.8 B/case`) |
| Input tokens (`cached`) | `21,235` (`8,076` cached) | `27,372` (`14,186` cached) | `+6,137` (`+28.9%`) |
| Output + Thoughts tokens | `789 + 13,425 = 14,214` | `790 + 14,933 = 15,723` | `+1,509` |
| Total API tokens | `35,449` (mean `5,908.2`) | `43,095` (mean `7,182.5`) | `+7,646` (`+21.6%`) |
| Wall-clock latency | `139.713 s` (mean `23.285 s`) | `134.818 s` (mean `22.470 s`) | `-4.895 s` (`-3.5%`) |
| Matched slice ADR / Page citations | `0` ADRs / `3` pages | `0` ADRs / `3` pages | `0` / `0` |
| List cost / Cached-adj. cost (USD) | `$0.069229` / `$0.063777` | `$0.079490` / `$0.069915` | `+$0.010261` / `+$0.006138` |

### Per-Ticket Breakdown

| Compare Case | Expected | Slice (B / ADRs / Pages) | `off` Verdict / Bytes / Tokens / Latency | `on` Verdict / Bytes / Tokens / Latency |
|---|---|---|---|---|
| `0001-honest` | `APPROVED` | `3,721 B` / `9` / `4` | `APPROVED` / `8,649 B` / `7,177` / `37.271s`* | `APPROVED` / `12,376 B` / `10,840` / `42.558s` |
| `0005-geocoder` | `APPROVED` | `4,337 B` / `9` / `5` | `APPROVED` / `22,487 B` / `14,466` / `52.346s` | `APPROVED` / `26,830 B` / `15,237` / `45.098s` |
| `0012-capacity` | `APPROVED` | `2,166 B` / `7` / `4` | `APPROVED` / `5,334 B` / `3,840` / `12.256s` | `APPROVED` / `7,506 B` / `4,848` / `14.738s` |
| `0020-metrics` | `APPROVED` | `2,473 B` / `8` / `4` | `APPROVED` / `4,320 B` / `3,401` / `11.206s` | `APPROVED` / `6,799 B` / `4,004` / `9.467s` |
| `0022-db-name` | `APPROVED` | `1,095 B` / `5` / `3` | `APPROVED` / `2,496 B` / `2,609` / `9.569s` | `APPROVED` / `3,597 B` / `3,061` / `10.053s` |
| `0001-sast-probe` | `CHANGES-REQUESTED` | `3,721 B` / `9` / `4` | `CHANGES-REQUESTED` / `4,783 B` / `3,956` / `17.065s` | `CHANGES-REQUESTED` / `8,510 B` / `5,105` / `12.904s` |

*\*Note: `0001-honest (off)` returned one transient 0-output-token response on initial attempt and succeeded cleanly on the single bounded retry.*

**Interpretation:** For single-turn `architect` diff review, `MAP_BRIEF=on` preserved `6/6` (`100%`) accuracy and parity latency (`22.47s` vs `23.29s` mean) while adding `+1,022.8` input tokens per ticket (`+$0.0010`–`$0.0017` list cost/review). Because `architect` already sees the full diff and declared perimeter in single-turn review, keeping `MAP_BRIEF=off` by default in `profiles/*.env` (ADR 0054 §5) remains justified until live M24 `dev-worker` A/B runs measure whether multi-turn coding workers reduce grep turns and reworks with `MAP_BRIEF=on`.

---

## 3. Multi-Provider Live Readiness & Status Matrix

| Provider | Model | Status | Observed Evidence |
|---|---|---|---|
| `gemini` | `gemini-3.8-flash` | **Executed (`L1`)** | `12/12` labeled reviewer-eval cases + `12/12` `MAP_BRIEF` comparison calls executed in Podman (`6/6` combined defects caught, `0/6` FP; `6/6` vs `6/6` `architect` accuracy). |
| `anthropic` | `claude-sonnet-5-5` | **Blocked (billing)** | Account credit balance too low (`Your credit balance is too low to access the Anthropic API`, request ID `req_<redacted>`, observed in Podman L1 ADK eval — [live-validation-2026-11-25.md](live-validation-2026-11-25.md)). |
| `openai` | `gpt-6.1-sol` | **`not_run`** | `OPENAI_API_KEY` absent from `.env` and environment. |
| `zai` | `glm-5.3` | **`not_run`** | `ZAI_API_KEY` absent from `.env` and environment. |
