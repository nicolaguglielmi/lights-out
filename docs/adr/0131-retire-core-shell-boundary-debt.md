# ADR 0131 — Retire Remaining `core/` Shell-Boundary Debt

- Status: accepted
- Date: 2026-11-25

## Context

ADR 0052 set the shell boundary at `SHELL_CODE_LINE_LIMIT = 30` non-blank, non-comment lines per `core/` script (`ci/shell-boundary.sh`), keeping only the four host scripts (`core/doctor.sh`, `core/attach.sh`, `core/preflight.sh`, `core/progress.sh`) as plain POSIX shell. Ten grandfathered `core/` scripts remained in `ci/shell-boundary.sh`'s `debt` list above that ceiling: `core/crew.sh`, `core/egress.sh`, `core/launchpad.sh`, `core/mutation-baseline.sh`, `core/postmortem.sh`, `core/promote.sh`, `core/steps/ci.sh`, `core/steps/release.sh`, `core/steps/review.sh`, and `core/steps/rework.sh`.

## Decision

1. Migrate the orchestration and verification logic of all 10 remaining `debt` scripts into pure-stdlib Python modules under `core/` (`core/crew_cmd.py`, `core/egress_cmd.py`, `core/launchpad_cmd.py`, `core/mutation_baseline.py`, `core/postmortem_cmd.py`, `core/promote_cmd.py`, `core/step_ci.py`, `core/step_release.py`, `core/step_review.py`, `core/step_rework.py`), sharing subprocess, env-file, JSON, and gate-output helpers in `core/cmd_util.py`.
2. Preserve every existing `.sh` entrypoint path (`core/crew.sh`, `core/egress.sh`, `core/launchpad.sh`, `core/mutation-baseline.sh`, `core/postmortem.sh`, `core/promote.sh`, `core/steps/*.sh`) as a thin wrapper of at most `25` non-blank, non-comment code lines (well under `SHELL_CODE_LINE_LIMIT = 30`) so callers (`Makefile`, `core/tower.sh`, `console/serve.py`, `ci/logbook.sh`, adapters, and gates) keep their exact paths, stdout `GO:`/`NO-GO:`/JSON contracts, stderr logs, and exit codes.
3. Clear `debt=''` in `ci/shell-boundary.sh` so the shell boundary has `0` grandfathered exceptions going forward, and align downstream static checks (`ci/tower.sh`, `ci/outside.sh`, `core/gates/harness_proof.py`, `core/tests/test_ux_glue.py`, `ci/tests/test_proxy_verify.py`) to inspect both `.sh` wrappers and their Python modules.

## Alternatives

- **Raise the ceiling or leave the 10 scripts in `debt`**: Keeping complex JSON assembly, retry polling, env parsing, and temporary worktree cleanup in POSIX shell with `jq`/`awk`/`sed` forks perpetuates quoting and error-handling fragility and defeats the ratchet established by ADR 0052.
- **Delete the `.sh` entrypoint files entirely**: Replacing `.sh` paths directly with `.py` invocations breaks existing host callers (`console/serve.py`, `core/tower.sh`, `Makefile`, conformance suites, and tests) in a single flag day; keeping thin `set -eu; exec python3 ... "$@"` wrappers preserves compatibility with zero caller churn.

## Consequences

- `ci/shell-boundary.sh` carries `debt=''` (`0` exceptions) and enforces the `30`-line limit uniformly on every non-host script under `core/`.
- Unit tests can import and test each orchestration module directly with injected environments and subprocess fakes, while `core/step_review.py` exposes modular functions (`prepare_review_workspace`, `run_single_review`, `parse_verdict`, `post_review_status`) ready for multi-reviewer composition.
- Aligned `ci/` and gate helper copy lists must keep each wrapper's companion Python module alongside the `.sh` entrypoint when copying subsets of `core/`.

## Provenance

- `SHELL_CODE_LINE_LIMIT = 30`: imported from ADR 0052 (`limit=30` in `ci/shell-boundary.sh`).
- Wrapper line budget `<= 25`: chosen beneath `SHELL_CODE_LINE_LIMIT = 30` with margin for `set -eu` plus one `exec python3 ...` dispatch (actual wrappers are `2` code lines each).
- Debt count `10` -> `0`: counted directly from `debt` entries in `ci/shell-boundary.sh` (`core/crew.sh`, `core/egress.sh`, `core/launchpad.sh`, `core/mutation-baseline.sh`, `core/postmortem.sh`, `core/promote.sh`, `core/steps/ci.sh`, `core/steps/release.sh`, `core/steps/review.sh`, `core/steps/rework.sh`) before clearing `debt=''`.
