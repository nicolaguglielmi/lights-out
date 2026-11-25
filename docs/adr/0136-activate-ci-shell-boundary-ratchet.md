# ADR 0136 — Migrate Clean-Boundary `ci/*.sh` Checks to Python and Activate the `ci/` Shell-Boundary Ratchet

- Status: accepted
- Date: 2026-11-25
- Milestone: M94 (completes ADR 0094)
- Builds on: [ADR 0052](0052-where-shell-is-allowed.md), [ADR 0084](0084-gates-under-the-shell-boundary.md), [ADR 0094](0094-ci-under-the-shell-boundary-later.md), [ADR 0131](0131-retire-core-shell-boundary-debt.md)

## Context

ADR 0052 established the `30` code-line shell boundary for `core/` (`ci/shell-boundary.sh`), ADR 0084 extended it to `gates/` (`core/gates/*.py`), and ADR 0131 retired all remaining `core/` shell debt so `core/` and `gates/` sit at `0` grandfathered exceptions.

ADR 0094 created `ci/checks/` and `ci/tests/` as the Python home for repository self-checks (outside `core/`, so outside the self-gate's SAST/mutation product scope and outside the `lights-out-gates` wheel), migrating `ci/contract.sh` first (`ci/checks/contract.py`) while deferring enforcement on `ci/` until parallel sprints landed. With `core/` and `gates/` debt-free and parallel `ci/*.sh` edits settled for v0.9.0, `ci/` remained the only unratcheted directory (`59` `.sh` scripts totaling `15,271` non-blank, non-comment code lines, with `46` non-host scripts exceeding the `30`-line limit).

## Decision

1. **Migrate `ci/shell-boundary.sh` and 10 clean-boundary `ci/*.sh` checks into `ci/checks/*.py`:**
   - `ci/checks/roles.py` (`ci/roles.sh`)
   - `ci/checks/authors.py` (`ci/authors.sh`)
   - `ci/checks/map_check.py` (`ci/map.sh`)
   - `ci/checks/plan_check.py` (`ci/plan.sh`)
   - `ci/checks/board.py` (`ci/board.sh`)
   - `ci/checks/prompts.py` (`ci/prompts.sh`, scanning `ci/checks/secrets_check.py` alongside `gates/*.sh`, `ci/secrets.sh`, runners, and `workload.env` for `HAND_TOOLS` drift)
   - `ci/checks/secrets_check.py` (`ci/secrets.sh`)
   - `ci/checks/secrets_history.py` (`ci/secrets-history.sh`)
   - `ci/checks/docs_drift.py` (`ci/docs-drift.sh`, preserving `MAP_EXCLUDE` forwarding and contract-compliant `NO-GO` when unset)
   - `ci/checks/evidence_check.py` (`ci/evidence.sh`)
   - `ci/checks/shell_boundary.py` (`ci/shell-boundary.sh`)
2. **Preserve thin POSIX `verdict` wrappers and planted-failure proofs:**
   - Every migrated `ci/<name>.sh` retains `#!/bin/sh`, `# SPDX-License-Identifier: Apache-2.0`, `set -eu`, `name=<check>`, `. ci/lib.sh`, and `verdict ci.checks.<module> "$@"` in at most `25` non-blank, non-comment code lines (`ci/shell-boundary.sh` in `<= 20` code lines while keeping `debt=''` for `core/tests/test_shell_debt_zero.py`), preserving the exact `ci/lib.sh` 1-line `GO:` / `NO-GO:` stdout contract.
   - Module names avoid shadowing Python standard-library modules (`map_check`, `plan_check`, `secrets_check`, `evidence_check`).
   - Every planted-failure self-test (`authors`, `plan_check`, `prompts`, `secrets_history`, `docs_drift`, `shell_boundary`) runs inside its check module before judging the repository.
3. **Activate the `ci/` shell-boundary ratchet in `ci/checks/shell_boundary.py` (ADR 0094 §2):**
   - Enforce the `30` non-blank, non-comment code-line limit across `core/`, `gates/`, and `ci/` uniformly (`LIMIT = 30`).
   - Preserve `CORE_GATES_HOST` (`core/doctor.sh`, `core/attach.sh`, `core/preflight.sh`, `core/progress.sh`) and `CORE_GATES_DEBT = {}` (`0` entries).
   - Declare `CI_HOST = ("ci/lib.sh", "ci/engine-compat.sh")` (`ci/lib.sh` is sourced by host scripts; `ci/engine-compat.sh` executes on the host via `./go engine-compat`).
   - Record `CI_DEBT` mapping all `35` remaining non-host `ci/*.sh` scripts over `30` code lines to their exact measured code-line counts (`awk '!/^[[:space:]]*(#|$)/' "$f" | wc -l`), enforcing all four ratchet clauses (`> 30` unlisted = `NO-GO`, `> ceiling` = `NO-GO`, `< ceiling` = `NO-GO` until lowered, `<= 30` or missing = `NO-GO` until removed) and proving all `core/`, `gates/`, and `ci/` failure cases on a planted temporary tree.

## Alternatives rejected

- **Leave `ci/` unratcheted indefinitely**: Without an enforced `CI_DEBT` ceiling in `ci/shell-boundary.sh`, new self-checks and edits to existing checks accumulate untestable shell logic and `jq`/`awk` glue instead of using `ci/checks/*.py`.
- **Migrate all 46 `ci/*.sh` files over 30 lines in one change**: Scripts such as `ci/logbook.sh` (`2,310` code lines) and active parallel sprint files (`ci/outside.sh`, `ci/codehost.sh`) would create massive merge conflicts and delay enforcement; ratcheting the measured `35` remaining files now locks down regressions immediately while permitting incremental paydown.
- **Drop `debt=''` from `ci/shell-boundary.sh`**: Breaks `core/tests/test_shell_debt_zero.py` without adding value; keeping `debt=''` guarded inside the `6`-line wrapper preserves compatibility while keeping `CI_DEBT` in Python.

## Consequences

- Every `ci/*.sh` script added going forward must stay at `<= 30` code lines and delegate logic to `ci/checks/*.py`, with unit tests under `ci/tests/` run by `ci/core-tests.sh`.
- Any future edit that shrinks or retires one of the `35` `CI_DEBT` scripts must lower or remove its entry in `ci/checks/shell_boundary.py`'s `CI_DEBT` map in the same change.
- Total shell code lines across the `11` migrated `ci/*.sh` files dropped from `716` to `47` (`-669` code lines; `-959` total lines), reducing non-host `ci/*.sh` files over `30` lines from `46` to `35`.

## Provenance

| Constant / Measurement | Value | Provenance |
|---|---|---|
| `LIMIT` (`SHELL_CODE_LINE_LIMIT`) | `30` code lines | Imported unchanged from ADR 0052 §4 (`core/`), ADR 0084 §5 (`gates/`), and ADR 0094 §2 (`ci/`). |
| Thin wrapper budget | `<= 25` (`<= 20` for `ci/shell-boundary.sh`) | Imported from ADR 0131 (`<= 25` code lines under `LIMIT = 30`); `ci/shell-boundary.sh` uses `6` code lines and the other `10` wrappers use `4`–`5` code lines each. |
| `CORE_GATES_DEBT` | `0` entries | Inherited unchanged from ADR 0131 (`M90`, `debt=''`). |
| Migrated `11` scripts code-line reduction | `716` -> `47` (`-669` code lines; `1,089` -> `130` total lines, `-959`) | Measured directly against `HEAD` (`77efba5`) with `awk '!/^[[:space:]]*(#|$)/'`: `roles` `33`->`4`, `secrets-history` `46`->`4`, `secrets` `49`->`4`, `shell-boundary` `50`->`6`, `map` `54`->`4`, `authors` `57`->`4`, `plan` `67`->`4`, `docs-drift` `71`->`5`, `board` `78`->`4`, `prompts` `90`->`4`, `evidence` `121`->`4`. |
| `CI_DEBT` count and repo totals | `35` files (`46` -> `35` over `30`; `15,271` -> `14,602` code lines across `59` `ci/*.sh` files) | Measured across all `59` `ci/*.sh` scripts in the post-migration checkout, excluding `CI_HOST` (`ci/lib.sh`, `ci/engine-compat.sh`) and scripts `<= 30` lines (`ci/profiles.sh` `30`, `ci/shell.sh` `30`, plus wrappers). |
