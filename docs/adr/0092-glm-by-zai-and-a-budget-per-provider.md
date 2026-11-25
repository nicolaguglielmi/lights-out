# ADR 0092 — GLM by Z.ai is the fourth provider (`MODEL_PROVIDER=zai`, on `claude-code` and the ADK roles), `TOKEN_BUDGET` can differ per provider, and the ledger prices cached input at the vendor's cached rate

- Status: accepted (M59)
- Date: 2026-11-16
- Amends: ADR 0040 §1, §2, §5 (a fourth provider, its key and its thinking
  map; `glm-` ids join `ci/model-free.sh`), ADR 0041 §2 (a harness may run
  more than one provider, one conformance file per provider), ADR 0083 §1,
  §2 and "The gap between the cost and the bill" (a cached-input price and
  the basis of each cost), ADR 0087 §4 (the 80% warning follows the
  provider's budget).

## Context

The owner wants GLM, by Z.ai, as a provider beside Gemini, Claude and
OpenAI. M24 (docs/plans/m24-phase-c-handoff.md,
`.claude/context/handoffs/claude-m24-phase-d-gemini-1628f2.md`) left two
findings on the same seam: one `TOKEN_BUDGET` for every provider stopped
three Gemini tickets of ten, and the ledger prices every input token as
uncached ($20.85 for the Gemini leg's dev-worker runs, $3.55 at the cached
price). The plan's rule holds: verify before writing, and stop on what is
not as described.

## Verified (2026-11-16, this session)

- **Z.ai's API.** OpenAI-compatible Chat Completions at
  `https://api.z.ai/api/paas/v4/chat/completions`, `Authorization: Bearer`
  (docs.z.ai/guides/overview/quick-start,
  docs.z.ai/api-reference/llm/chat-completion). Anthropic Messages at
  `https://api.z.ai/api/anthropic`, the URL Z.ai documents for Claude Code
  with the key in `ANTHROPIC_AUTH_TOKEN`
  (docs.z.ai/scenario-example/develop-tools/claude). That URL is listed
  under the GLM Coding Plan (docs.z.ai/devpack/quick-start); since
  2026-10-16 Standard (pay-as-you-go) keys and plan keys are separate, and
  "across supported endpoints, Standard keys use standard API billing";
  a Standard key replacing a plan key "can keep the same endpoint"
  (docs.z.ai/devpack/notice/api-key-management). **Not as expected:** no
  page says in one sentence that a Standard key is accepted at
  `/api/anthropic`. The live check (§8) is the proof; until it runs, the
  README says so.
- **Model, price, context.** `glm-5.3`: 1M-token context, 128K output,
  tool calling, `thinking` always enabled, `reasoning_effort` in
  `low|high|max` only, default `max` (docs.z.ai/guides/llm/glm-5.3 and the
  chat-completion reference: "GLM-5.3 and GLM-5.3-FLASH accept only low,
  high and max"). Price, USD per million tokens: input 1.40, cached input
  0.26, output 4.40; cached-input storage "limited-time free"
  (docs.z.ai/guides/overview/pricing; the page carries no date). Usage
  reports `prompt_tokens_details.cached_tokens`.
- **LiteLLM 1.106.6** (the pin, ADR 0040 §6), read in the workbench image:
  provider `zai` (`litellm/llms/zai/chat/transformation.py`), key
  `ZAI_API_KEY`, base `https://api.z.ai/api/paas/v4`; `zai/glm-5.3` in its
  cost map with `supports_reasoning`. **Not as expected:**
  `reasoning_effort` is not among its supported parameters for `zai`:
  `litellm.completion(model="zai/glm-5.3", reasoning_effort="high")`
  raises `UnsupportedParamsError`. `extra_body={"reasoning_effort": "max"}`
  reaches the request as a top-level `reasoning_effort` (checked against a
  local listener: `POST /chat/completions`, `model glm-5.3`,
  `reasoning_effort max`, tools kept, bearer key). google-adk 2.9.1's
  `LiteLlm` maps `prompt_tokens_details.cached_tokens` to
  `usage_metadata.cached_content_token_count` (lite_llm.py ~996–1038, 2486).
- **Claude Code 2.1.311** (the pin) against a local listener, `--model
  glm-5.3 --effort low|medium|high`, `ANTHROPIC_AUTH_TOKEN` set: `POST
  /v1/messages?beta=true`, `model glm-5.3`, `Authorization` set (no
  `x-api-key`), `thinking {type: adaptive}` and `output_config.effort` the
  level given. **Not as expected:** Z.ai documents no `output_config` on
  its Anthropic endpoint, so whether GLM honours the effort there is
  unknown; what the conformance suite proves is what leaves the harness.
- **Codex 0.166.0** speaks the Responses API only (`wire_api=responses`,
  `adapters/codex/entrypoint.sh`). Z.ai names a Responses base,
  `https://api.z.ai/api/v1`, in one table of the key guide and has no
  reference for it: no tool types, no `reasoning`. **Not as expected:**
  codex is not wired for Z.ai (§3).
- **Gemini, Anthropic, OpenAI cached prices** (each read 2026-11-16):
  gemini-3.8-flash context caching $0.075 until 2026-12-31, $0.15 from
  2027-01-01 (aistudio.google.com/docs/pricing); Claude Sonnet 5.5 "cache
  hits and refreshes" $0.10, 0.05x of input; 5-minute cache writes $2.50
  (platform.claude.com/docs/en/about-claude/pricing); gpt-6.1-sol cached
  input $0.10, short context (developers.openai.com/api/docs/pricing).

## Decision

1. **`MODEL_PROVIDER=zai`**, the vendor's own name for its platform and
   LiteLLM's provider prefix, so the ADK path needs no translation table.
   Not `glm` (a model family, not who bills) and not `zhipu` (the company,
   not the platform the key comes from). The key is `ZAI_API_KEY`,
   LiteLLM's name. The default model is `glm-5.3`, Z.ai's current flagship
   (decision: the one model on the pricing page's "Latest Models" with
   tool calling and the full context; `glm-5.3-flash` is the cheaper
   choice a user can set in `MODEL`). Profiles keep Gemini; `.env.example`
   gains `ZAI_API_KEY=replace-me` and the commented pair `MODEL_PROVIDER=zai`
   / `MODEL=glm-5.3`. `ci/model-free.sh` adds `glm-[0-9]` to its pattern.
2. **A harness may run more than one provider** (amends ADR 0041 §2). A
   harness's `conformance.env` stays its first provider; each further one
   is `adapters/<harness>/conformance.<provider>.env`, the same five keys
   plus an optional sixth, `BASE_URL`, the vendor endpoint the harness is
   pointed at for that provider. `run.sh` takes the file whose `PROVIDER`
   is `MODEL_PROVIDER` and refuses any other provider, naming the harness
   that fits (discovered over every conformance file, never listed). Its
   `EGRESS` is that file's; a `BASE_URL` must be `https://<EGRESS host>/…`
   (`ci/harness-shape.sh`). `gates/harness-conformance.sh` runs its eleven
   cases once per (harness, conformance file): `CONFORMANCE_HARNESS=claude-code`
   runs claude-code on anthropic, then on zai. `core/doctor.sh` and case 0
   read every conformance file.
3. **Which harness runs GLM.**
   - `claude-code`: `conformance.zai.env` with `PROVIDER=zai`,
     `MODEL=glm-5.3`, `KEY_VAR=ZAI_API_KEY`,
     `THINKING_PATH=.output_config.effort`, `EGRESS=api.z.ai:443`,
     `BASE_URL=https://api.z.ai/api/anthropic`. On zai, `run.sh` gives the
     container `ANTHROPIC_BASE_URL` (the file's, or `MODEL_BASE_URL` in
     conformance), `ANTHROPIC_AUTH_TOKEN` from `ZAI_API_KEY` by name, and
     `ANTHROPIC_API_KEY` empty, so an Anthropic key in `.env` never travels
     to Z.ai. The artefact check refuses on `ZAI_API_KEY`.
   - ADK roles: `zai` → `LiteLlm(model="zai/<MODEL>", api_key=<key>,
     extra_body={"reasoning_effort": <map>})`, no planner (ADR 0040 §3's
     rule: exactly one reasoning setting per request).
   - `codex`, `gemini-cli`, `antigravity-cli`: refuse `zai` in their one
     line, naming `claude-code`. Codex on Z.ai waits for a documented
     Responses endpoint and its own conformance file.
4. **The thinking map.** `ZAI_LOW=low`, `ZAI_MEDIUM=high`, `ZAI_HIGH=max`
   in `adapters/claude-code/thinking.env` and `adapters/adk/thinking.env`
   (decision: GLM-5.3 accepts three levels, `low|high|max`; the plan's
   three map onto them one to one and every value is one Claude Code's
   `--effort` also accepts, so no level is rewritten on either side and
   `medium`, which GLM-5.3 does not list, is never sent).
5. **Prices.** `profiles/prices.json` gains `zai`/`glm-5.3` (1.40, 0.26,
   4.40, source docs.z.ai/guides/overview/pricing, checked 2026-11-16),
   and every entry may carry `cached_input`, USD per million tokens of
   input served from the vendor's cache: added to the four existing rows
   from the pages above.
6. **Cached input in the run and the ledger** (amends ADR 0083 §2).
   `run.json`'s `tokens` gains `cached`, the part of `input` the vendor
   served from its cache, where the harness reports it: `gemini-cli`
   (`stats.cached`), `claude-code` (`usage.cache_read_input_tokens`; cache
   writes stay in `input` at the input price), `antigravity-cli` (the sum
   of `cache_read_tokens`) and the ADK roles (the sum of
   `cached_content_token_count`). `codex` does not report it yet: no key,
   not a 0. Found by the suite's cached part (Tests): agy 1.5.1 leaves
   cache reads out of `input_tokens` and `total_tokens`, so its runs and
   its budget hook undercounted every cached Gemini token; both now add
   `cache_read_tokens` back. The cost of a run is then
   `(input − cached) × input + cached × cached_input + output × output`,
   basis `cached`, when `tokens.cached` is a whole number from 0 to
   `input` and the price has `cached_input`; otherwise ADR 0083's formula,
   basis `uncached`. Every ledger entry gains `cost_basis`: `cached`,
   `uncached`, or null when the cost is unknown; a network's run is
   `cached` only when every member's is. M24's scoreboard dataset
   (`docs/scoreboard/m24-tenacity.json`) is sourced data and is not
   re-priced.
7. **A budget per provider** (amends ADR 0087 §4). A role's `role.env`
   may set `TOKEN_BUDGET_<PROVIDER>` (upper case) beside `TOKEN_BUDGET`,
   the default. `core/crew.sh`, `core/steps/review.sh` and the logbook
   (the run in flight and the role matrix) take the profile's provider's
   key when the role sets it, else the default; the 80% warning is 80% of
   that number. One number is set today:
   - dev-worker `TOKEN_BUDGET_GEMINI=3900000`. Provenance, measurement:
     M24's Gemini leg resent about 45k tokens per call and took 32 to 57
     calls a ticket; the three stopped tickets reached 2,600,000 at 41 to
     57 calls, the heaviest tenacity-10 at 41 (2,600,000 / 41 = 63,415 a
     call). At that cost the role's own `MAX_TURNS=60` is 3,804,878,
     rounded up to the next 100,000: on Gemini the turn cap, not the token
     budget, becomes the stop. At the cached price (93 % of input cached
     in M24) an attempt at the budget costs about $0.48.
   - The default stays 2,600,000 (M9, measured on Gemini). M24's Sonnet
     runs took 4 to 13 turns at about 19k a request (tenacity-09: 10
     requests, 187,194 input), so it does not bind there; GLM has no
     measurement and runs under the default until the live check gives one.
8. **The live check** is one ticket on GLM, prepared and estimated in the
   handoff, run only on the owner's approval after the owner has put
   `ZAI_API_KEY` in `.env`. L0 and `./go ci` need no Z.ai key.

## Tests, from this ADR

- `core/tests/test_spend.py`, on a table of the test's own: `model-c` at
  input 1.00, cached 0.10, output 5.00. 2,000,000 in of which 1,500,000
  cached, 100,000 out: $1.15, basis `cached`; with no `cached`: $2.50,
  `uncached`; `cached` above `input`, negative or not a whole number:
  `uncached`; the same run at `model-a` (no `cached_input`): $2.50,
  `uncached`. A network with one cached member and one uncached: the sum,
  basis `uncached`. Every ledger entry carries `cost_basis`; an unknown
  cost carries null.
- M24's numbers, on the committed table: the ten Gemini dev-worker runs of
  the phase D handoff's table (input, cached, output), recorded on
  2026-11-14: $3.550083 in all, basis `cached`; the same runs without
  `cached`: $20.847787.
- The committed table: every `cached_input`, where present, is a number of
  0 or more and at most the entry's `input`; `zai`/`glm-5.3` is there with
  its source and date.
- The budget: `role.env` with `TOKEN_BUDGET=100` and
  `TOKEN_BUDGET_GEMINI=200`: provider `gemini` gets 200, `anthropic` and
  an empty provider 100; the logbook's run in flight at 160 tokens on
  `gemini` is 80% of 200 and warns, at 159 it does not; on `anthropic` 80
  warns. `core/crew.sh` and `core/steps/review.sh` pass the provider's
  number to `run.sh`.
- ADK: `zai` builds `LiteLlm` with model `zai/<MODEL>`, the key from
  `ZAI_API_KEY`, `extra_body.reasoning_effort` the map's value per level,
  no `reasoning_effort` keyword and no planner; litellm 1.106.6 turns it
  into a top-level `reasoning_effort` in the outgoing request;
  `run.json`'s `tokens.cached` sums `cached_content_token_count`.
- The harnesses: `claude-code` on `zai` passes the eleven conformance
  cases against the scripted model (`./go harness-conformance
  CONFORMANCE_HARNESS=claude-code`), with `THINKING_PATH`'s value the
  map's; the other CLI harnesses refuse `zai` naming `claude-code` (case
  0); `ci/harness-shape.sh` refuses a provider file whose `BASE_URL` host
  is not its `EGRESS` host, a provider outside the four, or a key not in
  `.env.example`. `gemini-cli` and `claude-code` write `tokens.cached`
  from their transcripts (`ci/hooks.sh`-style fixtures).

## Alternatives rejected

- **Z.ai through codex's Chat Completions.** Codex 0.166.0 has no `chat`
  wire; a second codex pin for one provider is a second harness.
- **Z.ai's Coding Plan endpoint as the default.** A subscription, not a
  per-token price the ledger can use; a plan key works at the same URL.
- **`drop_params=True` on LiteLLM.** It would drop the effort silently:
  the lever would look wired and not be (ADR 0018's bug).
- **A budget table in `profiles/`.** The budget is a role's measured
  number; a second file would split one role's limits in two places.
- **Pricing cache writes at 1.25x.** Claude Code's `input` sums writes and
  reads; the writes are priced as input, an understatement the ADR names
  rather than a fourth price column.
- **`MODEL_PROVIDER=glm`.** The provider is who serves and bills; GLM is
  also served by other platforms (LiteLLM's table lists more than ten).

## Consequences

- **The one that hurts:** the Anthropic-endpoint path for GLM rests on a
  key guide sentence and on Claude Code sending `output_config.effort` to
  an endpoint that does not document it. Conformance proves the plumbing;
  only the live check proves Z.ai accepts it, and nothing yet proves the
  effort changes GLM's thinking.
- Every live Gemini run costs about a sixth of what the ledger said: the
  console's spend panel, `./go spend` and the M50 budget see the lower,
  truer number. Runs already in a ledger keep their price.
- A Gemini dev-worker attempt may now spend 3.9M tokens before the stop
  instead of 2.6M: about $0.10 more at the cached price, $0.98 more
  uncached.
- The claude-code harness runs the conformance suite twice: about twice
  its time in `./go harness-conformance`.

## Golden-rule check

At L0 nothing here is read: `MODEL_PROVIDER` is empty, replay costs 0,
no Z.ai key is asked for, and `./go demo` replays as before.
