# ADR 0113 — Zero-copy `init --apply` and incremental legacy mutation baseline

- Status: proposed
- Date: 2026-11-24
- Milestone: M73
- Extends: [ADR 0042](0042-gates-anywhere-init-and-the-toolchain-contract.md) §6 (`init`), [ADR 0015](0015-pr-gates-at-m4.md) §3 (`MUTATION_LINES`), [ADR 0037](0037-the-workload-is-a-repository-not-a-directory.md) §1, [ADR 0110](0110-stateful-monorepo-rust-java-and-js-managers.md)

## Context

`lights-out init` (`core/init.py`, `core/init.sh`) currently writes drafted contract files into a caller-supplied `--out` directory (or `$HOME/.local/state/lights-out/` via the standalone wrapper) and instructs the developer to copy `.lights-out/` into their repository and commit it manually. Two adoption bottlenecks slow onboarding on real repositories:

1. **Manual copy and commit friction.** Developers want an explicit opt-in `--apply-to <repo>` workflow (with optional `--branch`, `--commit`, and `--force`) that applies the drafted contract atomically into the target repository without silently overwriting tracked/modified files, leaking secrets (`.env`, private keys, tokens, passwords), or sweeping unrelated dirty files into a setup commit via `git add -A`. The same scoped-commit primitive is needed for ticket artifacts (`commit_ticket_artifact`).
2. **Full-scope mutation baseline cost on large legacy repositories.** Measuring `MUTATION_BASELINE` with `MUTATION_LINES=all` across 100k+ LOC codebases does not finish interactively during initial onboarding. Operators need an opt-in `--baseline-mode incremental` mode that deterministically selects a bounded number (`--baseline-max-files`, default `10`) of recently active source files inside `WORKLOAD_MUTATION_SCOPE`, emits a `MUTATION_LINES` spec compatible with every stack runner (`core/gates/mutation.py` wire format), and records explicit measurement provenance in `workload.env`.

Constraints preserved:

- `core/` stays stdlib-only, vendor-free (`ci/vendor-free.sh`), app-free (`ci/app-free.sh` — `core/init_apply.py` and `core/tests/test_init_apply.py` never spell `.lights-out`, receiving `contract_rel` dynamically), and model-free (`ci/model-free.sh`).
- Shell debt in `core/init.sh` and `core/mutation-baseline.sh` is untouched (`ci/shell-boundary.sh`).
- Default `core/init.py` behavior (`--repo`, `--out`, `--stacks`, `--set-baseline`) and `core/init.sh`'s one-line `STACK=<stack>` stdout contract remain unchanged.
- `workload.env` keeps strictly the twelve standard keys plus optional `WORKLOAD_HEALTH_PROBE` and `WORKLOAD_PORT` (`ci/workloads.sh`).

## Decision

### 1. `core/init_apply.py` (stdlib only, dynamic `contract_rel`)

Three cohesive primitives carry all filesystem, secret-guard, git-branch, scoped-commit, and incremental-scope logic:

1. **`apply_draft(draft_dir, target_repo, contract_rel, *, branch=None, commit=False, force=False)`**
   - Validates `draft_dir` and `target_repo` exist as directories, rejects empty drafts or symlinks in `draft_dir`, and verifies `contract_rel` is a non-empty relative path whose resolved target stays strictly inside `target_repo` (no absolute paths, `..` traversal, `$`, `#`, or symlink escape).
   - Scans every drafted file for secret risks before touching `target_repo`:
     - refuses `.env` and `.env.*` filenames as well as private-key filenames (`.pem`, `.key`, `.p12`, `.pfx`, `.jks`, `id_rsa`, `id_dsa`, `id_ecdsa`, `id_ed25519`, `cosign.key`);
     - refuses PEM private-key headers (`-----BEGIN ... PRIVATE KEY-----`) and high-confidence token prefixes (`gh[pousr]_`, `glpat-`, `AKIA`, `sk_live_`/`sk_test_`, `sk-`, `xox[baprs]-`, non-placeholder `Bearer `) anywhere in file contents;
     - refuses non-comment credential/token/key/password assignments (`KEY=VALUE` / `key: value`) with non-empty, non-placeholder values while permitting code identifiers and empty contract placeholders (such as `WORKLOAD_KILL_SWITCH=`).
   - Checks `<target_repo>/<contract_rel>` for existing files on disk or tracked/modified paths in git (`git ls-files` and `git status --porcelain`). If any overlapping file exists or is tracked/modified and `force=False`, refuses with `ApplyError` before switching branches or writing any file. When `force=True`, records the overwritten repo-relative paths in `overwritten`.
   - When `branch` is given, validates the ref name (`git check-ref-format --branch`), creates it (`git checkout -b <branch>`) if absent or switches to it (`git checkout <branch>`) if it already exists, checking target-branch collisions before switching.
   - Copies drafted files into a temporary directory on the destination filesystem, then atomically places each file via `os.replace` (rolling back on error), preserving executable permissions (`0o755` for executable scripts, `0o644` otherwise) without deleting unrelated existing files in `<target_repo>/<contract_rel>`.
   - When `commit=True`, stages only the applied `<contract_rel>/<file>` paths (`git add -- <paths>`) and creates a commit scoped exclusively to those paths (`git commit --only -m <msg> -- <paths>`), never running `git add -A` and leaving unrelated dirty or pre-staged files untouched.
   - Returns `{"applied_files": [...], "branch": <str|None>, "commit_sha": <str|None>, "overwritten": [...]}` with sorted repo-relative POSIX paths.

2. **`select_incremental_scope(repo_dir, scope, max_files=10)`**
   - Validates `max_files` is an integer `>= 1` (`ValueError` otherwise) and `scope` resolves inside `repo_dir`.
   - Collects eligible source files inside `scope`:
     - supported source extensions across all six platform stacks (`.py`, `.js`, `.mjs`, `.cjs`, `.jsx`, `.ts`, `.tsx`, `.mts`, `.cts`, `.go`, `.rs`, `.java`, `.kt`);
     - excludes test directories (`test`, `tests`, `__tests__`, `testdata`, `benches`, `examples`, `fixtures`), test file patterns (`test_*.py`, `*_test.*`, `*-test.*`, `*.test.*`, `*.spec.*`, `conftest.py`, `*Test.java`, `*Tests.java`, `*Spec.java`, `*Test.kt`, `*Tests.kt`, `*Spec.kt`), lockfiles/manifests, `.d.ts`, and hidden/build directories (`.git`, `node_modules`, `target`, `build`, `dist`, `out`, `.gradle`, `vendor`, `__pycache__`, `.venv`, `venv`, `.crew`).
   - Orders candidates deterministically: most-recently-touched existing eligible files from `git log --name-only --pretty=format: -- <scope>` first (newest commit first, preserving git's deterministic path order within a commit), then filling any remaining slots up to `max_files` with lexically sorted eligible paths inside `scope` (also serving as the complete fallback when git history is unavailable or empty).
   - Emits `IncrementalScope(mutation_lines, provenance)` (a 2-element `NamedTuple` unpacking cleanly as `(mutation_lines, provenance)`):
     - `mutation_lines`: space-separated `<file>=1-<line_count>` specs matching `core/gates/mutation.py`'s wire format (`core/gates/mutation.py` `groups()` and all six stack mutation runners parse `<file>=<start>-<end>` separated by spaces); `<line_count>` is the file's actual line count (minimum `1`), or `""` when no eligible source files exist;
     - `provenance`: `"incremental: top N recently changed files in <scope> (<files>)"` (or `"incremental: 0 eligible source files in <scope>"` when empty).

3. **`commit_ticket_artifact(repo_dir, artifact_path, message)`**
   - Validates `repo_dir` is a git repository, `message` is non-empty, and `artifact_path` is a relative path whose resolved target stays strictly inside `repo_dir` (rejecting absolute paths, `..` traversal, and escaping symlinks with `ValueError`) and exists on disk.
   - Runs the same secret scan on `artifact_path` before staging.
   - Stages and commits exclusively `artifact_path` (`git add -- <rel>` then `git commit --only -m <msg> -- <rel>`), leaving unrelated staged or unstaged worktree modifications untouched, and returns the new 40-character commit SHA (or `None` when `artifact_path` has no changes relative to `HEAD`).

### 2. `core/init.py` CLI extensions

`core/init.py` adds six optional flags while keeping `--repo`, `--out`, `--stacks`, `--commit`, `--today`, `--service`, and `--set-baseline` fully backward compatible:

- `--apply-to <repo>`: applies the drafted contract to `<repo>/.lights-out` via `init_apply.apply_draft`. When `--repo` is omitted with `--apply-to`, `--repo` defaults to `--apply-to`. When `--out` is omitted with `--apply-to`, drafts into a temporary directory so no intermediate artifact directory is left behind.
- `--branch <name>`: creates or switches to `<name>` during `--apply-to` (refused without `--apply-to`).
- `--commit`: creates a scoped git commit of the applied contract during `--apply-to` (refused without `--apply-to`).
- `--force`: permits overwriting existing/tracked files during `--apply-to` (refused without `--apply-to`).
- `--baseline-mode full|incremental` (default `full`): when `incremental`, `write_draft` calls `init_apply.select_incremental_scope(repo, scope, max_files=baseline_max_files)` and passes the result to `render_workload_env`, which records `# Provenance, measurement: incremental baseline scope (not yet measured); <provenance>; MUTATION_LINES=<spec>` directly above `MUTATION_BASELINE=`.
- `--baseline-max-files <int>` (default `10`): maximum source files selected in incremental mode; validated `>= 1`.

`core/init.py` preserves the single-line `STACK=<stack>` stdout output on success (`core/init.sh` compatibility) and prints apply/incremental details on stderr; refusal errors (`Undraftable`, `ApplyError`, `ValueError`) print one line on stdout and exit `1`.

## Alternatives rejected

- **Using `git add -A` inside `--apply-to --commit`.** Rejected: would sweep unrelated developer edits or untracked secrets in the target repository into the setup commit. Explicit path-scoped `git add -- <paths>` plus `git commit --only -- <paths>` isolates both unstaged and pre-staged unrelated files.
- **Emitting colon-separated `<file>:1-999999,...` in `MUTATION_LINES`.** Rejected: every mutation runner in `stacks/{python,javascript,typescript,go,rust,java}/runners/` and `core/gates/mutation.py` splits groups on whitespace and requires `=` (`<file>=<start>-<end>`); colon-separated groups would be silently ignored by the runners.
- **Adding a new `MUTATION_BASELINE_MODE=` or `MUTATION_LINES=` key to `workload.env`.** Rejected: `ci/workloads.sh` enforces the exact twelve-key contract plus optional plane port/probe keys; recording incremental scope in `# Provenance, measurement:` preserves 100% compatibility with `ci/workloads.sh`, `core/policy.py`, and `--set-baseline`.
- **Hardcoding `.lights-out` inside `core/init_apply.py`.** Rejected: `ci/app-free.sh` allows `.lights-out` only in allowlisted files (`core/init.py`, `core/tests/test_init.py`); passing `contract_rel` dynamically keeps `core/init_apply.py` reusable and app-free.

## Consequences

- Repositories can adopt `lights-out` in one command (`--apply-to`, `--branch`, `--commit`) without manual `cp -R` or overwriting existing contracts silently.
- Large legacy repositories can bound initial mutation baseline selection deterministically via `--baseline-mode incremental` with auditable provenance in `workload.env`.
- Console/automation workflows gain `commit_ticket_artifact()` for isolated artifact commits without disturbing user worktrees.
- The integration commit wires CLI wrapper/entrypoint flags (`lights-out init --apply`) and roadmap status separately.

## Provenance

| Number / constant | Meaning | Provenance |
|---|---|---|
| `max_files = 10` | Default `--baseline-max-files` cap in `select_incremental_scope` and `core/init.py` | Decision: caps initial incremental mutation scope to 10 active source files so onboarding remains interactive on 100k+ LOC repositories while configurable via `--baseline-max-files` |
| `max_files >= 1` | Lower bound on `--baseline-max-files` | Decision: at least 1 file must be eligible for selection; non-positive or boolean values fail validation cleanly |
| `1` | Start line and minimum line count in `<file>=1-<end>` | Inherited (`core/gates/mutation.py`, ADR 0015 §3): 1-based inclusive line numbering used by all six stack mutation runners; `<end>` is measured from each file's actual line count on disk |
| `20`, `16`, `10` | Minimum suffix lengths in high-confidence token secret patterns (`ghp_`/`glpat-`/`sk-` `>= 20`, `AKIA`/`sk_live_`/`Bearer` `>= 16`, `xox*-` `>= 10`) | External: documented prefix/token payload widths of standard credential formats, preventing false positives on short code tokens |
| `0o755`, `0o644` | File mode bits preserved/set when applying drafted files | Inherited (`core/init_plane.py`): owner-writable executable script mode (`0o755`) and non-executable config mode (`0o644`) |

## Tests

Spec-derived tests written before implementation:

- `core/tests/test_init_apply.py` (strictly app-free, vendor-free, model-free): tests `apply_draft` (atomic copy, overwrite refusal without `force`, `force=True` overwrite summary, secret refusal for `.env`, `.env.*`, PEM private keys, token prefixes, credential assignments, branch creation/switching, scoped commit leaving unrelated dirty/staged files untouched, escaping `contract_rel` refusal), `select_incremental_scope` (`git log --name-only` recency ordering, lexical fallback, test/lockfile/vendor exclusion, `<file>=1-<end>` format, provenance format, `max_files < 1` error), and `commit_ticket_artifact` (relative path check, traversal/symlink escape refusal, secret check, scoped commit leaving unrelated dirty/staged files intact).
- `core/tests/test_init.py`: tests `--apply-to`, `--branch`, `--commit`, `--force`, `--baseline-mode incremental`, `--baseline-max-files`, error exits on invalid flags/max-files, and backward compatibility with `--repo`/`--out`/`--set-baseline`.
- `ci/tests/test_init_apply_e2e.py`: end-to-end tests invoking `core/init.py` CLI over synthetic repositories, verifying `ci/workloads.sh` compatibility, `apply` + branch + commit git state, incremental `MUTATION_LINES` execution compatibility with `stacks/python/runners/mutation.sh` line-filter parsing, and `commit_ticket_artifact` under realistic dirty worktrees.
