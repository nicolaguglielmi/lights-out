# ADR 0118 — Greenfield `0→1` App Builder (`genesis`)

- Status: proposed
- Date: 2026-11-24
- Milestone: M78 (Wave 3)
- Builds on: ADR 0042 (init and workload contract), ADR 0052 (shell boundary), ADR 0055 (ticket spec gate), ADR 0061 (light-tier gates), ADR 0090 (intake interview), ADR 0100 (plane half draft), ADR 0102 (polyglot stacks), ADR 0110 (workspace and container plane), ADR 0113 (zero-copy `init --apply` and incremental baseline), ADR 0115 (autonomous ADR lifecycle)

## Context

`lights-out init` (`core/init.py`) and `lights-out intake` (`core/intake.py`) onboard existing repositories that already have source files, package manifests, lockfiles, and tests. Starting from a blank directory or empty git repository (`0→1` greenfield creation) still requires manual scaffolding before the platform's delivery loop can operate:

1. **No architectural baseline at day zero.** A new repository has no `docs/adr/0001-initial-architecture.md` recording the stack, HTTP surface, container packaging, and numeric threshold provenance required by ADR 0115.
2. **No runnable minimal workload or contract.** Without a minimal manifest, lockfile, `/healthz` + `/metrics` HTTP server, `Containerfile`, unit test suite, and `.lights-out/` contract (`workload.env`, `compose.yaml`, `smoke.sh`, `load.sh`, `flags/flags.json`, `runners/`), neither `core.init.write_draft` nor `core.light` / `gates` can execute.
3. **No ordered starter backlog.** Without testable `Given ... when ... then ...` starter tickets in `tickets/0001.md`..`tickets/NNNN.md` carrying explicit `## Perimeter` globs, `dev-worker` has no gated work queue.

Constraints preserved:

- `core/` stays 100% Python stdlib-only, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh` — `core/genesis.py` never hardcodes `.lights-out` or workload-specific names, receiving or assembling `contract_rel` cleanly), and model-free (`ci/model-free.sh`).
- `core/genesis.sh` stays strictly under the shell boundary (`<= 25` non-blank/non-comment code lines, within `ci/shell-boundary.sh`'s 30-line ceiling).
- Six agent roles remain fixed (`ci/roles.sh`), and `roles/architect/genesis.md` obeys every prompt hygiene rule (`ci/prompts.sh`).
- Wave B entrypoints (`Makefile`, `./go`, `lights-out`, `core/cli.py`) are wired separately after Wave A.

## Decision

### 1. `core/genesis.py` (stdlib only)

Expose `create_greenfield_app(repo_dir, brief, *, stack="python", name=None, port=8000, tickets_count=3, commit=True, allow_existing_empty=True, today=None)` and a CLI (`python3 core/genesis.py [--repo <dir>] (--brief <text> | --brief-file <path>) [--stack python|javascript|typescript|go|rust|java] [--name <slug>] [--port <n>] [--tickets-count <n>] [--no-commit] [--json]`):

1. **Safety, secret scrubbing, and cleanliness checks.**
   - Refuse empty/whitespace `brief` with `GenesisError(ValueError)`.
   - Scrub high-signal secret patterns (PEM private key blocks, `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`/`glpat-`/`AKIA`/`sk_live_`/`sk_test_`/`sk-`/`xox*-` tokens, `*_KEY=`/`*_TOKEN=`/`*_SECRET=`/`*_PASSWORD=` assignments, `Authorization: Bearer|Basic` headers, and URL `user:pass@` credentials) from `brief` and metadata into `<REDACTED>` before deriving names or writing any file.
   - Validate `stack` against the six platform stacks (`python`, `javascript`, `typescript`, `go`, `rust`, `java`) and validate `port` as an integer (not `bool`) in `1024..65535` (default `8000`) and `tickets_count` in `1..10` (default `3`).
   - Validate `repo_dir`:
     - If `repo_dir` does not exist, create it and run `git init -q -b main`.
     - If `repo_dir` is an existing non-git directory (including a subdirectory inside an outer git repository without its own `.git`), refuse unless completely empty and `allow_existing_empty=True` (in which case initialize `git init -q -b main` cleanly).
     - If `repo_dir` is a git repository, refuse if `allow_existing_empty=False` when any file or commit exists, or if any working-tree entry, staged path, tracked path, or historical commit touches a file outside root-level `README*`, `LICENSE*`, and `.gitignore` (e.g. refusing existing source files, manifests, `.env`, or subdirectories). Existing clean `README*`, `LICENSE*`, and `.gitignore` files are preserved.

2. **Synthesize `docs/adr/0001-initial-architecture.md`.**
   - Render `docs/adr/0001-initial-architecture.md` containing Context (referencing the scrubbed brief as derived input), Decision (chosen stack, module layout, HTTP endpoints `/healthz`, `/metrics`, and brief-derived domain endpoint, `Containerfile` `EXPOSE`/`HEALTHCHECK`, zero external unpinned dependencies), Alternatives considered and rejected, Consequences, and a `## Provenance` table covering every numeric threshold (including any brief-derived numeric constraint).
   - Validate the rendered record with `core.adr_gov.validate_adr(...) == []` before writing.

3. **Synthesize ordered starter backlog (`tickets/0001.md`..`tickets/NNNN.md`).**
   - Generate `tickets_count` (default `3`) ordered Markdown tickets derived from the scrubbed brief and normalized service slug (`1..48` characters matching `^[a-z][a-z0-9-]*$`).
   - Each ticket carries `- Status: open`, `- Feature: <slug>`, `- Priority: P0..P2`, `## Problem`, `## Acceptance criteria` with concrete `Given ... when ... then ...` paragraphs free of `core.spec.VAGUE` terms, and `## Perimeter` globs matching the generated scaffold.
   - Validate every generated ticket with `core.spec.check(...) is None` (equivalent to `gates/spec.sh`).

4. **Scaffold runnable minimal service for all six stacks.**
   - **`python` (default):** generates `<pkg>/__init__.py`, `<pkg>/server.py` (stdlib `http.server.ThreadingHTTPServer` binding `os.environ.get("HOST", "127.0.0.1")` to satisfy SAST checks while `Containerfile` sets `ENV HOST=0.0.0.0`, serving `/healthz` returning `200`, `/metrics` in Prometheus text format, and a brief-derived domain endpoint with validation and `400`/`404`/`405` responses), `pyproject.toml` + valid dependency-free `uv.lock` (`source = { virtual = "." }`), `Containerfile` (`AS runtime` + `AS test`, `EXPOSE <port>`, `HEALTHCHECK`), and `unittest` tests (`tests/test_*.py`) with branch and value assertions designed to kill mutants.
   - **`javascript`, `typescript`, `go`, `rust`, `java`:** each generates valid manifests/lockfiles (`package.json` + `package-lock.json`, `tsconfig.json`, `go.mod` + `go.sum`, `Cargo.toml` + `Cargo.lock`, `pom.xml`), runnable `/healthz` + `/metrics` + domain sources, unit tests, and `Containerfile` with `EXPOSE` and `HEALTHCHECK`, passing `core.init.infer(...)` cleanly.

5. **Draft & apply the contract and create clean two-commit history.**
   - Draft the contract directory using `core.init.write_draft(..., baseline_mode="incremental")` after writing the base service files (so `select_incremental_scope` records `MUTATION_LINES` provenance, and `core.init_plane` records `WORKLOAD_HEALTH_PROBE`, `WORKLOAD_PORT` when non-default, `compose.yaml`, `smoke.sh`, `load.sh`, `flags/flags.json`, and `runners/`) and apply atomically with `core.init_apply.apply_draft(..., commit=False)`.
   - When `commit=True`, stage explicit path lists (never `git add -A`) into:
     - **Base commit (`HEAD~1`):** `.gitignore`, `README.md`, `docs/adr/0001-initial-architecture.md`, `tickets/*.md`, `Containerfile`, manifest/lockfile, base `/healthz` + `/metrics` service and base tests, and the drafted contract directory;
     - **Feature commit (`HEAD`):** Ticket `0001` implementation (the brief-derived domain endpoint and its unit tests) strictly inside `tickets/0001.md`'s `## Perimeter`, so `core.light` (`TEST_ON_HOST=1`, `TICKET=tickets/0001.md`) and range gates evaluate `HEAD~1..HEAD` cleanly to `GO`.
   - When `commit=False`, write the complete service and contract into `repo_dir` without creating git commits.

### 2. `core/genesis.sh` and `roles/architect/genesis.md`

- `core/genesis.sh` is an executable POSIX shell wrapper (`<= 25` non-blank/non-comment code lines, passing `shellcheck -s sh` and `ci/shell-boundary.sh`) forwarding `REPO`, `BRIEF`, `BRIEF_FILE`, `STACK`, `SERVICE_NAME`, `PORT`, `TICKETS_COUNT`, `NO_COMMIT`, `JSON`, `GENESIS_ARGS`, and CLI arguments to `python3 core/genesis.py`.
- `roles/architect/genesis.md` provides the live `architect` role task prompt for greenfield architecture and starter ticket synthesis, adhering strictly to `ci/prompts.sh` and `ci/roles.sh`.

## Alternatives rejected

- **Requiring an LLM call inside `core/genesis.py` for offline `0→1` scaffolding.** Rejected: violates the golden L0 rule (`CLAUDE.md`) that local workflows must run cold and deterministically without API keys or network calls; live `architect` guidance is supplied cleanly via `roles/architect/genesis.md` when running at L1+.
- **Creating a single root commit when `commit=True`.** Rejected: evaluating `core.light` or range gates against the empty tree with Ticket `0001` (`## Perimeter` scoped to application sources and tests) would fail `perimeter` on platform/contract files and skip `test-proof` (`empty_base`). Splitting into a clean base commit (`HEAD~1`) plus the Ticket `0001` domain commit (`HEAD`) allows immediate verification with `GO` verdicts across `risk`, `perimeter`, `test-weakening`, `test`, `test-proof`, and `spec`.
- **Adding runtime framework dependencies (e.g. FastAPI or Express) to default scaffolds.** Rejected: requires network package resolution during cold L0 verification (`uv run --locked`, `npm ci`) and widens dependency CVE surface; standard-library HTTP servers work identically offline and online.
- **Using `git add -A` when committing generated files.** Rejected: risks sweeping stray files or ignored artifacts; explicit file lists match `core/init_apply.py` (ADR 0113).

## Consequences

- Any empty directory or clean seed repository can be turned into a tested, containerized, contract-backed workload with ADR `0001` and validated starter tickets in one deterministic command.
- The generated repository passes `core.init.infer`, `ci/workloads.sh`, `gates/spec.sh`, `core.adr_gov.validate_adr`, `gates/deps.sh`, and `core.light` (`HEAD~1..HEAD` -> `GO`) out of the box.
- Wave B can wire `Makefile`, `./go`, `lights-out`, and `core/cli.py` onto `core/genesis.sh` / `core/genesis.py` without modifying Wave A internals.

## Provenance

| Threshold / Constant | Value | Provenance |
|---|---|---|
| TCP `port` range | `1024..65535` (default `8000`) | External + Inherited: unprivileged IANA user/ephemeral port range (`>= 1024` avoids privileged system ports `1..1023`, `<= 65535` 16-bit TCP port ceiling); default `8000` matches `core/init_plane.py` (`DEFAULT_PORT = 8000`, ADR 0100). |
| `tickets_count` bounds | `1..10` (default `3`) | Derived + Decision: default `3` matches `roles/product/prompt.md` ("usually two or three" starter tickets); bounded to `1..10` to keep initial backlog concise and deterministic. |
| Service slug length | `1..48` characters | Decision: fits identifier and image-tag limits across all six ecosystems (Python package, npm `<= 214`, Go module path, Cargo crate, Maven artifactId, OCI tag `<= 128`). |
| `core/genesis.sh` code lines | `<= 25` lines (ceiling `30`) | Derived from `ci/shell-boundary.sh` (`limit=30`, ADR 0052 §4 and ADR 0084 §5), keeping a 5-line safety margin under the 30-line ceiling. |
| Incremental baseline `max_files` | `10` | Inherited from ADR 0113 (`core/init.py` / `core/init_apply.py` `select_incremental_scope` default). |
| Secret token prefix widths | `20`, `16`, `10` | Inherited from ADR 0113 (`core/init_apply.py` Provenance: `gh*`/`glpat-`/`sk-` `>= 20`, `AKIA`/`sk_live_`/`Bearer` `>= 16`, `xox*-` `>= 10`). |
| Supported stack count | `6` stacks | Measured from `stacks/` (`python`, `javascript`, `typescript`, `go`, `rust`, `java`, ADR 0102 / ADR 0110). |
| Agent role count | `6` roles | Inherited from `CLAUDE.md` and `ci/roles.sh` (`architect`, `dev-worker`, `ops-triage`, `product`, `release`, `test-reviewer`). |
| HTTP status codes (`200`, `201`, `400`, `404`, `405`) | `200`, `201`, `400`, `404`, `405` | External standard: HTTP Semantics RFC 9110 (`200 OK`, `201 Created`, `400 Bad Request`, `404 Not Found`, `405 Method Not Allowed`). |
| Base + initial feature commits | `2` commits (`HEAD~1..HEAD`) | Decision: establishes base contract + ADR commit at `HEAD~1` and Ticket `0001` feature commit at `HEAD` so `core.light` and range gates can prove `test-proof` and `perimeter` immediately. |

## Tests, from this ADR

- `core/tests/test_genesis.py` (app-free, vendor-free, model-free): tests safety/cleanliness checks (blank non-git init, non-empty non-git refusal, `allow_existing_empty=False`, existing `README`/`LICENSE`/`.gitignore` preservation, existing source/commit/history refusal, outer-git-repo refusal), secret scrubbing in briefs, `stack`/`port`/`tickets_count`/`name` validation, `docs/adr/0001-initial-architecture.md` validation under `core.adr_gov.validate_adr` (including briefs with threshold numbers), starter ticket generation passing `core.spec.check` (even with vague words in brief), all six stacks passing `core.init.infer`, contract generation with incremental baseline provenance, two-commit vs `--no-commit` behavior, and CLI flags (`--json`, `--brief-file`, `--no-commit`).
- `ci/tests/test_genesis_e2e.py`: end-to-end Podman tests running `core/genesis.py` and `core/genesis.sh`, validating `docs/adr/0001-*.md`, running `gates/spec.sh` on every generated ticket, running `ci/workloads.sh`, executing generated unit tests + live HTTP `/healthz`, `/metrics`, and domain endpoints, verifying mutant sensitivity, running `core.light` (`TEST_ON_HOST=1`) and individual gates (`risk`, `deps`, `test-weakening`, `test`, `test-proof`, `adr-check`, `secrets`) on `HEAD~1..HEAD` -> `GO`, and validating ADR 0118, `core/genesis.sh` shell boundary/shellcheck, and `roles/architect/genesis.md` prompt/role checks.
