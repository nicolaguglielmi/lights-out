# ADR 0102 — More stacks: Go, TypeScript, and Python poetry and requirements fallbacks

- Status: accepted (amends [ADR 0042 §6](0042-gates-anywhere-init-and-the-toolchain-contract.md), [ADR 0053 §1](0053-tests-that-prove-and-tickets-that-wait.md))
- Date: 2026-11-24
- Milestone: M71

## Context

`core/init.py` previously inferred only two stack shapes: `python` (`pyproject.toml` + `uv.lock`) and `javascript` (`package.json` + `package-lock.json`). Real repositories that use Go (`go.mod` + `go.sum`), TypeScript (`package.json` + `tsconfig.json` + `package-lock.json`), Poetry (`pyproject.toml` + `poetry.lock`), or pinned `requirements.txt` (`==`) were rejected as `Undraftable`.

Empirical checks in the workbench image (`localhost/lights-out-workbench:4150781479`) establish three constraints:

1. **Node 24 executes `.ts` natively.** Workbench ships Node `v24.21.0`, so `node --test` executes TypeScript test suites directly without a separate build step when `package.json` has no `scripts.test`. However, `eslint-plugin-security` alone fails to parse TypeScript syntax unless `@typescript-eslint/parser` and `typescript` are installed in the runner prefix.
2. **Workbench ships no Go toolchain.** Go inference cannot draft `TOOLCHAIN_IMAGE=workbench`; it must select `docker.io/library/golang:<major>.<minor>` from `go.mod` and note digest pinning in provenance.
3. **`osv-scanner 2.6.0` scans `go.mod`, not `go.sum`.** Running `osv-scanner scan source --format json -L go.sum` exits `127` (`could not determine extractor suitable to this file`), whereas `-L go.mod` succeeds (`0` clean or empty, `1` with advisories), and `-L poetry.lock` / `-L requirements.txt` also succeed (`0`, `1`, or `128` when empty, which `core/gates/deps.py` already treats as clean).

## Decision

1. **Inference module and precedence (`core/stacks.py`, wired via `core/init.py` `infer(repo)`):**
   Precedence at repository root:
   `python [uv -> poetry -> pinned requirements.txt]` -> `go` -> `typescript` -> `javascript`.
   - **`pyproject.toml` + `uv.lock`**: existing `infer_python(repo)` unchanged.
   - **`pyproject.toml` + `poetry.lock` (no `uv.lock`)**: `infer_python_fallback(repo)` drafts `stack=python`, `WORKLOAD_LOCKFILES=poetry.lock`, pytest test/SAST/mutation commands using `stacks/python/runners/`.
   - **`requirements.txt` (no `uv.lock` or `poetry.lock`)**: `infer_python_fallback(repo)` verifies every non-comment, non-blank requirement (stripping inline comments and environment markers after `;`, joining `\` continuations) uses an exact `==` version pin (no wildcard `*`). If any dependency is unpinned, it raises `Undraftable` naming all unpinned packages. When all are pinned, it drafts `stack=python`, `WORKLOAD_LOCKFILES=requirements.txt`, `uv run --no-project --with-requirements requirements.txt --with pytest python -m pytest ...`, and `stacks/python/runners/`.
   - **`go.mod`**: `infer_go(repo)` requires `go.sum` beside `go.mod` (else raises `Undraftable` naming `go mod tidy`). It parses `go <major>.<minor>` from `go.mod`, sets `TOOLCHAIN_IMAGE=docker.io/library/golang:<ver>` (noting digest pinning in provenance), `WORKLOAD_LOCKFILES=go.mod` (so `osv-scanner` in `gates/deps.sh` runs cleanly while `go.sum` presence is enforced), `WORKLOAD_TEST_PATHS=*_test.go:*/*_test.go`, `WORKLOAD_MUTATION_SCOPE` (`pkg/` -> `internal/` -> `cmd/` -> first Go source directory -> `.`), `TEST_CMD=go test ./...`, `SAST_CMD=sh .lights-out/runners/sast.sh <scope>`, and `MUTATION_CMD=sh .lights-out/runners/mutation.sh <scope> "go test ./..."`.
   - **`package.json` + `tsconfig.json`**: `infer_typescript(repo)` requires `package-lock.json` beside them (else `Undraftable` naming `npm install`), evaluates `engines.node` via `node_admits` against Node `24` (`workbench`) else `docker.io/library/node:<major>`, sets `WORKLOAD_LOCKFILES=package-lock.json`, `WORKLOAD_TEST_PATHS=test/*:*/test/*:*.test.*:*.spec.*:*-test.*:*_test.*`, `TEST_CMD=npm ci && npm test` (or `npm ci && node --test` when `scripts.test` is absent), `SAST_CMD=sh .lights-out/runners/sast.sh <src>`, and `MUTATION_CMD=npm ci && sh .lights-out/runners/mutation.sh <src> "<suite>"`.
   - **CI invariants preserved**: `core/stacks.py` and `core/tests/test_stacks.py` remain stdlib-only, 100% vendor-free (`ci/vendor-free.sh`), and 100% app-free (`ci/app-free.sh`), receiving the runners prefix from `core/init.py`.

2. **Go runners (`stacks/go/runners/`):**
   - `sast.sh` + `sast.go`: zero external Go dependencies; uses `go/ast`, `go/parser`, and `go vet -json` patterns over `<scope>` (skipping `*_test.go` and honoring `// nosec` comments) to emit ADR 0072 JSON `{"findings": N, "items": [{"rule", "file", "line"}]}` with CWD-relative forward-slash paths on stdout and diagnostics on stderr.
   - `mutation.sh` + `mutate.go`: zero external Go dependencies; uses `go/ast` + `go/parser` + `go/token` offset splicing, honors `MUTATION_LINES=all` or `<file>=<start>-<end>,...` wire format, runs the clean suite first to measure `10x` timeout (floor `1s`), excludes compile-error mutants from the population, treats timeouts as killed, emits `{"total","killed","survived","score"}` JSON on stdout, and writes `SURVIVOR file:line mutator` on stderr. Both `.sh` scripts are `shellcheck -s sh` clean.

3. **TypeScript runners (`stacks/typescript/runners/`):**
   - `sast.sh`, `mutation.sh`, `score.mjs`, `eslint.security.mjs`, `package.json`, `package-lock.json`: scans `.ts`, `.tsx`, `.mts`, and `.cts` via `eslint` + `eslint-plugin-security` + `@typescript-eslint/parser` + `typescript` and mutates via `@stryker-mutator/core` (10x clean timeout, floor `1s`, excluding `.d.ts` and test files). Lockfile generated inside workbench with `minimatch`/`qs` overrides so `osv-scanner` reports zero vulnerabilities.

4. **Example workloads (`workloads/minimal-ts/`, `workloads/minimal-go/`):**
   - `workloads/minimal-ts/`: Node 24 TypeScript gates-only workload with `.lights-out/` runners byte-identical to `stacks/typescript/runners/`, passing `ci/workloads.sh`, `osv-scanner`, and Node 24 native `node --test`.
   - `workloads/minimal-go/`: Go HTTP service (`Containerfile`, `go.mod`, `go.sum`, `pkg/calc/`, `cmd/server/`, `.lights-out/`) with runners byte-identical to `stacks/go/runners/`, passing `ci/workloads.sh` and `osv-scanner`.

## Alternatives rejected

- **Setting `WORKLOAD_LOCKFILES=go.sum` for Go**: `osv-scanner 2.6.0` exits `127` on `-L go.sum`, breaking `gates/deps.sh`. Requiring `go.sum` at inference time while passing `go.mod` to `WORKLOAD_LOCKFILES` enforces tidy lockfiles and keeps `core/gates/deps.py` untouched.
- **Bundling third-party Go SAST/mutation binaries (`gosec`, `go-mutesting`)**: requires external Go module downloads and breaks air-gapped or zero-dep `stacks/go/runners/`. Pure Go stdlib AST tools require zero module dependencies.
- **Installing `go` inside `workbench/Containerfile` for `./go ci`**: `./go ci` runs engine-free and must not require pulling `golang` images or enlarging `workbench` to pass repo self-checks.

## Consequences

- `./go init` supports Python (`uv`, `poetry`, pinned `requirements.txt`), Go, TypeScript, and JavaScript repositories.
- Go repos running `./go gates` locally require an engine socket (`TOOLCHAIN_IMAGE=docker.io/library/golang:<ver>`), while TypeScript repos admitting Node 24 run directly in `workbench`.
- Unpinned `requirements.txt` or `go.mod` without `go.sum` fails fast with an actionable `Undraftable` message.

## Provenance

| Choice | Value | Provenance |
|---|---|---|
| Go lockfile in `WORKLOAD_LOCKFILES` | `go.mod` (with `go.sum` required beside it) | Empirical check: `osv-scanner 2.6.0` in workbench exits `127` on `-L go.sum`, `0`/`1` on `-L go.mod` (2026-11-24) |
| TypeScript native test execution | `node --test` on Node 24 | Empirical check: `node v24.21.0` in workbench runs `.ts` tests natively (2026-11-24) |
| TypeScript ESLint parser + OSV overrides | `@typescript-eslint/parser`, `typescript`, `minimatch >=11.0.1`, `qs >=6.16.1` | Empirical check in workbench: `eslint` parses `.ts`, `stryker` kills `.ts` mutants, `osv-scanner` finds `0` CVEs (2026-11-24) |
| Poetry in-project `.venv` via `uv run` | `uv run --no-project --with poetry env -u VIRTUAL_ENV POETRY_VIRTUALENVS_IN_PROJECT=true python -m poetry install` + `.venv/bin/python -m pytest` | Empirical check in workbench: `uv run` exports `VIRTUAL_ENV` to its cache env, so `env -u VIRTUAL_ENV` is needed for Poetry to create `.venv` usable across Cosmic-Ray shards (2026-11-24) |
| Mutant timeout | `10x` clean suite, floor `1s` | Decision consistent with ADR 0015 §3 and `stacks/{python,javascript}/runners/mutation.sh` |
