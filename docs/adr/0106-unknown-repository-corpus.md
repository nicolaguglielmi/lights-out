# ADR 0106 — Unknown-repository verification corpus and nightly workflow

- Status: accepted
- Date: 2026-11-24
- Milestone: M70

## Context

`lights-out init` (`core/init.py`, `core/init_plane.py`, `core/stacks.py`) drafts contracts (`workload.env`, `worker.md`, `policy.env`, `runners/`, and when a `Containerfile` or `Dockerfile` exists at the repository root, `compose.yaml`, `flags/flags.json`, `smoke.sh`, and `load.sh`) for external repositories across Python (`uv`, `poetry`, pinned `requirements.txt`), JavaScript (`npm`), TypeScript (`npm` + `tsconfig.json`), and Go (`go.mod` + `go.sum`).

Individual unit tests in `core/tests/test_init.py`, `core/tests/test_plane_init.py`, and `core/tests/test_stacks.py` exercise isolated functions, and `ci/init.sh` tests two Python/JS shapes and key parsing. Without an end-to-end verification corpus spanning all supported stacks, container port configurations, stdlib `unittest` fallback, and negative refusal cases, regressions across the `init` -> `ci/workloads.sh` -> `core/light.py` pipeline can slip through unnoticed.

At the same time, `./go ci` must stay cold, engine-free, offline, and fast (within a few seconds for the corpus check), while a nightly GitHub Actions workflow can exercise full live tool execution (`CORPUS_FULL=1`) on the workbench.

## Decision

### 1. Corpus Repository Catalog (`core/corpus.py`)

`core/corpus.py` (standard library only, strictly vendor-free per `ci/vendor-free.sh`, app-free per `ci/app-free.sh` — never hardcoding `.lights-out`, `workloads/`, `app/`, or `.attached` — and model-free per `ci/model-free.sh`) deterministically builds isolated temporary git repositories covering 12 distinct shapes (8 positive, 4 negative):

#### Positive shapes (must draft cleanly, pass `core.light`, and pass `ci/workloads.sh`)

1. **`python-uv-http`**: Python `uv` HTTP service (`pyproject.toml`, `uv.lock` with `pytest`, `Containerfile` with `EXPOSE 8000` and `HEALTHCHECK CMD curl -f http://localhost:8000/healthz`). Expects `stack=python`, gates + plane files drafted, `WORKLOAD_HEALTH_PROBE=http:/healthz`, and `WORKLOAD_PORT` omitted (default `8000` per ADR 0100).
2. **`python-uv-lib`**: Python `uv` library (`pyproject.toml`, `uv.lock` with `pytest`, no `Containerfile` or `Dockerfile`). Expects `stack=python`, gates-only draft (`compose.yaml` absent, empty plane keys).
3. **`python-requirements`**: Python pinned `requirements.txt` package (`==` exact pins, no `pyproject.toml` or `uv.lock`). Expects `stack=python`, `WORKLOAD_LOCKFILES=requirements.txt`, `TEST_CMD` using `--with-requirements requirements.txt`, gates-only draft.
4. **`python-poetry-lib`**: Python `poetry` library (`pyproject.toml`, `poetry.lock`, no `uv.lock`). Expects `stack=python`, `WORKLOAD_LOCKFILES=poetry.lock`, gates-only draft.
5. **`javascript-npm-lib`**: JavaScript `npm` library (`package.json` with `scripts.test`, `package-lock.json`). Expects `stack=javascript`, `WORKLOAD_LOCKFILES=package-lock.json`, `TOOLCHAIN_IMAGE=workbench`, gates-only draft.
6. **`typescript-npm-lib`**: TypeScript `npm` + `tsconfig.json` library (`package.json` without `scripts.test`, `tsconfig.json`, `package-lock.json`). Expects `stack=typescript`, `WORKLOAD_LOCKFILES=package-lock.json`, `TEST_CMD=npm ci && node --test`, gates-only draft.
7. **`go-http`**: Go `go.mod` + `go.sum` HTTP service (`pkg/calc/`, `cmd/server/`, `Dockerfile` with `EXPOSE 8080`). Expects `stack=go`, `TOOLCHAIN_IMAGE=docker.io/library/golang:1.24`, `WORKLOAD_LOCKFILES=go.mod`, gates + plane files drafted, `WORKLOAD_HEALTH_PROBE=tcp:8080`, and `WORKLOAD_PORT=8080`.
8. **`python-plain-http`**: Uninstrumented stdlib Python HTTP container (`pyproject.toml`, `uv.lock` without `pytest`, `Containerfile` with `EXPOSE 5000` and no `HEALTHCHECK`). Expects `stack=python`, `TEST_CMD` falling back to `python3 -m unittest discover`, gates + plane files drafted, `WORKLOAD_HEALTH_PROBE=tcp:5000`, and `WORKLOAD_PORT=5000`.

#### Negative shapes (must fail inference deterministically with `Undraftable` naming what is missing)

9. **`neg-unpinned-requirements`**: `requirements.txt` containing unpinned or range-pinned dependencies (`flask>=2.0`, `requests`). Expects deterministic `Undraftable` naming `requirements.txt`, `unpinned`, `flask`, and `==`, with no `workload.env` written.
10. **`neg-pyproject-no-uv-lock`**: `pyproject.toml` without `uv.lock` (or `poetry.lock`/`requirements.txt`). Expects deterministic `Undraftable` naming `pyproject.toml` and `uv.lock`.
11. **`neg-unknown-language`**: Repository with no supported root manifest (`README.md` and `Gemfile` only). Expects deterministic `Undraftable` naming `no stack found`, `pyproject.toml`, `go.mod`, and `package.json`.
12. **`neg-go-no-sum`**: `go.mod` without `go.sum` beside it. Expects deterministic `Undraftable` naming `go.mod`, `go.sum`, and `go mod tidy`.

### 2. Execution Engine and Structured Report (`core/corpus.py`)

- `core/corpus.py` accepts `--contract-rel <rel>` (default `.` so `core/` never hardcodes `.lights-out`; `ci/checks/corpus.py` passes `--contract-rel .lights-out`), `--stacks <dir>`, `--report <path>` (default `.crew/corpus/report.json`), `--no-light`, and `--full` (or `CORPUS_FULL=1`).
- For each positive shape, `core.corpus.run()` builds an isolated temporary git repository at commit `c1`, invokes `core/init.py` `write_draft()`, verifies source files in `c1` are untouched, checks every drafted file and contract key against expectations, invokes optional verification callbacks (e.g. `ci/workloads.sh` in `ci/checks/corpus.py`), commits the drafted contract at `c2`, commits a valid feature + test change at `c3`, and invokes `core/light.py` `judge()` over `c2..c3`.
- Writes a structured JSON report (`schema: "lights-out.corpus/1"`, `mode`, `contract_rel`, `passed`, `failed`, `total`, `duration_ms`, `shapes[]`) to `--report` and returns `(passed, total, details)`.

### 3. Two Execution Modes: Fast Cold Check vs `CORPUS_FULL=1` Nightly Mode

- **Fast engine-free mode (default; `./go ci`, `./go ci-corpus`, `./go corpus`)**:
  - Offline, no container engine socket required, no PyPI/npm downloads.
  - Runs `init.write_draft()`, `ci/workloads.sh` (including `docker-compose config` validation for containerized shapes), `core.light` (`risk` and `test-weakening`), and the negative shape checks, finishing in a few seconds so `./go ci` stays fast.
- **Full live nightly mode (`CORPUS_FULL=1`)**:
  - Runs everything in fast mode plus live execution in workbench where supported:
    - Refreshes `uv.lock` via `uv lock` for `pytest`-backed Python shapes so locked runs resolve real dependencies.
    - Runs `core.light` with `TEST_ON_HOST=1` (exercising real `TEST_CMD` and `test-proof`) for workbench-compatible stacks (`python-uv-http`, `python-uv-lib`, `python-requirements`, `javascript-npm-lib`, `typescript-npm-lib`, `python-plain-http`).
    - Runs `SAST_CMD` (Python `sast.sh`) and `osv-scanner` (`-L <lockfile>`, treating exit `0` and `128` as clean per `core/gates/deps.py`) on lockfiles.
    - Honestly records external-toolchain targets that require a container engine image (`go-http` with `docker.io/library/golang:1.24`, lacking `go` inside workbench) as skipped for host execution rather than mocking them.

### 4. CI Check, 1-Line Contract, and Self-Test (`ci/checks/corpus.py`, `ci/corpus.sh`, `Makefile`, `go`)

- `ci/checks/corpus.py` (ADR 0094) drives `core.corpus.run(contract_rel=".lights-out", ...)` with a callback verifying every positive drafted contract against `ci/workloads.sh`.
- Includes an inline planted-defect self-test proving both `core.corpus.run()` and `ci/workloads.sh` fail (`NO-GO`) when given a broken shape or malformed `workload.env`.
- `ci/corpus.sh` wraps `verdict ci.checks.corpus`, honoring `ci/contract.sh`:
  - Exit `0`: `GO: corpus — <passed>/<total> repository shapes verified`
  - Exit `1`: `NO-GO: corpus — <reason>` (with details on stderr).
- Wired into `Makefile` (`corpus` target with `##` help comment, and `corpus` added to `CI_CHECKS` right before `contract`) and `./go` (help line + `CORPUS_FULL` forwarding).

### 5. Nightly Workflow (`.github/workflows/corpus.yml`)

- Triggered on `schedule` (`cron: '41 3 * * *'`, daily off the hour) and `workflow_dispatch`.
- Guarded by `if: github.server_url == 'https://github.com'` (ADR 0039), `permissions: contents: read`.
- Uses SHA-pinned `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`.
- Runs `./go ci-corpus` (fast contract check) and `CORPUS_FULL=1 ./go ci-corpus` (live workbench init + gates corpus check), and uploads `.crew/corpus/report.json` with SHA-pinned `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2`.
- Validated cleanly with `actionlint` via `ci/lint.sh`.

## Alternatives rejected

- **Checking committed static fixture directories instead of generating temporary git repositories**: Inflates the repository with duplicate boilerplate and triggers `ci/workloads.sh` / `.lights-out/` lockfile clutter. Generating isolated temporary git repositories keeps `core/corpus.py` self-contained and guarantees every repository starts from a clean git history.
- **Hardcoding `.lights-out` inside `core/corpus.py`**: Violates `ci/app-free.sh` and couples `core/corpus.py` to the contract directory literal. Passing `--contract-rel .lights-out` from `ci/checks/corpus.py` keeps `core/` 100% app-free.
- **Running live `uv`/`npm`/`docker` downloads in default `./go ci`**: Slows every contributor `./go ci` run and breaks offline verification. Splitting fast engine-free verification (`./go ci`) from `CORPUS_FULL=1` nightly verification preserves fast cold CI while still testing live toolchains nightly.

## Consequences

- Every `./go ci` run verifies all 12 positive and negative repository shapes end-to-end across `init`, `init_plane`, `stacks`, `light`, and `workloads` in a few seconds with zero network or engine dependency.
- Nightly runs (`corpus.yml`) additionally verify live `uv`, `pytest`, `unittest`, `npm`, `node --test`, `bandit`, and `osv-scanner` execution and archive `.crew/corpus/report.json` as an artifact.
- Adding a new stack or container inference rule in the future has one structured place to add positive and negative shapes.

## Golden-rule check

`git clone && ./go demo` is unchanged and requires no account or API key. `./go ci` remains cold, offline, and engine-free, adding only a few seconds for the 12 synthetic corpus shapes.

## Provenance

| Item | Value | Provenance |
|---|---|---|
| Positive corpus shapes | `8` (`uv` HTTP, `uv` lib, `requirements.txt`, `poetry.lock`, JS `npm`, TS `npm`, Go HTTP, plain Python `unittest` HTTP) | Covers all stacks in `core/init.py`, `core/init_plane.py`, and `core/stacks.py` (ADR 0042, 0100, 0102) |
| Negative corpus shapes | `4` (unpinned `requirements.txt`, `pyproject.toml` without `uv.lock`, unknown language, `go.mod` without `go.sum`) | Covers deterministic `Undraftable` error contracts across `core/init.py` and `core/stacks.py` |
| Report schema | `lights-out.corpus/1` at `.crew/corpus/report.json` | Decision: structured machine-readable summary for nightly artifact upload |
| Nightly cron schedule | `41 3 * * *` | Decision: daily off-the-hour UTC schedule, avoiding collision with `nightly-github.yml` (`23 3 * * *`) |
| `actions/checkout` pin | `3d3c42e5aac5ba805825da76410c181273ba90b1` (`v7.0.1`) | Matches `.github/workflows/ci.yml`, `nightly-github.yml`, and `package.yml` |
| `actions/upload-artifact` pin | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` (`v7.0.2`) | Matches `action.yml`, `.github/workflows/package.yml`, `stranger.yml`, `record.yml`, and `site.yml` |

## Tests

Written from this ADR before implementation:
- `core/tests/test_corpus.py` (strictly vendor-free, app-free, and model-free): verifies `core.corpus.run()` builds isolated git repositories across all >= 8 shapes (including all 7 core positive shapes + Poetry + >= 3 negative shapes), executes `init.write_draft()`, optional `on_draft` callbacks, and `core.light`, validates the `lights-out.corpus/1` report JSON written to `--report`, returns `(passed, total, details)`, and detects regressions when a shape fails or an expected `Undraftable` error is missing.
- `ci/tests/test_corpus_workflow.py`: verifies `ci/corpus.sh` obeys the 1-line `ci/contract.sh` output contract (`GO: corpus — <passed>/<total> repository shapes verified`), proves planted defects fail `NO-GO: corpus — ...`, integrates drafted contracts with `ci/workloads.sh`, verifies `Makefile` and `go` wiring, and validates `.github/workflows/corpus.yml` triggers, `github.server_url == 'https://github.com'` guard, SHA-pinned actions, and `actionlint` cleanliness.
