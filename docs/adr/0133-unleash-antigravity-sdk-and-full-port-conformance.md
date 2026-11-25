# ADR 0133: Unleash, Antigravity SDK, and 11/11 Port Conformance

- Status: accepted
- Date: 2026-11-25
- Milestone: M91
- Deciders: core maintainers

## Context

`PLAN.md` §4 defines eleven ports across the hexagonal core (`AgentHarness`,
`CodeHost`, `Delivery`, `StateStore`, `EventBus`, `Tracker`, `Registry`,
`Flags`, `Charts`, `Orchestrator`, and `Metrics`). Prior milestones delivered
nine of the eleven conformance suites and most first-party adapters, leaving
three structural gaps before closing §4:

1. **`Flags` Unleash adapter (`adapters/unleash/`)**: Flagd and HTTP-polling
   adapters existed, while Unleash remained listed only as an unimplemented
   §4 target. Feature toggles in `lights-out` must support Unleash Client/Frontend
   read evaluation (including `version`/`appVersion` strategy constraints) and
   Admin API toggling without leaking `UNLEASH_API_TOKEN` on process `argv`.
2. **`AgentHarness` Python SDK adapter (`adapters/antigravity-sdk/`)**: The
   existing `adapters/adk/` showed how an in-process Python SDK adapter runs
   conversational and workspace roles without a container image, whereas
   `adapters/antigravity-sdk/` did not yet exist. Bridging the Antigravity
   SDK (`Agent`, `LocalAgentConfig`, `CapabilitiesConfig`, `BudgetConfig`,
   `@hooks.pre_tool_call_decide`) through `adapters/gemini-cli/hooks/guard.sh`
   while preserving `OUT/run.json` before budget failures (ADR 0087) and
   avoiding container-CLI classification in `ci/harness-shape.sh` / `ci/hooks.sh`
   was required.
3. **11/11 conformance (`Metrics` and `Orchestrator`)**: `core/adapters.py` still
   held `"ORCHESTRATOR"` and `"METRICS"` in `NO_SUITE`, preventing any external
   `outside/*` adapter on those ports from being tried, proven, registered, and
   admitted. Built-in `promql` and `inproc` adapters also lacked executable
   conformance gates verifying their contracts.

## Decision

### 1. Unleash `Flags` adapter (`adapters/unleash/`)

Implement the four standard `Flags` scripts in `adapters/unleash/`:

- `env.sh`: requires HTTP(S) `UNLEASH_URL` (no embedded userinfo) and non-empty
  `UNLEASH_API_TOKEN`, defaults `UNLEASH_PROJECT=default`,
  `UNLEASH_ENVIRONMENT=development`, and `READY_TIMEOUT=60`, and provides
  `unleash_curl` which pipes `Authorization: $UNLEASH_API_TOKEN` exclusively on
  stdin (`curl -H @-`), never on `argv`.
- `ready.sh`: polls `$UNLEASH_URL/api/client/features` (falling back to
  `/health` on HTTP 404) up to `READY_TIMEOUT` seconds, silent on exit 0 and one
  line on stderr when unreachable.
- `evaluate.sh <flag> [version]`: queries
  `$UNLEASH_URL/api/client/features/<flag>` (falling back to `/api/frontend` on
  HTTP 404), evaluates `enabled`, active `strategies`, and `version`/`appVersion`
  constraints (`IN`, `STR_IN`, `NOT_IN`, `STR_EQ`, `STR_CONTAINS`,
  `STR_STARTS_WITH`, `STR_ENDS_WITH`, `inverted`, `caseInsensitive`), prints
  `true` or `false`, and exits 1 with empty stdout on errors.
- `set.sh <flag> <variant>`: maps `on`/`off` (`true`/`false`), refuses invalid
  variants before issuing network calls, posts to
  `/api/admin/projects/<project>/features/<flag>/environments/<env>/{on|off}`,
  and verifies the resulting state with `evaluate.sh` so all 6 cases of
  `gates/flags-conformance.sh` pass.

### 2. Antigravity SDK `AgentHarness` adapter (`adapters/antigravity-sdk/`)

Implement `adapters/antigravity-sdk/` following the non-container Python adapter
shape (`adapters/adk/`):

- Ship `run.sh`, `thinking.env` (`GEMINI_LOW=low`, `GEMINI_MEDIUM=medium`,
  `GEMINI_HIGH=high`), and package `lights_out_agy_sdk/{__init__.py,run.py}`,
  with zero `Containerfile`, `package.json`, `package-lock.json`, or
  `release.env` files so `ci/harness-shape.sh` and `ci/hooks.sh` never
  misclassify it as a CLI container harness.
- Configure `Agent`, `LocalAgentConfig`, `types.CapabilitiesConfig` (with
  disallowed built-ins matching the harness perimeter and sandboxed command
  execution), `types.BudgetConfig`, and `@hooks.pre_tool_call_decide`.
- Bridge every SDK tool call (`run_command`, file read/write/edit tools, and
  built-in/MCP tools) through `adapters/gemini-cli/hooks/guard.sh` so
  `OUT/hooks.log` records uniform `ALLOW`/`DENY` entries across harnesses.
- Refuse loudly in one stderr line naming missing package `google-antigravity`
  whenever it is absent and `AGY_SDK_STUB=1` (allowed only when
  `CONFORMANCE=1`) is not active—zero silent hidden mocks.
- Write `OUT/stop` and `OUT/run.json` BEFORE raising budget failures on
  `TOKEN_BUDGET` or `MAX_TURNS` (ADR 0087), plus role deliverables
  (`OUT/patch`, `OUT/pr.json`, `OUT/final.md` for coding; `OUT/final.md` for
  review; `OUT/calls.json` for conversational roles) and secret-leak checks.

### 3. `Metrics` and `Orchestrator` conformance suites & 11/11 admission

- Add thin gate wrappers `gates/metrics-conformance.sh` and
  `gates/orchestrator-conformance.sh` (<= 25 non-blank non-comment lines, cold
  NO-GO unless `CONFORMANCE=1`) delegating to `core/gates/metrics.py` and
  `core/gates/orchestrator.py`.
- `core/gates/metrics.py` verifies 5 cases: optional `ready.sh` (silent exit 0;
  skipped for `promql`-style adapters lacking `ready.sh`), valid query vector
  shape `[timestamp, numeric_value]`, empty-result query `data.result == []`,
  empty/malformed query refusal (nonzero with empty stdout), and unreachable
  `METRICS_URL` refusal.
- `core/gates/orchestrator.py` verifies 5 cases: optional `ready.sh` (skipped
  for `inproc`-style adapters lacking `ready.sh`), idle empty-queue drain,
  real `EVENT_BUS` + `STATE_STORE` step dispatch transitioning a ticket from
  `opened` to `held` via `core/tower.sh` + `TOWER_STEPS`, failing-step nonzero
  exit with queue acknowledgment so the bus drains cleanly, and refusal on
  missing/unknown `EVENT_BUS` or unreadable `STATE_STORE`.
- Add cold outside fixtures `fixtures/outside-adapters/file-metrics/` and
  `fixtures/outside-adapters/file-orchestrator/`.
- Move `"ORCHESTRATOR": "orchestrator-conformance"` and
  `"METRICS": "metrics-conformance"` into `SUITES` in `core/adapters.py` and set
  `NO_SUITE = ()`, yielding 11/11 port conformance.
- Update `ci/outside.sh` to verify trial, suite GO, register admission, byte
  tamper refusal, and symlink refusal for both `METRICS=outside/file-metrics`
  and `ORCHESTRATOR=outside/file-orchestrator`, plus bad outside names and
  unknown port refusal. Keep vendor names (`unleash`, `github`, etc.) out of
  `core/` by placing Unleash/SDK integration proofs in
  `ci/tests/test_unleash_and_agy_sdk.py`.

## Provenance

| Numeric Value / Cap | Kind | Source / Justification |
|---|---|---|
| `READY_TIMEOUT=60` (`adapters/unleash/env.sh`) | decision | Matches existing `Flags`/`CodeHost` polling readiness budget across `adapters/` (ADR 0044, ADR 0107) |
| Connect timeout `2`s, max request time `5`s (`unleash_curl`) | decision | Upper bound matching `adapters/github/env.sh` and `adapters/flags-http` fast-fail budgets inside conformance suites |
| `TIMEOUT_S=900` (`adapters/antigravity-sdk/run.sh`) | decision | Matches `adapters/adk/run.sh` and `adapters/gemini-cli/run.sh` role execution ceiling |
| Conformance wrapper line limit `25` (`<= 30` gate limit) | measurement | Enforced by `ci/shell-boundary.sh` gate wrapper ceiling (`30` lines) |
| `11` entries in `adapters.SUITES`, `0` in `adapters.NO_SUITE` | external | Exact port count enumerated in `PLAN.md` §4 (`AgentHarness`..`Metrics`) |
| `5` cases in `metrics-conformance` & `orchestrator-conformance` | decision | Covers ready, valid happy path, empty/noop path, invalid input refusal, and missing/unreachable dependency refusal |
| `6` cases in `flags-conformance` | external | Existing contract count verified by `core/gates/flags.py` |
| `MAX_QUERIES=10`, `PATHS_LIMIT=40` (`find_files` tool) | decision | Matches existing `adapters/adk/` read-only project map tool bounds to prevent oversized prompt payloads |
| Conformance and stub opt-in flags (`CONFORMANCE=1`, `AGY_SDK_STUB=1`), error exit (`1`), success exit (`0`) | decision | Binary `1` opt-in guards keep `ci/contract.sh` cold and offline while gating conformance suites and synthetic test stubs; exit `0`/`1` match `core/gates` (`ADR 0058`, `ADR 0087`) |

## Alternatives

- **Requiring `ready.sh` unconditionally in `Metrics` and `Orchestrator` suites**:
  Would break built-in `adapters/promql/` (which ships `query.sh` only) and
  `adapters/inproc/` (which ships `run.sh` only) or force scope creep into
  unowned existing adapter directories. Treating `ready.sh` as optional when
  absent while strictly enforcing exit 0 + empty stdout when present matches
  `adapters/prometheus/`, `adapters/managed-prometheus/`, `adapters/cloud-workflows/`,
  and both outside fixtures cleanly.
- **Hidden in-memory fallback when `google-antigravity` is uninstalled**:
  Explicitly rejected; silent fallbacks violate cold failure visibility. Missing
  package fails in one stderr line naming `google-antigravity`, while synthetic
  `CONFORMANCE=1` tests require an explicit `AGY_SDK_STUB=1` flag and external
  test module on `PYTHONPATH`.
- **Implementing Unleash in Python or passing token via curl CLI arguments**:
  Using POSIX `sh` + `curl -H @-` + `jq` matches every sibling `adapters/flags-*`
  adapter and guarantees `UNLEASH_API_TOKEN` never appears in process listings.

## Consequences

- `core/adapters.py` now reaches 11/11 `SUITES` with `NO_SUITE = ()`, allowing
  outside implementations for every port in `PLAN.md` §4 to follow the unified
  trial, conformance, hash-pinned register, and admission flow.
- `core/` remains 100% vendor-free (`ci/vendor-free.sh` green), while
  `ci/tests/test_unleash_and_agy_sdk.py` exercises real HTTP Unleash flows and
  SDK hook/budget contracts.
- `adapters/antigravity-sdk` runs cleanly on host/container environments without
  Docker/Podman build steps when `google-antigravity` is installed on
  `PYTHONPATH`, and fails explicitly when absent.
