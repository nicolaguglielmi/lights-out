# Live Validation & `MAP_BRIEF` Measurement Report (2026-11-25)

- **Date:** 2026-11-25
- **Commit base:** `main` @ `32ccfa67910070be2244cf65f58f457597ff7d14`
- **Workbench image:** `localhost/lights-out-workbench:4150781479` (Python `3.13.5`, `adk 2.14.0`, `gitleaks 8.30.1`)
- **Security & Honesty Policy:** Zero `.env` secret values are stored, echoed, or committed. `docs/scoreboard/m24-tenacity.json` was left completely untouched (`openai` remains honestly `not_run`). Every latency, exit code, and error below was directly observed from Podman and host CLI runs.

---

## 1. `MAP_BRIEF=off` vs `MAP_BRIEF=on` Measurement (`core/map_brief_eval.py`)

Evaluated across all 16 repository tickets (`tickets/*.md`) at commit `32ccfa67910070be2244cf65f58f457597ff7d14` against `.crew/map.sqlite` (`adr=126`, `command=154`, `file=1210`, `key=275`, `page=151`, `symbol=6719`, `test=132`, `ticket=16`) and `app/worker.md` (`1,061` bytes, `265.25` approx tokens).

### Aggregate Summary

- **Stdout contract:** `GO: map-brief-eval — 16 tickets (13 with perimeter), off=58954B (~14738.5 tok) -> on=103092B (~25773.0 tok), delta=+44138B (+11034.5 tok), deterministic=yes`
- **Determinism:** `checked=true`, `deterministic=true` across `13/13` perimetered tickets (byte-identical `slice_sha256` comparing `.crew/map.sqlite` against a fresh temp `core/map.py build`).
- **Means (all 16 tickets):** `off = 3,684.6 B`, `on = 6,443.2 B`, `delta = +2,758.6 B`
- **Means (13 tickets with `## Perimeter`):** `off = 3,342.8 B`, `on = 6,738.1 B`, `slice = 3,389.2 B` (`847.3` approx tokens at `len/4`), `min_slice = 469 B` (`0021`), `max_slice = 11,251 B` (`0006`)
- **Graph linkage coverage (13 perimetered tickets):** `12` with linked ADRs (`72` unique ADRs), `12` with linked docs pages (`11` unique pages), `4` with linked test files (`4` unique test files)

### Per-Ticket Measured Footprint

| Ticket | Perimeter | Globs | Ticket (B) | `MAP_BRIEF=off` (B / tok) | `MAP_BRIEF=on` (B / tok) | Slice (B / tok) | Delta (B / %) | Files (Missing Globs) | ADRs | Pages | Tests | History | Symbols |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `0001` | yes | 5 | 3,245 | 4,312 / 1,078.0 | 7,965 / 1,991.25 | 3,647 / 911.75 | +3,653 (+84.7%) | 6 (0) | 9 | 3 | 2 | 5 | 16 |
| `0002` | no | 0 | 4,955 | 6,022 / 1,505.5 | 6,022 / 1,505.5 | 0 / 0.0 | +0 (0.0%) | 0 (0) | 0 | 0 | 0 | 0 | 0 |
| `0003` | no | 0 | 3,495 | 4,562 / 1,140.5 | 4,562 / 1,140.5 | 0 / 0.0 | +0 (0.0%) | 0 (0) | 0 | 0 | 0 | 0 | 0 |
| `0004` | no | 0 | 3,846 | 4,913 / 1,228.25 | 4,913 / 1,228.25 | 0 / 0.0 | +0 (0.0%) | 0 (0) | 0 | 0 | 0 | 0 | 0 |
| `0005` | yes | 6 | 7,000 | 8,067 / 2,016.75 | 12,308 / 3,077.0 | 4,235 / 1,058.75 | +4,241 (+52.6%) | 7 (0) | 9 | 4 | 2 | 6 | 28 |
| `0006` | yes | 6 | 1,980 | 3,047 / 761.75 | 14,304 / 3,576.0 | 11,251 / 2,812.75 | +11,257 (+369.4%) | 68 (0) | 16 | 4 | 0 | 7 | 0 |
| `0007` | yes | 4 | 1,273 | 2,340 / 585.0 | 5,313 / 1,328.25 | 2,967 / 741.75 | +2,973 (+127.1%) | 2 (2) | 23 | 1 | 0 | 7 | 0 |
| `0008` | yes | 4 | 1,289 | 2,356 / 589.0 | 5,902 / 1,475.5 | 3,540 / 885.0 | +3,546 (+150.5%) | 3 (1) | 30 | 5 | 0 | 3 | 0 |
| `0009` | yes | 4 | 1,148 | 2,215 / 553.75 | 5,042 / 1,260.5 | 2,821 / 705.25 | +2,827 (+127.6%) | 3 (1) | 21 | 3 | 0 | 6 | 0 |
| `0010` | yes | 11 | 3,849 | 4,916 / 1,229.0 | 10,538 / 2,634.5 | 5,616 / 1,404.0 | +5,622 (+114.4%) | 6 (5) | 47 | 5 | 0 | 8 | 0 |
| `0011` | yes | 2 | 1,136 | 2,203 / 550.75 | 3,522 / 880.5 | 1,313 / 328.25 | +1,319 (+59.9%) | 2 (0) | 5 | 3 | 0 | 2 | 0 |
| `0012` | yes | 3 | 2,620 | 3,687 / 921.75 | 5,757 / 1,439.25 | 2,064 / 516.0 | +2,070 (+56.1%) | 2 (1) | 7 | 3 | 0 | 6 | 12 |
| `0013` | yes | 4 | 2,101 | 3,168 / 792.0 | 5,916 / 1,479.0 | 2,742 / 685.5 | +2,748 (+86.7%) | 4 (0) | 9 | 3 | 2 | 5 | 8 |
| `0020` | yes | 3 | 1,388 | 2,455 / 613.75 | 4,847 / 1,211.75 | 2,386 / 596.5 | +2,392 (+97.4%) | 3 (0) | 8 | 3 | 1 | 3 | 15 |
| `0021` | yes | 2 | 1,323 | 2,390 / 597.5 | 2,865 / 716.25 | 469 / 117.25 | +475 (+19.9%) | 0 (2) | 0 | 0 | 0 | 0 | 0 |
| `0022` | yes | 1 | 1,234 | 2,301 / 575.25 | 3,316 / 829.0 | 1,009 / 252.25 | +1,015 (+44.1%) | 1 (0) | 5 | 2 | 0 | 1 | 1 |

**Interpretation:** `MAP_BRIEF=on` adds a modest, deterministic `~847` tokens on average across perimetered tickets (under `2,815` tokens even on broad dependency tickets `0006`/`0010`), directly exposing governing ADRs, documentation pages, and test files without needing runtime grep turns. Turning `MAP_BRIEF=on` by default in `profiles/*.env` remains gated on live M24 A/B rework/turn trials (ADR 0054 §5).

---

## 2. Non-Destructive Live Readiness Preflight (`core/live_preflight.py`)

Executed both on the macOS host (`--env-file .env`) and inside Podman (`localhost/lights-out-workbench:4150781479`, receiving `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` via `-e` from a silent subshell).

### Provider Key Presence (Boolean Only)

| Provider | Env Var | Present in `.env` | Status | Default Harness / Model |
|---|---|---|---|---|
| `gemini` | `GEMINI_API_KEY` | `True` | `ready` | `gemini-cli` / `gemini-3.8-flash` |
| `anthropic` | `ANTHROPIC_API_KEY` | `True` | `ready` | `claude-code` / `claude-sonnet-5-5` |
| `openai` | `OPENAI_API_KEY` | `False` | `missing-key` | `codex` / `gpt-6.1-sol` |
| `zai` | `ZAI_API_KEY` | `False` | `missing-key` | `claude-code` / `glm-5.3` |

### Workstation CLI Visibility & Auth (Read-Only)

- **Host environment (`kind=host`):**
  - `gh`: installed & authenticated (`gh auth status` exit `0`, account `nicolaguglielmi`, scopes `delete_repo`, `gist`, `read:org`, `repo`, `workflow`).
  - `gcloud`: installed & authenticated (`gcloud auth list` exit `0`, active account `<redacted>`, default gcloud project `<redacted>`).
  - `aws`: not installed (`exit 1`, `missing-auth`).
  - `az`: not installed (`exit 1`, `missing-auth`).
  - `podman`: installed and answering (`ready`).
- **Workbench container (`kind=container`):**
  - `gh`, `gcloud`, `aws`, `az`, and `container_engine` cleanly report `not-visible-in-container`, while `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` passed via `-e` report `present=True` (`sources=["env"]`).

### Readiness Matrix

| Track | Status | Observed Gap | Exact Next Command(s) |
|---|---|---|---|
| `M24` (`openai`/`zai`) | `missing-key` | `2/4` provider keys configured (`openai`, `zai` missing); `core/m24_runner.py` reports `incomplete` (`2/3 providers run; not run: openai`) | `./go m24-check` · `export OPENAI_API_KEY='<key>' && ./go record-evals PROFILE=l1 MODEL_PROVIDER=openai MODEL=gpt-6.1-sol` · `export ZAI_API_KEY='<key>' && ./go record-evals PROFILE=l1 MODEL_PROVIDER=zai MODEL=glm-5.3` |
| `M21` (`github`) | `missing-key` | `gh` CLI authenticated on host (`nicolaguglielmi`), but `adapters/github/env.sh` requires `GITHUB_TOKEN` and `GITHUB_REPO` in `.env`/env | `export GITHUB_TOKEN='<value>' GITHUB_REPO='<owner>/<throwaway-repo>' && ./go codehost-conformance CODE_HOST=github` |
| `M8`/`M10`/`M44` (`cloud-conformance`) | `missing-auth` | `gcloud` CLI authenticated on host (`<redacted>`), but `.env`/env lacks `GOOGLE_CLOUD_PROJECT`/`GCP_PROJECT`, pipeline/region, and OIDC vars (`CLOUD_CONFORMANCE_ENABLED`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`) | `./go doctor --level l2` · `./go cloud-setup CLOUD=gcp-cloud-deploy` · `./go delivery-conformance DELIVERY=outside/gcp-cloud-deploy ADAPTERS_PATH="$(pwd)/plugins/cloud"` · `gh workflow run cloud-conformance.yml -f target=all` |

---

## 3. Real Live L1 ADK Evaluations (in Podman)

All runs executed inside `podman run --rm` (`localhost/lights-out-workbench:4150781479`) with `PLANE_HOST=nowhere.invalid` and without `ADK_RECORD` (leaving `fixtures/adk/` untouched and cleaning `.adk/` scratch dirs after each eval).

### L0 Replay Baseline

- `AGENT_HARNESS=replay sh ci/evals.sh`: **exit `0` (`GO: evals`)**, `82` unit tests in `19.202s` + all `adk eval` replay suites passed.

### Live `gemini` (`PROFILE=l1`, `MODEL_PROVIDER=gemini`, `MODEL=gemini-3.8-flash`, `AGENT_HARNESS=gemini-cli`)

- `ci/evals.sh` (`ROLES=release`): **exit `0` (`GO: evals`)**, total `218.592s` (`82` unit tests in `111.086s` + live `release/briefs` `adk eval` under concurrent container load).
- Per-evalset sequential `adk eval` measurements (`Tests passed` / `Tests failed`, `tool_trajectory_avg_score=1.0`, `EXACT`):

| Role / Evalset | Exit | Verdict | Elapsed (s) | Observed Summary |
|---|---|---|---|---|
| `release` (`briefs.evalset.json`) | `0` | **PASS** | `32.378` | `Tests passed: 2`, `Tests failed: 0` |
| `ops_triage` (`alerts.evalset.json`) | `0` | **PASS** | `26.353` (isolated) / `55.417` (batch) | `Tests passed: 3`, `Tests failed: 0` |
| `ops_triage` (`postmortem.evalset.json`) | `0` | **PASS** | `40.972` | `Tests passed: 1`, `Tests failed: 0` |
| `product` (`product.evalset.json`) | `0` | **PASS** | `114.705` | `Tests passed: 2`, `Tests failed: 0` |
| **Total live Gemini L1 ADK evals** | **`0`** | **PASS (8/8)** | **`214.4`–`243.5`** | **`8` passed, `0` failed across all 4 evalsets** |

### Live `anthropic` (`PROFILE=l1`, `MODEL_PROVIDER=anthropic`, `MODEL=claude-sonnet-5-5`, `AGENT_HARNESS=claude-code`, `LITELLM_LOCAL_MODEL_COST_MAP=True`)

- `ci/evals.sh` (`ROLES=release`): **exit `1` (`NO-GO: evals — release/briefs: 2 eval case(s) failed; log on stderr`)**, total `234.761s` (`82` unit tests in `106.159s` + `adk eval` ~`128.6s` with retries).
- `adk eval` summary: `briefs: Tests passed: 0, Tests failed: 2`.
- Exact API error returned by Anthropic:
  ```text
  litellm.BadRequestError: AnthropicException - {"type":"error","error":{"type":"invalid_request_error","message":"Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing to upgrade or purchase credits."},"request_id":"req_<redacted>"} LiteLLM Retried: 7 times
  ```

### Summary Verdict

- **Gemini L1 live ADK path:** Verified end-to-end (`8/8` cases green across `release`, `ops_triage`, and `product`).
- **Anthropic L1 live ADK path:** Blocked at live API call time by account billing (`Your credit balance is too low to access the Anthropic API`); plumbing and request routing via `LiteLlm` reached Anthropic cleanly and failed honestly without fallback or mutation.
- **OpenAI & Z.ai:** Keys absent (`missing-key`); not executed.
