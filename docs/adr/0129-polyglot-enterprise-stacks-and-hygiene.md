# ADR 0129 — Polyglot Enterprise Stacks (`dotnet`, `kotlin`, `swift`, `php`, `ruby`), 36-Shape Corpus, Outside `EventBus`, and CNCF Hygiene

- Status: accepted (amends [ADR 0042](0042-your-test-runner-in-your-image.md) §6, [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §5, [ADR 0102](0102-more-stacks-go-typescript-python-fallbacks.md), [ADR 0106](0106-unknown-repository-corpus.md), and [ADR 0110](0110-stateful-monorepo-rust-java-and-js-managers.md))
- Date: 2026-11-24
- Milestone: M89

## Context

Enterprise adoption of `lights-out` across heterogeneous engineering organizations encountered four concrete gaps:

1. **Five missing enterprise language stacks**: `core/init.py` and `core/stacks.py` inferred Python, JavaScript, TypeScript, Go, Rust, and Java, leaving out C#/.NET (`*.csproj`, `*.sln`), first-class Kotlin (`build.gradle.kts`, `pom.xml` with Kotlin plugins or `src/main/kotlin`), Swift (`Package.swift`), PHP (`composer.json`), and Ruby (`Gemfile`).
2. **Corpus coverage stopped at 24 shapes**: `core/corpus.py` verified 24 repository shapes and lacked positive and lockfile-refusal shapes for `.NET`, Kotlin, Swift, PHP, and Ruby (including container plane, Central Package Management, Maven vs Gradle Kotlin, RSpec vs Rake, and polyglot monorepo sub-services).
3. **No external `outside/*` fixture for `EventBus`**: `ci/outside.sh` proved `StateStore`, `Delivery`, `CodeHost`, `Registry`, `Flags`, and `Tracker` through cold `outside/*` fixtures, whereas `EVENT_BUS` had `eventbus-conformance` in `core/adapters.py` without a self-contained `fixtures/outside-adapters/file-eventbus/` fixture proving unproven refusal, timed lease redelivery (`CONFORMANCE_ACK_DEADLINE`), registration, tamper refusal, and broken-adapter refusal in `ci/outside.sh`.
4. **Missing top-level CNCF / OpenSSF community governance files**: The repository shipped `CONTRIBUTING.md` and `SECURITY.md` without `CODE_OF_CONDUCT.md` or `GOVERNANCE.md` documenting community standards, maintainer responsibilities, the ADR lifecycle, the six governed agent roles, and the human `go-for-launch` gate.

## Decision

### 1. Five Polyglot Enterprise Stacks (`core/stacks.py`, `core/init.py`, `stacks/<lang>/runners/`)

Extend `core/stacks.py` and `_infer_single_dir` in `core/init.py` with five deterministic, stdlib-only stack inferrers and zero-dependency Python 3 stdlib SAST (`sast.sh` -> `sast.py`) and line-scoped mutation (`mutation.sh` -> `mutate.py`) runners:

- **`dotnet`** (`stacks/dotnet/runners/`):
  - Detected when `*.csproj` or `*.sln` sits at the target root (and in monorepo sub-service discovery via `_MANIFEST_NAMES` / `.csproj` / `.sln`).
  - Requires `packages.lock.json` or `Directory.Packages.props` (at the service root, workspace root, or child project directories under a root `.sln`); refuses missing lockfiles with a single-line `Undraftable` naming `packages.lock.json`, `Directory.Packages.props`, and `dotnet restore --use-lock-file`.
  - Infers target framework major from `<TargetFramework>` / `<TargetFrameworks>` (`net<major>.<minor>`) or `global.json`, selecting `mcr.microsoft.com/dotnet/sdk:<major>.0` (default `8.0`) and `dotnet test`.
- **`kotlin`** (`stacks/kotlin/runners/`):
  - Promoted to a first-class `"kotlin"` stack when `pom.xml`, `build.gradle`, or `build.gradle.kts` declares Kotlin (`kotlin-maven-plugin`, `kotlin-stdlib`, `org.jetbrains.kotlin`, `kotlin("jvm")`, `kotlin("multiplatform")`, `id 'kotlin'`) or when `src/main/kotlin` holds `.kt` sources without pure-Java `src/main/java/*.java` taking precedence. Existing pure-Java Maven and Gradle repositories continue resolving to `"java"`, and direct callers of `stacks.infer_java` remain unchanged.
  - Requires `pom.xml` (Maven, `docker.io/library/maven:3.9-eclipse-temurin-21`, `./mvnw test` or `mvn test`) or `gradle.lockfile` / `gradle/verification-metadata.xml` / `buildscript-gradle.lockfile` (Gradle, `docker.io/library/gradle:8-jdk21`, `./gradlew test` or `gradle test`), with `KOTLIN_TEST_PATHS` (`src/test/*:*/src/test/*:*Test.kt:*/*Test.kt:*Tests.kt:*/*Tests.kt`).
- **`swift`** (`stacks/swift/runners/`):
  - Detected from `Package.swift`; requires `Package.resolved` (refusing missing `Package.resolved` with `swift package resolve` guidance), `swift test`, and `docker.io/library/swift:6.0`.
- **`php`** (`stacks/php/runners/`):
  - Detected from `composer.json`; requires `composer.lock` (refusing missing `composer.lock` with `composer install` guidance), `vendor/bin/phpunit`, and `docker.io/library/php:<major>.<minor>-cli` parsed from `require.php` / `config.platform.php` (default `8.3-cli`).
- **`ruby`** (`stacks/ruby/runners/`):
  - Detected from `Gemfile`; requires `Gemfile.lock` (refusing missing `Gemfile.lock` with `bundle lock` guidance), `bundle exec rspec` when `spec/` exists else `bundle exec rake test`, and `docker.io/library/ruby:<major>.<minor>` parsed from `.ruby-version`, `Gemfile`, or `Gemfile.lock` (default `3.3`).

Each stack's `sast.sh` emits `{"findings": N, "items": [{"rule", "file", "line"}]}` on stdout (honoring `nosec` / `NOSONAR` comments while stripping string/comment tokens) across five deterministic language security rules, and `mutation.sh` honors `MUTATION_LINES`, rejects red test suites before scoring, excludes compile errors where applicable, logs `SURVIVOR <file>:<line> <mutator>` on stderr, and emits `{"total", "killed", "survived", "score"}` on stdout.

### 2. 36-Shape Unknown-Repository Corpus (`core/corpus.py`, `ci/checks/corpus.py`)

- Expand `core.corpus.default_shapes()` from 24 to 36 shapes by adding 8 positive shapes (`dotnet-sdk-http`, `dotnet-cpm-sln-lib`, `kotlin-gradle-lib`, `kotlin-maven-http`, `swift-spm-lib`, `php-composer-lib`, `ruby-bundler-rspec`, `monorepo-dotnet-ruby-service`) and 4 negative lockfile-refusal shapes (`neg-dotnet-no-lock`, `neg-swift-no-resolved`, `neg-php-no-lock`, `neg-ruby-no-lock`), while updating `neg-unknown-language` from `Gemfile` (now supported as Ruby) to an unsupported `build.zig` fixture.
- Extend `ci.checks.corpus.self_test` to assert the 36-shape catalog size and verify planted polyglot stack expectation mismatches fail deterministically.

### 3. Outside `EventBus` Fixture and Cold Conformance (`fixtures/outside-adapters/file-eventbus/`, `ci/outside.sh`)

- Add `fixtures/outside-adapters/file-eventbus/{env.sh,ready.sh,publish.sh,pull.sh,ack.sh}` (all executable POSIX `sh`), implementing the `EventBus` port contract ([ADR 0019](0019-event-bus-and-tower.md) §3, [ADR 0060](0060-state-and-bus-conformance.md) §4) over an atomic file-locked spool and lease directory under `FILE_EVENTBUS_DIR` (default `.crew/file-eventbus`):
  - `publish.sh <data>` rejects empty or multi-line payloads and writes raw payload bytes atomically under an exclusive `fcntl.flock` lock.
  - `pull.sh` expires leases whose deadline (`FILE_EVENTBUS_ACK_DEADLINE`, falling back to `CONFORMANCE_ACK_DEADLINE`, default `600` seconds) has elapsed, leases at most one unleased message under a whitespace-free hex `<ackId>`, and writes `<ackId> <data>\n` byte-for-byte on stdout.
  - `ack.sh <ackId>` validates `<ackId>` and idempotently deletes the message held by `<ackId>` (treating already-acked or unknown IDs as exit 0).
- Wire `EVENT_BUS=outside/file-eventbus` into `ci/outside.sh` (`a5`): unproven refusal -> mid-trial tamper refusal -> broken-adapter `NO-GO` refusal -> `eventbus-conformance` `GO` across all 6 cases (`CONFORMANCE_ACK_DEADLINE=1`) + registration -> admission symlink -> 1-byte tamper refusal -> symlink-in-tree refusal.

### 4. CNCF / OpenSSF Hygiene Documentation (`CODE_OF_CONDUCT.md`, `GOVERNANCE.md`)

- Add `CODE_OF_CONDUCT.md` adopting Contributor Covenant 2.1 standards, scope, reporting, and enforcement aligned with `SECURITY.md`.
- Add `GOVERNANCE.md` documenting project maintainers, DCO contribution rules (`CONTRIBUTING.md`), the ADR decision process (`./go adr-check`, `core/adr_gov.py`), high-risk human review boundaries (`RISK_HIGH_PLATFORM` in `policy/merge.env`), external adapter conformance (`core/adapters.py`), the six governed agent roles (`roles/`), and the human `go-for-launch` promotion gate (`./go promote`).

## Alternatives rejected

- **Folding Kotlin unconditionally into `"java"`**: Rejected — Kotlin repositories use `src/main/kotlin`, Kotlin-specific build plugins, and Kotlin string-template / coroutine idioms that benefit from dedicated `KT001`..`KT005` SAST rules and `KOTLIN_TEST_PATHS`, while keeping pure-Java projects on `"java"` avoids breaking existing Java contracts and tests.
- **Allowing `.csproj`, `Package.swift`, `composer.json`, or `Gemfile` without a committed lockfile**: Rejected — `gates/deps.sh` (`osv-scanner 2.6.0`) requires deterministic pinned dependency manifests (`packages.lock.json` / `Directory.Packages.props`, `Package.resolved`, `composer.lock`, `Gemfile.lock`); silently skipping dependency scanning would violate the no-hidden-mocks rule.
- **Skipping case 5 redelivery in `ci/outside.sh` for `outside/file-eventbus`**: Rejected — running `eventbus-conformance` with `CONFORMANCE_ACK_DEADLINE=1` adds roughly 2 seconds while proving real timed lease expiry and redelivery of unacked messages alongside non-redelivery of acked messages.

## Consequences

- `./go init` now supports 11 first-class language stacks (`python`, `javascript`, `typescript`, `go`, `rust`, `java`, `kotlin`, `dotnet`, `swift`, `php`, `ruby`), all validated cold by `ci/corpus.sh` (`36/36` shapes) and `ci/workloads.sh`.
- `ci/outside.sh` proves seven conformance-gated `outside/*` ports (`StateStore`, `Delivery`, `CodeHost`, `Registry`, `Flags`, `Tracker`, `EventBus`) end to end without network or container-engine sockets.
- `CODE_OF_CONDUCT.md` and `GOVERNANCE.md` are checked continuously by `ci/docs-drift.sh` so cited paths, commands, and configuration keys cannot drift.

## Provenance

| Numeric threshold | Where it comes from |
|---|---|
| `5` new stacks (`11` total stacks) and `5` SAST rules per new stack | Decision: covers the five target enterprise ecosystems (`dotnet`, `kotlin`, `swift`, `php`, `ruby`) and the five core CWE classes (command injection, SQL injection, hardcoded credentials, weak crypto/TLS bypass, unsafe deserialization). |
| `36` corpus shapes (`24` prior + `8` positive + `4` negative) | Decision: extends [ADR 0110](0110-stateful-monorepo-rust-java-and-js-managers.md)'s `24`-shape catalog with positive shapes (including plane, flavor, and monorepo variants) and lockfile-refusal negatives for every newly added stack, staying well under `ci/tests/test_corpus_workflow.py`'s `20000` ms (`20` s) ceiling. |
| `10.0`x clean-suite timeout multiplier and `1.0` s floor in `mutate.py` | External to this ADR: matches `stacks/rust/runners/mutate.py` and `stacks/java/runners/mutate.py` ([ADR 0110](0110-stateful-monorepo-rust-java-and-js-managers.md)) to bound infinite-loop mutants. |
| Default toolchain versions (`8.0` for .NET SDK, `21` / Gradle `8` / Maven `3.9` for Kotlin, `6.0` for Swift, `8.3-cli` for PHP, `3.3` for Ruby) | External constraint: current upstream LTS / stable official container tags when a repository manifest declares no explicit runtime version. |
| `600` s default `FILE_EVENTBUS_ACK_DEADLINE` and `1` s `CONFORMANCE_ACK_DEADLINE` in `ci/outside.sh` (`6` conformance cases, `16`-byte / `32`-hex `<ackId>`, `12` hex chars in `core/adapters.py`) | External / decision: `600` s matches `adapters/spool/spool.py` ([ADR 0060](0060-state-and-bus-conformance.md) §2); `1` s is the minimum positive integer accepted by `core/gates/eventbus.py` (`deadline + 1` = `2` s wait for case `5` of `6`); `12`-char suite digest prefix comes from [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §3. |
| `6` governed agent roles and `100`% production traffic behind human `go-for-launch` | External to this ADR: scope invariant enforced by `ci/roles.sh` (`CLAUDE.md`) and [ADR 0013](0013-canary-controller-and-verdict.md) / [ADR 0020](0020-human-promote-gate-and-prompts.md). |
