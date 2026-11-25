# ADR 0094 — `ci/` under the shell boundary, later: where its Python lives, and its debt

- Status: accepted (M61)
- Date: 2026-11-16

## Context

ADR 0052 put `core/` under a rule (a shell file has at most 30 code lines,
logic in Python, a ratchet that only goes down) and named `ci/` as a later
slice; ADR 0084 added `gates/`. `ci/` is what is left: the repository's own
checks, the suites `./go ci` runs. Measured on 2026-11-16 at the
head of `claude/m61-python-slice-56bf33`, main 5f42b22 merged in: 19,278 lines of
`.sh` in 57 files, 14,510 code lines (non-blank, not a comment); 47 files over 30
code lines, 14,350 code lines between them. Most of them are end-to-end suites:
glue around `make`, `git`, a temp tree and planted files, with `jq`
assertions between. One is a judge rather than glue: `ci/contract.sh`, the
meta-gate every gate's output passes through.

Two things stop the rule from simply extending to `ci/`. The self gate
(ADR 0049) mutates and scans `core/` as the product; a `ci/` suite is a test
of that product, and putting its logic under `core/` would mutate tests of
tests and ship them in the `lights-out-gates` wheel's package directory
(ADR 0061). And in the sprint of 2026-11-16 four milestones edit `ci/*.sh`
in parallel (M58, M59, M60, M62): an exact-count debt list there would make
each of their edits a NO-GO until its entry moved.

## Decision

1. **A `ci/` check's logic in Python lives in `ci/checks/<x>.py`**, a package
   (`ci/__init__.py`, `ci/checks/__init__.py`), standard library only, run
   by its `ci/<x>.sh` through `verdict ci.checks.<x>` from the repository
   root, the same contract as ADR 0084 §3. Its unit tests are
   `ci/tests/test_<x>.py`, `unittest`, written from the check's header and
   the ADRs it cites, never from the shell, run by `ci/core-tests.sh` beside
   `core/tests/`. Outside `core/`, so outside the self gate's mutation and
   SAST scope and outside the wheel: these are tests, judged by `./go ci`
   running them, not mutated as product. A `ci/` module may run processes
   with `subprocess` directly (it is not under bandit; `core/gates/`'s one
   `proc` stays the product's rule).
2. **`ci/` enters the ratchet after the sprint M58–M63 has landed**, not
   before: `ci/shell-boundary.sh` then scans `ci/` too, with ADR 0052 §4's
   rule unchanged (30 code lines, provenance there), the debt below
   re-measured on the landed main and listed with exact counts. Until then
   the list here is a record, not a check.
3. **The first `ci/` slice is `ci/contract.sh`** (M61): 225 code lines to 4,
   its judging in `ci/checks/contract.py` (the shape of a line, which record
   is usable, the planted run read gate by gate, and the probes of
   `ci/lib.sh`'s `nogo` and `record_check`), unchanged in what it accepts and
   refuses; its planted proof is the same tree, run by the same check.
4. **The debt at this head**, `file code-lines`, for the follow-up to
   re-measure (provenance: a measurement, 2026-11-16, this branch's head):

```
ci/agents.sh 443
ci/assign.sh 145
ci/attach.sh 357
ci/authors.sh 57
ci/board.sh 78
ci/codehost.sh 406
ci/decide.sh 440
ci/dev-phase.sh 112
ci/docs-drift.sh 71
ci/evals.sh 66
ci/evidence.sh 121
ci/first-run.sh 191
ci/gates-light.sh 157
ci/harness-shape.sh 387
ci/harness.sh 66
ci/hooks.sh 305
ci/init.sh 371
ci/intake.sh 453
ci/lib.sh 112
ci/logbook.sh 2190
ci/mainline.sh 351
ci/map.sh 54
ci/metrics.sh 328
ci/network.sh 144
ci/outside.sh 115
ci/package.sh 461
ci/perimeter.sh 92
ci/plan.sh 67
ci/plugin.sh 821
ci/postmortem.sh 733
ci/promote.sh 398
ci/prompts.sh 90
ci/range.sh 642
ci/readiness.sh 428
ci/release.sh 252
ci/review.sh 351
ci/roles.sh 33
ci/sast-diff.sh 225
ci/screen.sh 355
ci/secrets-history.sh 46
ci/secrets.sh 49
ci/shell-boundary.sh 62
ci/store-bus.sh 198
ci/toolchain.sh 172
ci/tower.sh 637
ci/wiki.sh 271
ci/workloads.sh 447
```

   The heaviest by code and branching lines (`if`, `case`, `&&`, `||`,
   `jq`, `awk`, `sed`): `logbook` 2,152 and 620, `tower` 637 and 339,
   `plugin` 821 and 208, `attach` 357 and 187, `postmortem` 733 and 181.

## Alternatives rejected

- **`ci/` logic under `core/`, as `core/gates/` is.** The self gate would
  mutate the tests of the product as if they were the product, and every
  `ci/` suite's helper would sit in the wheel's package directory.
- **`ci/` into the ratchet now.** Four parallel milestones edit `ci/*.sh`;
  each edit would be a NO-GO until its entry moved, and the landing session
  would resolve every count by hand.
- **Python next to each script, `ci/<x>.py`, with no package.** `verdict`
  runs `python3 -m MODULE`; a module needs a package to be named, and the
  tests a place `unittest discover` finds that is not every script.

## Consequences

- `ci/core-tests.sh` runs `ci/tests/` too.
- `ci/contract.sh`'s planted tree copies `ci/checks/` with it.
- A change to `ci/contract.sh`'s rule is a change to
  `ci/checks/contract.py` and its tests.

## Golden-rule check

`./go demo` does not run `ci/`; `./go ci` runs the same checks, in the same
workbench image, with `python3` already in it. No key, no account, no new
image layer.
