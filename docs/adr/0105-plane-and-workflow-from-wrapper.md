# ADR 0105 — Plane and full E2E workflow from the single `lights-out` wrapper

- Status: accepted (M67)
- Date: 2026-11-24
- Milestone: M67
- Amends: [ADR 0045](0045-one-command-one-package.md) (`lights-out` remains socket-free for read-only/gates and state-only commands while adding opt-in engine-socket mounting and host-mirrored `$out/platform` staging for plane and full E2E workflow commands)

## Context

ADR 0045 shipped the standalone POSIX wrapper `lights-out` and `package/entrypoint.sh` for eight read-only/gates/triage commands (`init`, `gates`, `test-proof`, `test-integrity`, `triage`, `postmortem`, `doctor`, `version`) with zero container engine socket access. Everything involving the L0 Compose plane, ticket workflow, guided setup, metrics, and release console required cloning `lights-out` and running `./go`.

M65 (ADR 0098), M66 (ADR 0100), M69 (ADR 0099), M44 (ADR 0104), and guided UX (ADR 0103) turned `lights-out` into a single-install package (`uv tool install lights-out-gates`) that can draft both gates and plane halves of `.lights-out/` for arbitrary repositories. Users installing the wrapper still lack a way to run the full end-to-end delivery lifecycle (`start`, `next`, `intake`, `answer`, `intake-confirm`, `assign`, `up`, `down`, `smoke`, `release`, `promote`, `console`, `dora`, `spend`, `evidence`, `cloud-setup`) from their own repository without cloning `lights-out`.

Two constraints govern how the wrapper runs those commands inside `ghcr.io/nicolaguglielmi/lights-out:<version>`:

1. **Least-privilege socket boundary.** Read-only, PR-gate, role-replay, and state-only commands must never receive the container engine socket or `DOCKER_HOST`. Only explicit, opt-in plane and workflow commands that drive `docker-compose`, sibling worker/probe containers, or engine image inspection may mount the engine socket, and the wrapper must print a one-line notice on stderr when doing so.
2. **Compose bind-mount path resolution.** `compose.yaml` mounts `./roles`, `./core`, `./.crew`, `./${WORKLOAD_CONTRACT}/flags`, and `./gates/harness-conformance` relative to the platform root, and builds `backend` from `${WORKLOAD}`. Because `docker-compose` inside the wrapper container talks to the **host** container engine over the mounted socket, relative bind-mount sources resolve on the **host** filesystem, not inside `/opt/lights-out` in the container. Staging the platform tree at `$out/platform` mounted identically at `$out/platform:$out/platform` (with `$out/crew` mounted at `$out/crew:$out/crew`) makes every Compose bind mount and build context resolve identically inside the container and on the host engine, while keeping the user's repository mounted read-only at `/repo:ro`.

## Decision

### 1. Three command tiers in `lights-out` and `package/entrypoint.sh`

`lights-out` expands from 8 to 24 subcommands (`--version` continues to alias `version`, and `help` / `-h` / `--help` print usage):

| Tier | Commands | Image | Engine socket / `DOCKER_HOST` | Host mounts |
|---|---|---|---|---|
| Host-only | `doctor`, `version`, `--version` | none (`doctor` inspects `$gates_image`) | Never | None |
| Read-only & gates | `init`, `gates`, `test-proof`, `test-integrity`, `triage`, `postmortem` | `$gates_image` (4 gates/init) / `$full_image` (triage, postmortem) | Never | `-v "$here:/repo:ro"`, `-v "$out:/out"` |
| State-only | `next`, `dora`, `spend`, `cloud-setup` | `$full_image` | Never | `-v "$here:/repo:ro"`, `-v "$out/crew:/opt/lights-out/.crew"` (plus `ADAPTERS_PATH` `:ro` if set) |
| Opt-in plane & workflow | `start`, `intake`, `answer`, `intake-confirm`, `assign`, `up`, `down`, `smoke`, `release`, `promote`, `console`, `evidence` | `$full_image` | Mounted (opt-in) + 1-line stderr notice | `-v "$here:/repo:ro"`, `-v "$out/platform:$out/platform"`, `-v "$out/crew:$out/crew"`, socket, (plus `ADAPTERS_PATH` `:ro` if set) |

The gates image (`LIGHTS_OUT_FLAVOUR=gates`) continues to refuse all full-image commands (`triage`, `postmortem`, all 4 state-only commands, and all 12 plane/workflow commands) with a one-line `NO-GO` naming `ghcr.io/nicolaguglielmi/lights-out:<version>` and `LIGHTS_OUT_IMAGE`.

### 2. Socket resolution and opt-in notice

For opt-in plane/workflow commands only, `lights-out` reuses `./go`'s socket resolution pattern:

- When the engine is `podman`: query `"$engine" info --format '{{.Host.RemoteSocket.Path}}'` (stripping `unix://`) and `"$engine" info --format '{{.Host.RemoteSocket.Exists}}'`. If `Exists != true` or the path is empty, exit 1 with a single stderr line naming the missing `socket`.
- Otherwise (`docker` or compatible): read `${DOCKER_HOST:-}` (stripping `unix://`), defaulting to `/var/run/docker.sock`. Require `[ -S "$sock" ]`, else exit 1 with a single stderr line naming the missing `socket`. Under `docker`, also carry `--group-add <gid>` for the socket's group and `--add-host host.containers.internal:host-gateway` (matching `./go`).
- Mount `-v "$sock:/var/run/docker.sock"`, pass `-e DOCKER_HOST=unix:///var/run/docker.sock`, keep `--security-opt label=disable` (never `:z` or `:Z`), and print a single notice line on stderr explaining the opt-in engine socket mount.

### 3. Host-mirrored `$out/platform` staging in `package/entrypoint.sh`

For opt-in plane/workflow commands, `lights-out` creates `$out/platform` and `$out/crew` on the host, mounts both at their exact host paths (`-v "$out/platform:$out/platform"` and `-v "$out/crew:$out/crew"`), and passes `-e LIGHTS_OUT_PLATFORM_DIR="$out/platform"`, `-e LIGHTS_OUT_CREW_DIR="$out/crew"`, and `-e WORKBENCH_IMAGE="$image"`.

Inside `package/entrypoint.sh`, when `LIGHTS_OUT_PLATFORM_DIR` is set:

1. Check `$LIGHTS_OUT_PLATFORM_DIR/.platform-revision` against the image revision tuple (`$LIGHTS_OUT_VERSION:$LIGHTS_OUT_REVISION:$LIGHTS_OUT_FLAVOUR`). Whenever it differs or is absent, clean old staged platform entries (preserving `.crew` and `.attached`) and copy `/opt/lights-out` into `$LIGHTS_OUT_PLATFORM_DIR` excluding `.crew`, `.attached`, and `.gates-out`, then write `.platform-revision`.
2. Link `$LIGHTS_OUT_PLATFORM_DIR/.crew -> $LIGHTS_OUT_CREW_DIR` so all state, keys, logs, and sqlite data persist in `$out/crew` while Compose paths `./.crew/...` resolve identically on the host engine.
3. `cd "$LIGHTS_OUT_PLATFORM_DIR"`.
4. Attach `/repo` via `core/attach.sh /repo "$LIGHTS_OUT_PLATFORM_DIR/.attached"` (skipped for stateless `cloud-setup`, and tolerated for `down` or snapshot `console` when `/repo` has no commit or contract so teardown/snapshot serving still works).
5. Dispatch `make --no-print-directory <target> ...`, preserving stdout (1-line `GO:`/`NO-GO:` contract or `--json`) and normalizing any non-zero `make` failure (such as GNU `make`'s exit `2`) to exit `1`.

### 4. `console`, `start`/`next` flags, allowlist synchronization, and secrets

- **`console`**: publishes `-p ${CONSOLE_PUBLISH:-127.0.0.1}:${CONSOLE_PORT:-8098}:8098`, passes `CONSOLE_PORT`, `CONSOLE_BIND=0.0.0.0`, `CONSOLE_PUBLISH`, and forwards non-empty `CONSOLE_AUTH`, `CONSOLE_IDENTITY_HEADER`, `CONSOLE_LAUNCHERS`, `CONSOLE_SNAPSHOT`, `CONSOLE_HOSTS`, `CONSOLE_POLL_S` by name (`-e VAR`).
- **`start` and `next`**: accept `start`/`next` CLI flags (`--json`, `--mode`, and for `start`: `--non-interactive`, `--preset`, `--provider`, `--harness`, `--delivery`, `--profile`, `--ticket-budget-usd`, `--day-budget-usd`) as well as `KEY=VALUE` overrides (`PRESET=`, `PROVIDER=`, `MODE=`, `SPEND_BUDGET_TICKET_USD=`, `SPEND_BUDGET_DAY_USD=`, etc.), forwarding CLI flags via `LIGHTS_OUT_CLI_ARGS` (`\037`-separated, matching `./go` and `core/start.sh`). For `start`, `lights-out` also computes host doctor state into `LIGHTS_OUT_DOCTOR_RC`, `LIGHTS_OUT_DOCTOR_OUT`, and `LIGHTS_OUT_DOCTOR_ERR` so `core/start.py` sees real host engine/image status.
- **Identical `KEY=*` allowlist**: `lights-out` and `package/entrypoint.sh` keep the exact same 29 `KEY=*` case arms (`BASE`, `HEAD`, `JUDGED`, `STAGED`, `ALERT`, `INCIDENT`, `PROFILE`, `MODEL_PROVIDER`, `MODEL`, `TICKET`, `TEXT`, `ANSWER`, `DECISIONS`, `VERSION`, `CANARY_VERSION`, `CANARY_TAG`, `GO_FOR_LAUNCH`, `REASON`, `MODE`, `PRESET`, `CLOUD`, `DELIVERY`, `CODE_HOST`, `AGENT_HARNESS`, `PROVIDER`, `ADAPTERS_PATH`, `WINDOW_DAYS`, `SPEND_BUDGET_TICKET_USD`, `SPEND_BUDGET_DAY_USD`). Unknown subcommands, invalid flags, and unknown keys are refused before touching any container engine.
- **Secrets and `ADAPTERS_PATH`**: Allowed non-empty secret env vars (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ZAI_API_KEY`, `GITHUB_TOKEN`, `GITLAB_TOKEN`, `METRICS_AUTH_HEADER`) are forwarded strictly by name (`-e VAR`), never by value and never via `--env-file`. When `ADAPTERS_PATH` is set (env or arg) on state-only or plane commands, it must be an existing absolute directory and is bind-mounted read-only (`-v "$ADAPTERS_PATH:$ADAPTERS_PATH:ro"`, never `:z` or `:Z`).

### 5. Behavioral engine-boundary verification in `ci/package.sh`

`ci/package.sh` retains its static bans on `--env-file` and `:z`/`:Z`, removes the obsolete blanket static grep against `.sock`/`DOCKER_HOST`, and replaces it with behavioral fake-engine checks plus planted-defect self-tests proving:

- Read-only/gates and state-only commands never pass any socket mount, `DOCKER_HOST`, or `LIGHTS_OUT_PLATFORM_DIR`.
- Opt-in plane/workflow commands do pass the socket mount, `DOCKER_HOST`, `LIGHTS_OUT_PLATFORM_DIR`, `LIGHTS_OUT_CREW_DIR`, `--security-opt label=disable`, and the 1-line stderr opt-in notice (and fail cleanly with 1 stderr line naming `socket` when the socket is missing).
- Every one of the 24 subcommands dispatches as expected, and unknown subcommands, disallowed flags, and unknown `KEY=VALUE` arguments refuse before touching any engine.

## Alternatives rejected

- **Mount the container engine socket for all `lights-out` commands (including `gates` and `init`).** Violates ADR 0045 principle 1: PR gates and CI runs on untrusted pull requests must never receive control of the host engine daemon.
- **Pass `--env-file .env` or `:z`/`:Z` relabeling.** `--env-file` risks leaking unrelated host environment values; `:z`/`:Z` relabels user trees on SELinux hosts and fails on rootless Podman Machine mounts (ADR 0045 §2).
- **Keep `/opt/lights-out` as the Compose working directory without `$out/platform` mirroring.** Inside the container `/opt/lights-out` does not exist on the host filesystem, so `docker-compose` talking to the host daemon would fail every `./roles`, `./core`, `./.crew`, and `./${WORKLOAD_CONTRACT}/flags` bind mount.
- **Require users to clone `lights-out` for plane/workflow commands.** Defeats the single-install `lights-out` wrapper experience (`uv tool install lights-out-gates`, ADR 0099) once M65/M66 enabled plug-and-play plane contracts.

## Consequences

- Any repository with a committed `.lights-out/` contract can run the entire E2E workflow (`init`, `gates`, `start`, `next`, `up`, `intake`, `answer`, `intake-confirm`, `assign`, `smoke`, `release`, `promote`, `console`, `dora`, `spend`, `evidence`, `cloud-setup`, `down`) from the single `lights-out` executable without cloning `lights-out`.
- First invocation of a plane command for a given image revision copies the platform tree into `$out/platform`; subsequent invocations reuse it via `.platform-revision`.
- `lights-out` remains a standalone POSIX `sh` script shipped byte-identically inside the `lights-out-gates` wheel (`core/tests/test_one_install.py`), with zero external runtime dependencies on the host beyond POSIX `sh`, `git`, and `podman`/`docker`.

## Golden-rule check

`git clone && ./go demo` is untouched and remains key-free at L0. `./go ci` remains engine-free and offline, running `ci/package.sh` with fake `podman`/`docker` shims and planted-defect self-tests alongside `core/tests/test_wrapper_plane.py` and `ci/tests/test_wrapper_plane_e2e.py`.

## Provenance

| Item | Value / Rule | Provenance |
|---|---|---|
| Subcommand count | `24` (`8` existing + `4` state-only + `12` plane/workflow) | ADR 0045 §2, ADR 0103, ADR 0104, and this ADR |
| `KEY=*` allowlist | `29` keys synchronized across `lights-out` and `package/entrypoint.sh` | `ci/package.sh` `allowlist` check (ADR 0045 §2) |
| Default console port / publish host | `8098` on `127.0.0.1` | ADR 0047 §4–§5 (`go` parity) |
| Platform cache key | `.platform-revision` (`$LIGHTS_OUT_VERSION:$LIGHTS_OUT_REVISION:$LIGHTS_OUT_FLAVOUR`) | `package/Containerfile.tail` build env vars |
| Socket detection & SELinux flag | `RemoteSocket.Path` / `RemoteSocket.Exists` (podman), `DOCKER_HOST` / `/var/run/docker.sock` + `--group-add` (docker), `--security-opt label=disable` | `./go` proven engine socket pattern |

## Tests

Written from this ADR before implementation:

- `core/tests/test_wrapper_plane.py` (strictly vendor-free, app-free, and model-free): tests `--version`/help/unknown refusals, allowlist parity, read-only & state-only socket isolation, opt-in plane socket/mirroring/console/`ADAPTERS_PATH`/secret-by-name behavior, missing socket failure, and entrypoint staging (`LIGHTS_OUT_PLATFORM_DIR`, `.platform-revision`, `.crew` symlink, `cloud-setup`, `gates` flavour guard, exit code preservation).
- `ci/tests/test_wrapper_plane_e2e.py`: tests full E2E wrapper + entrypoint dispatch with workload/API-key fixtures, `start`/`next` flag & `KEY=VALUE` forwarding, host doctor integration, `promote` `REASON=` mapping, and `ci/package.sh` behavioral & planted-defect verification.
