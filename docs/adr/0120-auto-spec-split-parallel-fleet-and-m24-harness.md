# ADR 0120 — Autonomous Spec-Split Network, Bounded Parallel Fleet, and Deterministic M24 Benchmark Harness

- Status: accepted (extends [ADR 0036](0036-the-dev-worker-step-as-a-network.md) §2–§6, [ADR 0053](0053-perimeters-never-overlap.md) §5, [ADR 0088](0088-the-provider-scoreboard.md) §2–§9, [ADR 0090](0090-the-intake-interview-before-the-ticket.md) §3–§5, [ADR 0109](0109-continuous-orchestration-auto-incident-and-alert-webhook.md) §3, and [ADR 0119](0119-epics-dag-and-multi-service-monorepos.md) §2)
- Date: 2026-11-25
- Milestone: M80 (`swarm/m80-spec-split-fleet`)

## Context

Three gaps remained before `lights-out` could autonomously fan out ticket implementation and tests across disjoint workers, process multiple disjoint tickets concurrently through the control plane, and evaluate multi-provider benchmark runs deterministically:

1. **Manual `## Network` authoring**: `adapters/network/run.sh` (ADR 0036) already isolates `impl` and `tests` members in separate clones and enforces per-member perimeter checks, but tickets still required hand-written `## Network` sections. Neither `core/intake.py` nor `adapters/network/run.sh` could partition a ticket's `## Perimeter` into disjoint `impl` and `tests` sub-perimeters automatically, nor validate `network` split hints while preserving L0 replay fixtures.
2. **Sequential fleet execution and unserialized canary writes**: `core/runner.py` (ADR 0109) drained one queued event at a time sequentially. Running multiple disjoint tickets simultaneously requires bounded parallel workers in `core/runner.py` while strictly serializing same-ticket events and fleet-wide `merge`, `mainline`, and `release` steps, backed by a kernel-enforced `ReleaseTrainLock` in `core/releases.py` so concurrent releases never clobber `RELEASE_RECORD` (`.crew/release.json`) or `RELEASES_LOG` (`.crew/releases.jsonl`).
3. **Manual M24 evaluation**: `docs/scoreboard/m24-tenacity.json` and `core/scoreboard.py` (ADR 0088) render HTML and summary lines, but lacked an automated evaluator checking the `docs/PLAN.md` §14 exit criterion across providers and a validated logbook-to-dataset ingestion tool that rejects missing provenance or replayed-only runs without fabricating missing provider results.

## Decision

### 1. Autonomous `## Network` Spec-Split (`core/spec_split.py`, `core/spec_split.sh`, `core/intake.py`, `roles/architect/perimeter.md`, `adapters/network/run.sh`)

- **Stdlib-only module `core/spec_split.py`** (wrapped by thin POSIX script `core/spec_split.sh` under `ci/shell-boundary.sh`'s `30`-line limit):
  - `classify_path(path)` partitions paths and globs into `"tests"` (matching `tests/*`, `*/tests/*`, `test_*`, `*_test.*`, `*.spec.*`, `*.test.*`, `*Test.*`, `*Tests.*`, `*_spec.*`, `test/*`, `*/test/*`) vs `"impl"` (application code and documentation).
  - `propose_network_split(perimeter, criteria=None, max_members=MAX_NETWORK_MEMBERS)` requires at least `MIN_SPLITTABLE_PATHS = 2` distinct perimeter entries (`1` non-empty `impl` set and `1` non-empty `tests` set), `<= MAX_NETWORK_MEMBERS` (`4`, per ADR 0036 §2), `100%` coverage of the ticket's declared `perimeter`, and zero pairwise glob overlap using `fnmatchcase` and `core.assign.overlaps` matching `adapters/network/run.sh`. Returns `None` cleanly when unsplittable (for example, impl-only, tests-only, or overlapping globs such as `src/*` alongside `src/tests/test_a.py`).
  - `render_network_section(members)` and `inject_network_section(markdown, members=None)` format `## Network` (`### impl`, `### tests`, brief, and `- \`path\`` lines) compatible with `tickets/0012.md` and `adapters/network/run.sh`, preserving any pre-existing `## Network` section unchanged.
  - Exposes CLI subcommands `check <ticket.md> [--json]`, `split <ticket.md> [--in-place] [--json]`, and `compose <proposal.json>`.
- **Backward-compatible intake and architect prompt**:
  - `roles/architect/perimeter.md` documents optional disjoint `impl` vs `tests` split hints while preserving `propose_perimeter(ticket, paths)` untouched for ADK replay compatibility (`ci/evals.sh`, `ci/prompts.sh`, `ci/model-free.sh`).
  - `core/intake.py` computes/validates and renders `## Network` in `compose()`, `perimeter()`, and `perimeter_decided()` only when `ticket.get("network")` is explicitly present or `INTAKE_NETWORK_SPLIT=on` (default `off`, leaving L0 replay fixtures byte-identical).
- **Opt-in network adapter auto-split**:
  - `adapters/network/run.sh` preserves default `${NETWORK_AUTO_SPLIT:-off}` behavior unchanged (handing a ticket without `## Network` to the member harness as a single worker, ADR 0036 §5 / `ci/network.sh`). When `NETWORK_AUTO_SPLIT=on` and `$TICKET_FILE` lacks `## Network`, it runs `python3 core/spec_split.py split "$TICKET_FILE"` into `$OUT/split-spec.md` (failing cleanly with exit `1` if unsplittable).

### 2. Bounded Parallel Fleet & Release-Train Lock (`core/releases.py`, `core/runner.py`)

- **Release-train lock (`core/releases.py`)**:
  - Add `ReleaseTrainLock` using non-blocking `fcntl.flock(LOCK_EX | LOCK_NB)` polling on `RELEASE_TRAIN_LOCK` (default `<dir of RELEASE_RECORD>/release-train.lock`) with bounded timeout `RELEASE_TRAIN_TIMEOUT_S` (default `900` seconds, raising `ReleaseTrainTimeout` on timeout).
  - Guard `record()` (which also covers `running`) and `answer()` with `ReleaseTrainLock`, honoring both thread-local re-entrancy and the `LIGHTS_OUT_TRAIN_HELD=1` environment guard so nested subprocess invocations from a lock-holding parent never self-deadlock.
  - Add CLI subcommand `python3 core/releases.py train <cmd> [args...]` that holds `ReleaseTrainLock`, exports `LIGHTS_OUT_TRAIN_HELD=1`, executes `<cmd>`, and preserves its exact exit code.
- **Bounded parallel fleet (`core/runner.py`)**:
  - Add `--workers <N>` and `RUNNER_WORKERS` (default `1`, validated within `1..MAX_RUNNER_WORKERS` where `MAX_RUNNER_WORKERS = 8`).
  - When `workers == 1`, execute the exact sequential drain loop unchanged (`ci/tests` and `core/tests` parity).
  - When `workers > 1`, pull batches of up to `workers` messages and execute via bounded `ThreadPoolExecutor(max_workers=workers)` while strictly serializing: (a) events sharing the same `ticket` (grouped in FIFO order and guarded by a per-ticket lock so a ticket never races itself), and (b) `SERIAL_STEPS = ("merge", "mainline", "release")` across the entire fleet under a fleet serialization lock and `ReleaseTrainLock` (with `LIGHTS_OUT_TRAIN_HELD=1` passed to step subprocesses) so concurrent `main-green` merges queue deterministically into canary verification without clobbering `RELEASE_RECORD`.

### 3. Deterministic M24 Benchmark Harness (`core/m24_runner.py`, `core/m24_runner.sh`)

- **Stdlib-only module `core/m24_runner.py`** (wrapped by thin POSIX script `core/m24_runner.sh`):
  - `evaluate` inspects `docs/scoreboard/m24-tenacity.json` plus optional `--logbook` inputs (reusing `core/scoreboard.py` validation and logbook parsing) against `docs/PLAN.md` §14 (`>= M24_PASS_NUMERATOR/3` = `2/3` auto-mergeable PRs without human code edits across at least `M24_MIN_PROVIDERS = 3` providers). Emits deterministic JSON (`--json`) and human text verdicts: `"pass"` (exit `0`), `"fail"` (exit `1`), or `"incomplete"` (exit `3`, honestly listing `not_run` providers without fabricating runs).
  - `ingest-logbook` validates real run logbooks (requiring non-replayed model tasks, valid `lights-out/logbook/1.x` schema, non-empty git commit SHA, and explicit `source.run` provenance) and emits/merges `lights-out/scoreboard-data/1` rows validated by `core.scoreboard.load_dataset`.

## Alternatives rejected

- **Enabling `INTAKE_NETWORK_SPLIT=on` or `NETWORK_AUTO_SPLIT=on` by default**: Rejected because existing L0 replay fixtures and `ci/network.sh` / `ci/intake.sh` contracts require single-worker fallback when `## Network` is absent and byte-identical intake proposals by default.
- **Replacing `propose_perimeter(ticket, paths)` with a different ADK tool signature**: Rejected because `ci/evals.sh` enforces `architect` having exact toolset `{"find_files", "propose_perimeter"}` and existing L0 architect replay recordings use `(ticket, paths)`.
- **Blocking `fcntl.flock(LOCK_EX)` without a timeout in `ReleaseTrainLock`**: Rejected because a stuck release worker would hang the fleet indefinitely without surfacing a diagnostic timeout.
- **Unbounded thread spawning or per-step process locks without per-ticket ordering**: Rejected because same-ticket concurrent events would race state transitions and unbounded worker pools could exhaust host memory or container limits.
- **Generating synthetic rows for unexecuted providers in `docs/scoreboard/m24-tenacity.json`**: Rejected by CLAUDE.md and ADR 0088 ("a number with no source does not enter"); `m24_runner.py` reports `incomplete` honestly until real runs exist.

## Consequences

- Tickets with separable implementation/doc and test perimeters can be auto-split deterministically at intake or network-harness execution time with zero changes to existing L0 golden tests when opt-in flags stay `off`.
- `core/runner.py --workers N` safely runs disjoint tickets concurrently (`1..8` workers) while serializing same-ticket steps and fleet `merge`/`mainline`/`release` critical sections under `ReleaseTrainLock`.
- `core/m24_runner.py` provides reproducible verification and logbook ingestion for M24 without touching `docs/scoreboard/m24-tenacity.json`'s truthful `not_run` state.
- Wave B (`integrator-4`) remains free to wire `./go` / `Makefile` / `core/cli.py` / `ROADMAP.md` cleanly without merge conflicts.

## Provenance

| Numeric threshold / parameter | Value | Provenance |
|---|---|---|
| `MAX_NETWORK_MEMBERS` | `4` | External/decision from [ADR 0036](0036-the-dev-worker-step-as-a-network.md) §2 ("The number": free tier `~250` requests/day divided by `MAX_TURNS=60` in `roles/dev-worker/role.env` is `4.2`, floored to `4`). |
| `MIN_SPLITTABLE_PATHS` | `2` | Decision: at least `1` `impl`/doc path and `1` `tests` path are necessary to form two non-empty disjoint member perimeters. |
| `DEFAULT_WORKERS` / `MAX_RUNNER_WORKERS` | `1` / `8` (`1..8`) | Decision: default `1` preserves sequential L0 `core/runner.py` parity ([ADR 0109](0109-continuous-orchestration-auto-incident-and-alert-webhook.md)); cap `8` matches the `8` PR gate fanout and bounds concurrent git worktrees/subprocesses on a single host. |
| `RELEASE_TRAIN_TIMEOUT_S` | `900` seconds | External/decision: `15 * STAGE_SECONDS` (`60` s default in `core/launchpad.sh`) matching `docs/PLAN.md` §2 `900` s demo/release ceiling and `TIMEOUT_S=900`. |
| `RELEASE_TRAIN_POLL_S` | `0.05` seconds | Decision: `50` ms non-blocking `flock` poll cadence avoids tight busy-wait while waking promptly when a concurrent release finishes. |
| `M24_MIN_PROVIDERS` | `3` | External: `docs/PLAN.md` §14 and M24 require running the benchmark ticket suite across three independent model providers. |
| `M24_PASS_NUMERATOR` / denominator | `2` / `3` (`>= 2/3`) | External: `docs/PLAN.md` §14 exit criterion requires at least two out of three auto-mergeable PRs without human code edits. |
| Shell wrapper code line ceiling | `<= 20` (under `30` limit) | External/decision: [ADR 0052](0052-where-shell-is-allowed.md) §4 and `ci/shell-boundary.sh` enforce a `30`-line cap; wrappers `core/spec_split.sh` and `core/m24_runner.sh` target `<= 20` lines. |

## Golden-rule check

`git clone && ./go demo` and `./go ci` remain unchanged: `INTAKE_NETWORK_SPLIT`, `NETWORK_AUTO_SPLIT`, and `RUNNER_WORKERS` default to `off`/`off`/`1`, `docs/scoreboard/m24-tenacity.json` remains truthful without fabricated runs, and all new modules use Python standard library only and pass `ci/vendor-free.sh`, `ci/app-free.sh`, `ci/model-free.sh`, and `ci/shell-boundary.sh`.
