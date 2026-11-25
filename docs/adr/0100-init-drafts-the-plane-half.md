# ADR 0100 — `init` drafts the plane half when a container build file exists

- Status: accepted (M66)
- Date: 2026-11-24
- Milestone: M66
- Amends: [ADR 0097 §4](0097-from-zero-to-hero-and-a-start-with-nothing-installed.md) (removes the blocker against drafting the plane half); [ADR 0042 §6–§7](0042-gates-anywhere-init-and-the-toolchain-contract.md) (`init` drafts the plane half alongside the gates half when a container build file exists); [ADR 0064 §3](0064-the-first-run-drafts-the-contract.md) (`init/summary.md` lists drafted plane files); [ADR 0098 §3–§4](0098-verify-from-the-proxy.md) (`WORKLOAD_PORT` extends the proxy/probe port beyond `8000`)

## Context

ADR 0097 §4 rejected drafting the plane half (`compose.yaml`, `flags/flags.json`, `smoke.sh`, `load.sh`) in `./go init` because ADR 0030 §3 required every workload to implement `GET /healthz` JSON, `GET /metrics` with `target_info{service_version}`, and a runtime feature-flag kill switch in application code. Starting a plane over a drafted `compose.yaml` without those endpoints would have produced an unjudgeable canary — a hidden mock.

ADR 0098 (M65) removed that requirement on the L0 edge-proxy path:
1. `verify/slo.promql` evaluates traffic, 5xx error rate, and latency p95 from Envoy's upstream cluster counters rather than application `/metrics` or `target_info`.
2. `adapters/envoy-canary/status.sh` reads container versions from the container engine socket (`lights-out.version` label, `APP_VERSION` env, or image tag/digest) rather than `/healthz` JSON.
3. `WORKLOAD_HEALTH_PROBE` accepts an HTTP path or TCP connect check (`tcp` / `tcp:<port>`), and `WORKLOAD_KILL_SWITCH` may be empty so rollback shifts traffic back to stable without requiring an application flag SDK.

One hard assumption remained from ADR 0030 §3: Envoy's `stable` and `canary` upstream clusters and `deploy.sh` / `ready.sh` hardcoded port `8000`, whereas real repositories frequently `EXPOSE` `3000`, `5000`, `8080`, or another port.

## Decision

### 1. Optional `WORKLOAD_PORT` in `workload.env` and `ci/workloads.sh`

- `workload.env` gains an optional thirteenth/fourteenth key `WORKLOAD_PORT` alongside `WORKLOAD_HEALTH_PROBE`.
- Valid values are empty (`""`) or an integer in `1..65535` (canonical decimal without leading zeros). When unset or empty, the default is `8000` (provenance: ADR 0030 §3 compatibility).
- `ci/workloads.sh` validates `WORKLOAD_PORT` syntax and permits both `WORKLOAD_HEALTH_PROBE` and `WORKLOAD_PORT` as optional keys.
- `Makefile` resets `WORKLOAD_PORT :=` unless set on the command line (matching `WORKLOAD_HEALTH_PROBE`), so an exported value never leaks across recursive `make` invocations for other workloads.

### 2. Dynamic upstream port in `adapters/envoy-canary` and `compose.yaml`

- `compose.yaml` passes `WORKLOAD_PORT: "${WORKLOAD_PORT:-8000}"` in the `envoy` service's environment.
- `adapters/envoy-canary/envoy.yaml` retains `port_value: 8000` on the `stable` and `canary` clusters so build-time `RUN envoy --mode validate -c /etc/envoy/envoy.yaml` in `adapters/envoy-canary/Containerfile` stays valid without build-time variables.
- `adapters/envoy-canary/entrypoint.sh` validates `WORKLOAD_PORT` (default `8000`, integer `1..65535`), copies `/etc/envoy/envoy.yaml` to `/tmp/envoy.yaml` (writable under `user: envoy`), replaces `port_value: 8000` with `port_value: ${WORKLOAD_PORT:-8000}` when needed, and `exec`s `envoy -c /tmp/envoy.yaml "$@"`. `adapters/envoy-canary/Containerfile` installs this entrypoint and sets `CMD []` so the base image's default `CMD` does not append duplicate arguments.
- `adapters/envoy-canary/deploy.sh` and `adapters/envoy-canary/ready.sh` use `${WORKLOAD_PORT:-8000}` as the port for HTTP probes (`deploy.sh` against `backend-canary`) and bare `tcp` probes (`deploy.sh` against `backend-canary`, `ready.sh` against `backend`). Explicit `tcp:<port>` in `WORKLOAD_HEALTH_PROBE` overrides `${WORKLOAD_PORT:-8000}`.

### 3. Plane-half inspection and drafting (`core/init_plane.py`)

A new stdlib-only module `core/init_plane.py` (strictly compliant with `ci/vendor-free.sh` and `ci/app-free.sh`, never naming vendors, bundled workloads, or the contract directory string) provides:

- `detect_container(repo)`:
  - Checks `Containerfile` then `Dockerfile` at `repo` root. Returns `None` if neither exists (repos without a container build file stay gates-only and print a clean stderr note).
  - Parses logical Dockerfile instructions (ignoring `#` comment lines, joining `\` line continuations, case-insensitive instructions).
  - Extracts `dockerfile` (`"Containerfile"` or `"Dockerfile"`).
  - Extracts the first valid integer port (`1..65535`) from `EXPOSE` (stripping optional `/tcp` or `/udp` suffix; ignoring non-literal variables like `$PORT`), defaulting to `8000` when none is declared.
  - Extracts a clear HTTP path from `HEALTHCHECK` (matching `https?://<host>/<path>` with no `$`, `#`, or whitespace inside the path, producing `http:/<path>` or `http:/` when only host:port is given); `HEALTHCHECK NONE`, non-HTTP commands, variable-expanded URLs, or missing `HEALTHCHECK` fall back honestly to `tcp:<port>`. Never fabricates `/healthz`.
  - Detects multi-stage `FROM ... AS runtime` and `FROM ... AS test` stage names (case-insensitive `FROM`/`AS`/stage name, tolerating `FROM --platform=...`).

- `draft_plane(repo, contract_dir, info, committed=None)`:
  - Writes into `contract_dir` without overwriting any plane file that already exists there (preserving committed or hand-edited plane files):
    - `<contract_dir>/compose.yaml` defining the three `ci/workloads.sh` services:
      - `backend`: `image: localhost:5050/lights-out-backend:${APP_TAG:-latest}`, `build:` with `context: ${WORKLOAD}`, `dockerfile: <dockerfile>`, and `target: runtime` only when `has_runtime` is true;
      - `backend-canary`: `image: ${CANARY_IMAGE:-localhost:5050/lights-out-backend:latest}`, `profiles: ["canary"]`, and no `build:` section;
      - `backend-test`: `profiles: ["test"]`, `build:` with `context: ${WORKLOAD}`, `dockerfile: <dockerfile>`, and `target: test` only when `has_test` is true.
    - `<contract_dir>/flags/flags.json`: defines only the boolean `release-freeze` flag (`"on": true, "off": false`, `defaultVariant: "off"`). Omits `$schema` so `core/init_plane.py` carries zero flag-vendor names (`ci/vendor-free.sh`); the flag daemon consumes this JSON schema-free cleanly (verified on the pinned v0.16.3 image).
    - `<contract_dir>/smoke.sh` (executable `0o755`): POSIX `#!/bin/sh`, curls `$APP_URL<path>` (detected `HEALTHCHECK` HTTP path if clear, else `/`), accepting `2xx` or `3xx`.
    - `<contract_dir>/load.sh` (executable `0o755`): POSIX `#!/bin/sh`, loops `curl` against `"$APP_URL$WORKLOAD_SAMPLE_PATH"` (defaulting `WORKLOAD_SAMPLE_PATH` to `/` if empty/unset).
  - Returns an ordered mapping `{key: (value, provenance)}` for `workload.env`:
    - `CANARY_VERSION=0.2.0-canary` (`# Provenance, inferred: ...`)
    - `WORKLOAD_KILL_SWITCH=` (`# Provenance, inferred: ...`)
    - `WORKLOAD_HEALTH_PROBE=<detected>` (`# Provenance, inferred: ...`)
    - `WORKLOAD_SAMPLE_PATH=/` (`# Provenance, inferred: ...`)
    - `WORKLOAD_PORT=<port>` (`# Provenance, inferred: ...`), emitted only when `<port> != 8000`
  - When `committed` already carries a plane contract (any non-empty plane key or explicit `WORKLOAD_HEALTH_PROBE`/`WORKLOAD_PORT` in `committed`), `render_workload_env` preserves those committed keys with `# Provenance, kept: ...`, while still filling any un-set inferred keys (`WORKLOAD_HEALTH_PROBE` or non-8000 `WORKLOAD_PORT`) when absent from `committed`.

### 4. Minimal hooks in `core/init.py` and `package/entrypoint.sh`

- `core/init.py` hooks `core/init_plane.py` inside `write_draft` and `render_workload_env` only, leaving `infer()` untouched.
- `package/entrypoint.sh` lists any drafted plane files (`compose.yaml`, `flags/flags.json`, `smoke.sh`, `load.sh`) with their byte counts in `/out/init/summary.md` when `compose.yaml` is present.

## Alternatives rejected

- **Substitute `WORKLOAD_PORT` in `compose.yaml` via build args or separate Envoy image per workload**: Rebuilds the Envoy image whenever switching workloads; a lightweight runtime entrypoint substituting `/tmp/envoy.yaml` keeps one cached Envoy image while validating the baked config at build time.
- **Guess `/healthz` or `/health` when `HEALTHCHECK` is absent**: Violates "no hidden mocks" (`CLAUDE.md`) — a real app without `/healthz` would hang `ready.sh` and `deploy.sh` until timeout. `tcp:<port>` succeeds when the container binds its declared port and fails loudly otherwise.
- **Overwrite existing `compose.yaml` / `flags/flags.json` / `smoke.sh` / `load.sh` unconditionally**: Would destroy hand-written plane configurations and kill-switch flags (e.g., existing attached workloads with custom flags) whenever `./go init` re-drafts `workload.env` and `runners/`.

## Consequences

- Any repository with a supported stack (`pyproject.toml`/`uv.lock` or `package.json`/`package-lock.json`) and a root `Containerfile` or `Dockerfile` receives a complete, `ci/workloads.sh`-conformant gates + plane draft from `./go init` / `lights-out init`.
- Repositories without a `Containerfile` or `Dockerfile` keep the exact 12-key gates-only draft.
- `smoke.sh` and `load.sh` assume an HTTP server answering `2xx`/`3xx` on `/` (or on the `HEALTHCHECK` HTTP path); pure non-HTTP TCP servers must edit `smoke.sh` / `load.sh` after drafting.

## Golden-rule check

`./go demo` continues to use `app/` with default `WORKLOAD_PORT` (`8000`) and passes through the same Envoy container image. `./go ci` remains engine-free and offline, gaining unit and contract tests in `core/tests/test_plane_init.py` and `ci/tests/test_plane_init_contract.py`.

## Provenance

| Item | Value | Provenance |
|---|---|---|
| Default `WORKLOAD_PORT` | `8000` | ADR 0030 §3 backward compatibility |
| Valid `WORKLOAD_PORT` range | `1..65535` | TCP/UDP 16-bit port range (RFC 793) |
| Default `CANARY_VERSION` | `0.2.0-canary` | Decision: distinguishes canary from baseline `0.1.0` without naming any workload |
| Default `WORKLOAD_SAMPLE_PATH` | `/` | Decision: universal HTTP root path when app routes are unknown |
| Default `WORKLOAD_KILL_SWITCH` | empty (`""`) | ADR 0098 §5: proxy SLO rollback needs no in-app kill switch |
| Fallback `WORKLOAD_HEALTH_PROBE` | `tcp:<port>` | Decision + ADR 0098 §3: avoids fabricating `/healthz` |

## Tests

Written from this ADR before implementation:
- `core/tests/test_plane_init.py` (strictly vendor-free and app-free):
  - `detect_container`: precedence `Containerfile` over `Dockerfile`, none when absent, first `EXPOSE` port extraction (plain, `/tcp`, multi-port, `$VAR` fallback to `8000`), `HEALTHCHECK` HTTP path extraction (shell form, exec JSON form, multiline `\`, host-only `/`, ignoring `$VAR`/`#` or non-HTTP/NONE -> `tcp:<port>`), `AS runtime` / `AS test` stage detection (single-stage vs multi-stage vs `--platform=`).
  - `draft_plane`: writes `compose.yaml` (with and without `target: runtime`/`test`, `backend-canary` without `build:`), `flags/flags.json` (`release-freeze` boolean only), executable `smoke.sh` (detected path or `/`, 2xx/3xx), executable `load.sh` (`$APP_URL$WORKLOAD_SAMPLE_PATH`); preserves existing files; returns expected keys & `# Provenance, inferred:` comments; omits `WORKLOAD_PORT` when `8000` and includes it when `!= 8000`.
  - `core/init.py` hooks: `write_draft` + `render_workload_env` draft plane + workload keys when container file exists, preserve committed values, and stay gates-only with clean stderr note when absent.
- `ci/tests/test_plane_init_contract.py`:
  - `ci/workloads.sh` accepts valid `WORKLOAD_PORT` (`""`, `1`, `3000`, `65535`) and rejects invalid ones (`0`, `65536`, `abc`, `08000`, `8000/tcp`).
  - Drafted `compose.yaml` + `workload.env` + `flags/flags.json` pass `docker-compose config` / `ci/workloads.sh`, and drafted `smoke.sh` / `load.sh` pass `shellcheck -s sh` and behave accurately against a local HTTP test server.
  - `adapters/envoy-canary/{entrypoint.sh,deploy.sh,ready.sh}` and `compose.yaml` handle `WORKLOAD_PORT` substitution (`port_value: <port>`) while keeping `/etc/envoy/envoy.yaml` intact at `8000`.
  - `package/entrypoint.sh` `summary.md` lists drafted plane files when `compose.yaml` is present.
