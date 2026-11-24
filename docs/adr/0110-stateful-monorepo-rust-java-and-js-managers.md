# ADR 0110 — Complex real-repo support: Rust, Java/Kotlin, JS package managers, monorepo discovery, and stateful plane drafting

- Status: accepted (extends [ADR 0042](0042-gates-anywhere-init-and-the-toolchain-contract.md), [ADR 0100](0100-draft-the-plane-half-from-the-container.md), [ADR 0102](0102-more-stacks-go-typescript-python-fallbacks.md), [ADR 0106](0106-unknown-repository-corpus.md))
- Date: 2026-11-24
- Milestone: P4 (Track 4)

## Context

`core/init.py`, `core/stacks.py`, and `core/init_plane.py` previously handled single-root repositories for Python (`uv`, `poetry`, pinned `requirements.txt`), JavaScript (`npm` + `package-lock.json`), TypeScript (`npm` + `tsconfig.json` + `package-lock.json`), and Go (`go.mod` + `go.sum`), with single-container plane drafting (`Containerfile` / `Dockerfile` at root).

Real repositories frequently use:
1. **Rust** (`Cargo.toml` + `Cargo.lock`) and **Java / Kotlin** via **Maven** (`pom.xml`) or **Gradle** (`build.gradle` / `build.gradle.kts` + Gradle lockfiles).
2. Alternative JavaScript / TypeScript package managers (**pnpm**, **Yarn**, **Bun**) alongside `npm`.
3. **Monorepos and workspaces** (`package.json` `"workspaces"`, `pnpm-workspace.yaml`, `Cargo.toml` `[workspace]`, `settings.gradle` / `settings.gradle.kts`, multi-module `pom.xml`, or multi-service subdirectories) where one repository holds multiple deployable services.
4. **Stateful services** needing Postgres, Redis, or MySQL dependencies, database migrations before startup, isolated `crew-*` dependency containers for dev-workers ([ADR 0046](0046-harness-egress-confined-to-the-provider.md)), authenticated health/load probes, and opt-in worker package-index egress.

### Empirical `osv-scanner 2.6.0` verification in workbench

Before choosing `WORKLOAD_LOCKFILES` values and refusal rules, every candidate lockfile and manifest format was tested directly inside `localhost/lights-out-workbench:4150781479` (`osv-scanner version: 2.6.0`, `osv-scalibr version: 0.5.2`) using `osv-scanner scan source --format json -L <file>`:

| Ecosystem | File / Variant | Empty / No-Deps Exit | Populated Exit | Supported? | Empirical Notes |
|---|---|---|---|---|---|
| Rust | `Cargo.lock` (v3 & v4) | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Handled cleanly by `core/gates/deps.py` (`0`, `1`, or `128` with `No package sources found`). |
| JS / TS | `pnpm-lock.yaml` (v6 & v9) | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Supported directly. |
| JS / TS | `yarn.lock` (v1 & Berry v8) | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Supported directly. |
| JS / TS | `bun.lock` (JSON / JSONC with trailing commas) | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Text lockfile (Bun >= 1.2) supported directly. |
| JS / TS | `bun.lockb` (legacy binary format) | `127` (`could not determine extractor suitable to this file`) | `127` | **No** | Rejected by `osv-scanner 2.6.0`. Must be refused at inference time when no text `bun.lock` exists. |
| Java / Kotlin | `pom.xml` (with literal or `${property}` deps) | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Supported directly; emits non-fatal enricher warning on stderr. |
| Java / Kotlin | `gradle.lockfile` | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Supported directly. |
| Java / Kotlin | `buildscript-gradle.lockfile` | — | `0` (`1 package`) | Yes | Supported directly. |
| Java / Kotlin | `gradle/verification-metadata.xml` | `128` (`No package sources found`) | `0` (`1 package`) | Yes | Supported directly. |
| Java / Kotlin | `build.gradle` / `build.gradle.kts` | `127` (`could not determine extractor suitable to this file`) | `127` | **No** | Unsupported without `gradle.lockfile`, `gradle/verification-metadata.xml`, or `buildscript-gradle.lockfile`. |

Workbench (`localhost/lights-out-workbench:4150781479`) ships `node` `v24.21.0`, `npm` `11.19.0`, `corepack` `0.36.0`, `python3` `3.13`, and `uv` `0.12.13`, and ships **no** `rustc`/`cargo`, `java`/`mvn`/`gradle`, `pnpm`/`yarn` shims on `PATH`, or `bun`.

## Decision

### 1. Rust stack (`core/stacks.py`, `stacks/rust/runners/`)

- Detected by `Cargo.toml` (when unambiguous or targeted via `INIT_SERVICE`).
- Requires `Cargo.lock` beside `Cargo.toml` (or at workspace root when `INIT_SERVICE` targets a Cargo workspace member); otherwise raises `Undraftable` naming `Cargo.toml`, `Cargo.lock`, and `cargo generate-lockfile`.
- Drafts:
  - `WORKLOAD_LOCKFILES`: `Cargo.lock` (or `<service>/Cargo.lock` when crate-local)
  - `WORKLOAD_MUTATION_SCOPE`: `src/` (or first non-test source dir / `.`)
  - `WORKLOAD_TEST_PATHS`: `tests/*.rs:tests/*/*.rs:*_test.rs:*/*_test.rs`
  - `TOOLCHAIN_IMAGE`: `docker.io/library/rust:1.85` (with digest-pinning note in provenance)
  - `TEST_CMD`: `cargo test`
  - `SAST_CMD` / `MUTATION_CMD`: `stacks/rust/runners/{sast.sh,mutation.sh}` (POSIX `sh` wrappers, `shellcheck -s sh` clean, backed by Python 3 stdlib tokenizers/mutators requiring zero external crates).
- `sast.py` strips comments/strings and flags `unsafe` blocks/fns/impls (`RS001`), hardcoded credential literals (`RS002`), dynamic shell/process commands (`RS003`), dynamic SQL string formatting (`RS004`), disabled TLS certificate/hostname verification (`RS005`), and weak crypto / `mem::transmute` (`RS006`), honoring `// nosec`.
- `mutate.py` runs the clean suite first (10x timeout, floor 1s), scopes mutants via `MUTATION_LINES`, avoids mutating `#[cfg(test)]` modules, `const` definitions, attributes, `use` paths, and generic `<T>` type brackets, excludes compile-error mutants from the population, reports `SURVIVOR file:line mutator` on stderr, and emits `{"total","killed","survived","score"}` on stdout.
- **`ci/vendor-free.sh` word-boundary update**: tightened `argo` to POSIX-portable `(^|[^a-z])argo` under `grep -rniE` so Rust literals (`Cargo.toml`, `Cargo.lock`, `cargo test`, `cargo generate-lockfile`) in `core/` do not false-positive while vendor tokens (`argo`, `argo-rollouts`, `argocd`, `ARGO_NAMESPACE`, `my_argo`) still fail cleanly (`ci/tests/test_stacks_extended_contract.py`).

### 2. Java and Kotlin stack (`core/stacks.py`, `stacks/java/runners/`)

- Detected by `pom.xml` (Maven) or `build.gradle` / `build.gradle.kts` (Gradle).
- **Maven**: `WORKLOAD_LOCKFILES=pom.xml`, `TOOLCHAIN_IMAGE=docker.io/library/maven:3.9-eclipse-temurin-21`, `TEST_CMD=./mvnw test` (when `mvnw` exists in the service or repo root) else `mvn test`.
- **Gradle**: requires `gradle.lockfile`, `gradle/verification-metadata.xml`, or `buildscript-gradle.lockfile` (in service or workspace root); if absent, raises `Undraftable` naming `./gradlew dependencies --write-locks`, `gradle.lockfile`, and `gradle/verification-metadata.xml` because `osv-scanner 2.6.0` exits `127` on `build.gradle`. Sets `TOOLCHAIN_IMAGE=docker.io/library/gradle:8-jdk21` and `TEST_CMD=./gradlew test` (when `gradlew` exists) else `gradle test`.
- Production scope defaults to `src/main/` (fallback `src/`), `WORKLOAD_TEST_PATHS=src/test/*:*/src/test/*:*Test.java:*/*Test.java:*Tests.java:*/*Tests.java:*Test.kt:*/*Test.kt:*Tests.kt:*/*Tests.kt`.
- `stacks/java/runners/{sast.sh,sast.py,mutation.sh,mutate.py}` provide zero-dependency static checks (`Runtime.exec`/`ProcessBuilder`, dynamic SQL in `Statement`/`Query`, hardcoded credentials, weak `MD5`/`SHA-1`/`DES`/`ECB`, unsafe deserialization/`XMLInputFactory`, honoring `// nosec` / `// NOSONAR`) and line-scoped Java/Kotlin mutation with compile-error exclusion.

### 3. Extended JavaScript and TypeScript package managers (`core/stacks.py`)

Shared JS/TS inference checks lockfiles in service directory (or workspace root when `INIT_SERVICE` is set), honoring `packageManager` hints when multiple lockfiles coexist and falling back in order:
1. `package-lock.json` -> `npm ci` + `npm test` / `node --test`, `WORKLOAD_LOCKFILES=package-lock.json`, `TOOLCHAIN_IMAGE=workbench` (or `docker.io/library/node:<major>`).
2. `pnpm-lock.yaml` -> `corepack pnpm install --frozen-lockfile` + (`corepack pnpm test` or `node --test`), `WORKLOAD_LOCKFILES=pnpm-lock.yaml`, `TOOLCHAIN_IMAGE=workbench` (or `docker.io/library/node:<major>`).
3. `yarn.lock` -> `corepack yarn install --immutable` (Berry `__metadata:`) or `corepack yarn install --frozen-lockfile` (v1) + (`corepack yarn test` or `node --test`), `WORKLOAD_LOCKFILES=yarn.lock`, `TOOLCHAIN_IMAGE=workbench` (or `docker.io/library/node:<major>`).
4. `bun.lock` -> `bun install --frozen-lockfile` + (`bun run test` or `bun test`), `WORKLOAD_LOCKFILES=bun.lock`, `TOOLCHAIN_IMAGE=docker.io/oven/bun:1`.
5. `bun.lockb` alone -> raises `Undraftable` explaining `osv-scanner 2.6.0` rejects binary `bun.lockb` (`rc=127`) and instructing `bun install --save-text-lockfile` to emit `bun.lock`.
6. None present -> raises `Undraftable` naming `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, and `bun.lock`.

### 4. Monorepo and sub-service discovery (`INIT_SERVICE`, `core/init.py`, `core/stacks.py`)

- `init.infer(repo, service=None)` and `init.write_draft(..., service=None)` (plus `--service` CLI flag) honor `INIT_SERVICE=<relpath>` from the environment when `service` is omitted. Path traversal (`..`, absolute paths, `$`, `#`) and missing directories raise `Undraftable`.
- When `INIT_SERVICE` is omitted, workspace discovery inspects:
  - `package.json` `"workspaces"` (list or `{"packages": [...]}`) and `pnpm-workspace.yaml`
  - `Cargo.toml` `[workspace]` `members` / `exclude`
  - `settings.gradle` / `settings.gradle.kts` `include(...)`
  - `pom.xml` `<modules>`
  - fallback 1-2 level subdirectory scan when no supported root manifest exists.
- Whenever discovery finds 2+ sub-services (or a workspace with ambiguous members), inference **refuses to guess** and raises `Undraftable` listing the discovered sub-services and instructing `INIT_SERVICE=<service>`.
- When `INIT_SERVICE=<service>` is active:
  - `WORKLOAD_TEST_ROOT=<service>/`
  - `WORKLOAD_MUTATION_SCOPE=<service>/<local_scope>`
  - `WORKLOAD_TEST_PATHS` prefixes every glob with `<service>/` so `core/testdiff.py` and `core/gates/testproof.py` scope strictly to `<service>/`.
  - `WORKLOAD_LOCKFILES` uses `<service>/<lockfile>` when local to the service or `<lockfile>` when inherited from the workspace root.
  - Under npm workspaces (`npm 11.19.0` verified empirically in workbench), running plain `npm ci` inside `<service>/` fails with `EUSAGE`; monorepo inference adds `--workspaces=false` when `<service>/package-lock.json` is local and `--prefix <rel_root>` when inheriting the workspace root lockfile.
  - `SAST_CMD` and `MUTATION_CMD` address `.lights-out/runners/` relative to `<service>/` (`posixpath.relpath(runners_dir, service)`, e.g. `../../.lights-out/runners/sast.sh`), containing zero `$` or `#` hazards.

### 5. Stateful Compose, migration, auth smoke, and opt-in egress drafting (`core/init_plane.py`)

- `detect_container(repo, service=None)` searches `<service>/{Containerfile,Dockerfile}` first when `service` is set, then root `{Containerfile,Dockerfile}`.
- `detect_stateful_deps(repo, service=None)` scans non-comment lines in `.env.example`, `compose.yaml`, and `docker-compose.yml` (at root and `<service>/` when set) for `postgres` (`DATABASE_URL`, `POSTGRES_*`, `postgres://`, `postgresql://`, `postgres`/`postgis` service or image), `redis` (`REDIS_URL`, `REDIS_*`, `redis://`, `rediss://`, `redis`/`valkey`), and `mysql` (`MYSQL_URL`, `MYSQL_*`, `mysql://`, `mysql`/`mariadb`).
- When stateful dependencies are detected, drafted `compose.yaml` appends annotated `#` comments containing:
  - Ephemeral `crew-postgres` / `crew-redis` / `crew-mysql` services confined to `profiles: ["crew"]`, `networks: [crew]`, `dns: ["192.0.2.1"]` with no published ports ([ADR 0046 §6-7](0046-harness-egress-confined-to-the-provider.md)).
  - An `x-crew-worker` isolation snippet wiring `depends_on` and isolated test-DB/cache URLs into harness services without hardcoding vendor service names inside `core/`.
  - An optional `migrate` one-shot hook (`restart: "no"`, `condition: service_completed_successfully`).
  Keeping these sections annotated comments preserves cold `docker-compose config` validity (`ci/workloads.sh` and `ci/harness-shape.sh`) and avoids unpinned DB pulls until the repo operator fills in project-specific images and credentials.
- Drafted `smoke.sh` and `load.sh` honor optional `SMOKE_AUTH_HEADER` via POSIX `set -- ... -H "$SMOKE_AUTH_HEADER"` (`shellcheck -s sh` clean) without embedding secrets or echoing credentials.
- `draft_plane` also writes `crew-egress.txt.example` (commented out 100%, opt-in only) documenting ADR 0046 §5 exfiltration tradeoffs and listing example registry `host:port` entries.

### 6. Extended corpus and example workloads

- `workloads/minimal-rust/` (`Cargo.toml`, `Cargo.lock`, `src/lib.rs`, `tests/calc_test.rs`) and `workloads/minimal-java/` (`pom.xml`, `Containerfile`, `src/main/java/...`, `src/test/java/...`) provide complete valid `.lights-out/` contracts passing `ci/workloads.sh`, `ci/shell.sh`, and `osv-scanner 2.6.0`.
- `core/corpus.py` adds synthetic shapes for Rust, Java Maven, Java Gradle, JS `pnpm`, TS `yarn`, `INIT_SERVICE` monorepo, stateful compose + auth smoke + egress example (positive), comment-only `.env.example` (negative stateful check), and negative refusals for missing `Cargo.lock`, missing `gradle.lockfile`, binary `bun.lockb`, and ambiguous workspace monorepos — keeping cold `./go corpus` fast (< 10s).

## Alternatives rejected

- **Passing `-L build.gradle` or `-L bun.lockb` to `osv-scanner 2.6.0`**: Empirically exits `127` (`could not determine extractor suitable to this file`), breaking `gates/deps.sh`. Requiring supported lockfiles (`gradle.lockfile` / `gradle/verification-metadata.xml` and text `bun.lock`) keeps `core/gates/deps.py` untouched and surfaces actionable fix commands.
- **Emitting uncommented `postgres`/`redis`/`mysql` and harness anchors directly in drafted `compose.yaml`**: Would require guessing unpinned image tags, credentials, and vendor harness service names inside `core/` (violating `ci/vendor-free.sh` and `ci/harness-shape.sh`). Annotated scaffolding gives operators ready-to-enable ADR 0046 templates without breaking cold `ci/workloads.sh`.
- **Auto-picking the first workspace member on ambiguous monorepos**: Guessing a sub-service silently drafts the wrong contract. Refusing with the exact discovered member list and `INIT_SERVICE=<service>` is explicit and deterministic.
- **Bundling external Rust/Java SAST or mutation crates/plugins (`cargo-mutants`, `pitest`, `spotbugs`)**: Requires network downloads and non-stdlib toolchains inside minimal images. Pure Python 3 stdlib tokenizers/mutators require zero extra dependencies in `rust:1.85`, `maven:3.9-eclipse-temurin-21`, `gradle:8-jdk21`, or `workbench`.

## Consequences

- `lights-out init` supports Python (`uv`, `poetry`, pinned `requirements.txt`), JS/TS (`npm`, `pnpm`, `yarn`, `bun.lock`), Go, Rust, Java/Kotlin (Maven & Gradle), and monorepos (`INIT_SERVICE`).
- Rust and Java/Kotlin workloads run via official container images (`TOOLCHAIN_IMAGE`) while staying cold-verifiable via `ci/workloads.sh`, `core/light.py`, and `osv-scanner` inside workbench.
- `core/` remains 100% stdlib-only, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh`), and model-free (`ci/model-free.sh`).
