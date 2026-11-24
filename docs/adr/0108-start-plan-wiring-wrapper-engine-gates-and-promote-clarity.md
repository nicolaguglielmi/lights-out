# ADR 0108 — `.crew/start-plan.env` Auto-Wiring, Explicit `--engine` Opt-In for Wrapper Gates, and Promote-to-Stable Clarity

- Status: proposed
- Date: 2026-11-24
- Milestone: Track 2 — UX Glue (`agy/p2-ux-glue`)
- Amends:
  - [ADR 0103](0103-guided-end-to-end-experience.md) (persists Make-consumable non-secret keys in `.crew/start-plan.env` and wires `Makefile` + `./go` to load them with deterministic exclusions)
  - [ADR 0045](0045-one-command-one-package.md) and [ADR 0105](0105-plane-and-workflow-from-wrapper.md) (keeps default `lights-out init`, `gates`, `test-proof`, `test-integrity` 100% socket-free while adding explicit `--engine` / `LIGHTS_OUT_ENGINE_GATES=1` opt-in for custom-image and `compose:<service>` toolchains)
  - [ADR 0020](0020-adk-agents-and-go-for-launch-at-m7.md) §4 (adds vendor-free stderr post-promotion guidance in `core/promote_hint.py` without changing `core/promote.sh`'s stdout contract or 84-line shell boundary)

## Context

Three friction points remain between guided setup, standalone wrapper PR gates, and the final promotion step:

1. **Setup preferences are not wired into Make.** `./go start` (ADR 0103) writes `.crew/start-plan.env` with setup keys (`START_PROFILE`, `MODEL_PROVIDER`, `AGENT_HARNESS`, `DELIVERY_ADAPTER`, `DECIDE_ADAPTER`, `SUGGESTED_*_BUDGET_USD`), and includes `WORKLOAD_CONTRACT`, yet `Makefile` and `./go` never load `.crew/start-plan.env`. Users who pick a provider, harness, cloud Delivery plugin, or spend budget in `./go start` still have to re-specify `PROFILE=...`, `MODEL_PROVIDER=...`, `AGENT_HARNESS=...`, `DELIVERY=...`, `DECIDE=...`, and `ADAPTERS_PATH=...` on every subsequent command. Meanwhile, writing raw `WORKLOAD_CONTRACT` in `.crew/start-plan.env` risks colliding with Make's computed `WORKLOAD_CONTRACT`.
2. **Custom toolchains (`golang:*`, `node:*`, `compose:<service>`) cannot run through `lights-out gates` without cloning `lights-out`.** By design (ADR 0045, ADR 0105), `lights-out init`, `gates`, `test-proof`, and `test-integrity` never mount the container engine socket by default. When an external Go or Compose-backed repository runs `lights-out gates`, it hits a refusal telling the user only to clone `lights-out` and run `./go gates` or weaken `TOOLCHAIN_IMAGE=workbench`.
3. **`./go promote` says only `[go-for-launch] <version> at 100% of traffic (<delivery>)`.** Operators promoting at L0 or on an attached repository lack an explicit reminder of what shifted (canary weight to 100% vs replacing the stable image/tag, and next steps such as `./go evidence`).

## Decision

### 1. Auto-wiring `.crew/start-plan.env` (`core/start.py`, `Makefile`, `./go`)

1. **Non-secret Make-consumable plan (`core/start.py`)**:
   - Persist clean, non-secret `KEY=VALUE` entries in `.crew/start-plan.env`:
     - `START_PRESET`, `START_MODE`, `START_PROFILE`
     - `MODEL_PROVIDER`, `MODEL` (from `profiles/providers.json` `default_model`, preserving zero model IDs in `core/`), `AGENT_HARNESS`
     - `DELIVERY` (resolved target, e.g. `envoy-canary` or `outside/<cloud>`), `DELIVERY_ADAPTER`, `DELIVERY_TARGET`, `DELIVERY_SOURCE`
     - `DECIDE` and `DECIDE_ADAPTER` (from `profiles/providers.json` `decide`)
     - `ADAPTERS_PATH` (only when `DELIVERY_SOURCE=plugins/cloud`, pointing to resolved `<root>/plugins/cloud`)
     - `AUTO_INCIDENT_ON_ROLLBACK` (`on` by default, or `off` if explicitly set)
     - `SPEND_BUDGET_TICKET_USD` and `SPEND_BUDGET_DAY_USD` when non-empty (validated as positive decimal numbers, alongside existing `SUGGESTED_TICKET_BUDGET_USD` / `SUGGESTED_DAY_BUDGET_USD` for backwards compatibility)
     - `START_WORKLOAD_CONTRACT` (replacing raw `WORKLOAD_CONTRACT` so Make's computed `WORKLOAD_CONTRACT` is never shadowed)
     - `PROVIDER_KEY_VAR`, `PROVIDER_KEY_CONFIGURED`, `DOCTOR_STATUS`
   - **Strict secret refusal**: `core/start.py` refuses to write or read any secret key matching `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, or `METRICS_AUTH_HEADER` in `.crew/start-plan.env`, and validates values against control characters, `$`, `#`, and unsafe whitespace. Writes use atomic temp-file replacement.

2. **Deterministic `Makefile` precedence and exclusions**:
   - Exclude `.crew/start-plan.env` completely whenever any Make goal matches `demo`, `demo-%`, `ci`, `ci-%`, `e2e`, `corpus`, `init`, `try`, `gates`, `test-proof`, `test-integrity`, `mutation-baseline`, or `attached` by exporting `LIGHTS_OUT_NO_START_PLAN := 1`, ensuring recursive `$(MAKE)` calls remain 100% deterministic and repository contract/gate checks never fail on an unproven planned `outside/*` delivery adapter prior to `delivery-conformance`.
   - Outside excluded goals, if `.crew/start-plan.env` exists:
     1. **Secret-key refusal**: fail before inclusion with `$(error ...)` if `.crew/start-plan.env` defines any `*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, or `METRICS_AUTH_HEADER`.
     2. **Profile selection**: when `PROFILE` was not set on the command line or environment (`$(origin PROFILE)` is neither `environment` nor `command line`), honor a valid `START_PROFILE` from `.crew/start-plan.env` before `PROFILE ?= l0` and `include profiles/$(PROFILE).env`.
     3. **Include order**:
        `profiles/$(PROFILE).env` -> `.crew/start-plan.env` -> `.crew/ports.env` -> `.crew/remote.env` -> `.env` -> CLI overrides.
   - Outside-adapter admission (`core/adapters.py admit`) exempts `start`, `next`, and `cloud-setup` alongside `%-conformance` so selecting an unproven cloud plugin in `start` does not block running `cloud-setup`, `next`, `start`, or `delivery-conformance`, while CLI/env `outside/*` overrides remain strictly governed by ADR 0058 on all non-conformance targets.

3. **`./go` host-side alignment**:
   - Do not force `-e PROFILE=l0` into the workbench container when `PROFILE` was unset on the host, so Make can honor `START_PROFILE` from `.crew/start-plan.env` and still default `PROFILE ?= l0` otherwise (`demo`'s background CI container still pins `PROFILE=${PROFILE:-l0}`).
   - Resolve host `ADAPTERS_PATH` across environment -> `.env` -> `.crew/start-plan.env` (when not running excluded `demo|demo-*|ci|ci-*|e2e|corpus|init|try|gates|test-proof|test-integrity|mutation-baseline|attached` commands) -> `profiles/<effective_profile>.env` so external/plugin adapter directories are bind-mounted `:ro` into workbench.

### 2. Explicit `--engine` opt-in for `lights-out` gate commands (`lights-out`, `package/entrypoint.sh`, `ci/package.sh`)

1. **Default remains 100% socket-free**:
   - Plain `lights-out init`, `lights-out gates`, `lights-out test-proof`, and `lights-out test-integrity` continue to run `$gates_image` with NO engine socket mount, NO `DOCKER_HOST`, NO `LIGHTS_OUT_PLATFORM_DIR`, and NO stderr socket notice.
   - When `TOOLCHAIN_IMAGE != workbench` without opt-in, `package/entrypoint.sh` refuses with a one-line `NO-GO` naming the engine socket and all three paths: `lights-out gates --engine`, `./go gates`, and `TOOLCHAIN_IMAGE=workbench`.

2. **Explicit opt-in via `--engine` or `LIGHTS_OUT_ENGINE_GATES=1`**:
   - `--engine` is accepted ONLY on `init`, `gates`, `test-proof`, and `test-integrity` (refused before touching any engine on all other subcommands).
   - When `--engine` or `LIGHTS_OUT_ENGINE_GATES=1` is active on those four subcommands:
     - `lights-out` selects `$full_image` (which provides `docker-compose`), resolves the engine socket using the ADR 0105 `podman`/`docker` pattern (failing cleanly with one stderr line naming `socket` if missing), prints a single opt-in notice line on stderr naming `socket`, mounts `-v "$out:/out"`, `-v "$out/platform:$out/platform"`, `-v "$out/crew:$out/crew"`, and the socket, and passes `LIGHTS_OUT_PLATFORM_DIR`, `LIGHTS_OUT_CREW_DIR`, `LIGHTS_OUT_ENGINE_GATES=1`, and `WORKBENCH_IMAGE`.
     - `package/entrypoint.sh` refuses `--engine` / `LIGHTS_OUT_ENGINE_GATES=1` if running inside `LIGHTS_OUT_FLAVOUR=gates`, stages `$LIGHTS_OUT_PLATFORM_DIR` with `.crew -> $LIGHTS_OUT_CREW_DIR`, clones `/repo` into `$LIGHTS_OUT_PLATFORM_DIR/.gates-attached` (so `compose:<service>` and image build contexts resolve identically on the host engine without colliding with `$plat/.attached`), and executes the standard `init`/`gates`/`test-proof`/`test-integrity` pipeline writing `/out/gates/` and `/out/.lights-out`.
   - The synchronized 29-key `KEY=*` allowlists in `lights-out` and `package/entrypoint.sh` remain untouched and identical; no `--env-file` or `:z`/`:Z` flags are ever used.

### 3. Promote-to-stable clarity (`core/promote_hint.py`, `core/promote.sh`)

- `core/promote_hint.py` is stdlib-only and strictly vendor-free, app-free, and model-free (`ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`).
- `core/promote.sh` invokes `python3 core/promote_hint.py "$version" || :` after `core/releases.py answer GO` in place of its inline final stderr line, keeping `core/promote.sh` at exactly **84 code lines** (`ci/shell-boundary.sh`) and preserving its exact 1-line stdout (`GO: go-for-launch`).
- `core/promote_hint.py` emits `[go-for-launch] <version> at 100% of traffic (<DELIVERY>)` plus concise stderr context distinguishing local edge-proxy promotion (stable slot stays unchanged until the next stable build/publish) from external/cloud rollout promotion, attached-repository rebuild/publish reminders when `WORKLOAD_ATTACHED` is set, and `./go evidence VERSION=<version>`.

## Alternatives rejected

- **Auto-loading `.crew/start-plan.env` unconditionally in `Makefile`:** Breaks deterministic `demo`, `ci`, `e2e`, and `corpus` targets whenever a developer has run `./go start`.
- **Persisting `WORKLOAD_CONTRACT` directly in `.crew/start-plan.env`:** Risks shadowing Make's computed `WORKLOAD_CONTRACT`; `START_WORKLOAD_CONTRACT` records the setup context safely.
- **Mounting the engine socket by default in `lights-out gates`:** Violates ADR 0045 least-privilege PR gate isolation on untrusted pull requests.
- **Expanding `core/promote.sh` in shell past 84 lines:** Violates `ci/shell-boundary.sh`; delegating the stderr hint to `core/promote_hint.py` preserves the exact 84-line debt ceiling.

## Consequences

- Running `./go start` configures subsequent `./go` and `make` workflow targets automatically while keeping `.env` and CLI overrides higher precedence and demos/CI deterministic.
- Standalone `lights-out gates --engine` supports Go, Node, and `compose:<service>` repositories without cloning `lights-out`, while plain `lights-out gates` remains 100% socket-free.
- Operators running `./go promote` or `lights-out promote` receive clear, accurate post-promotion stderr guidance without breaking any machine-parsed stdout contract.

## Golden-rule check

`./go demo` and `./go ci` remain 100% deterministic, key-free, and isolated from `.crew/start-plan.env`. Default `lights-out init` and `lights-out gates` remain 100% socket-free.

## Tests

Written before implementation (RED -> GREEN):

- `core/tests/test_start.py`: updated assertions for `DELIVERY`, `DECIDE`, `MODEL`, `AUTO_INCIDENT_ON_ROLLBACK`, `ADAPTERS_PATH` (cloud plugin only), `SPEND_BUDGET_*` (when non-empty), `START_WORKLOAD_CONTRACT`, and secret-key refusal on `.crew/start-plan.env`.
- `core/tests/test_ux_glue.py` (strictly vendor-free, app-free, and model-free): tests `core/promote_hint.py` stdout/stderr isolation, local-proxy vs external adapter vs attached-repo guidance, `core/promote.sh` 84-line boundary, `Makefile` precedence (`profiles -> start-plan -> ports -> remote -> .env -> CLI`), `START_PROFILE` honoring, secret refusal in `start-plan.env`, `demo`/`ci`/`e2e`/`corpus` exclusion, `start`/`next`/`cloud-setup` admission exemptions, and `--engine` flag/env validation + socket mounting in `lights-out` and `package/entrypoint.sh`.
- `ci/tests/test_ux_glue_e2e.py`: end-to-end tests exercising `./go start` -> `Makefile` auto-wiring with real profiles/cloud plugins, `lights-out gates --engine` against Go/Compose fixtures with fake engine shims, refusal message parity (`./go gates` and `lights-out gates --engine`), `ci/package.sh` behavioral & planted-defect verification, and `core/promote.sh` + `ci/promote.sh` parity.
