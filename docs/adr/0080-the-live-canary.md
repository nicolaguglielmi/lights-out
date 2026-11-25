# ADR 0080 — The live canary: each stage records when it started, when it was judged and its numbers as structures; the in-progress record names the running stage; a rollback records its reason; the console shows the stage in course and the rollback as they happen, one point per stage

- Status: accepted
- Date: 2026-11-12
- Milestone: M46 (docs/PLAN.md, "Sprint 2026-11-12", stream A, third slice)
- Amends: ADR 0020 §4 and ADR 0025 §7 (the release record's stage entry
  gains `started_at`, `judged_at` and `slo`; the record gains `running`
  while in progress and `reason` when rolled back); ADR 0047 §2 (logbook
  1.4 carries them). ADR 0047's "nothing to chart" stands: this is the
  stage in course, not a time series.

## Context

Read on 2026-11-12:

- A stage of the release record is `{pct, verdict, numbers}`, `numbers`
  being the `slo:` lines `verify/slo.sh` prints on stderr
  (`core/releases.py stage`). There is no time: the record cannot say when
  a stage started, how long it was watched, or when it was judged.
- `core/launchpad.sh` writes the record `in-progress` before the deploy
  and replaces it with `held` or `rolled-back` at the end. Between the
  two, the record does not change: a console polling every 2 s sees
  "in-progress" for two minutes and then the verdict.
- A rollback's reason is the launchpad's last line (`NO-GO: launchpad —
  5%: … → rolled back`), printed and lost. The record keeps the failing
  stage's verify line only when verify itself said NO-GO. A shift that
  did not take, a freeze at a stage, a deploy that never became healthy
  or a DAST NO-GO leave no reason in the record.
- The numbers are text. A screen that wants "error rate of the canary
  against the stable and against the threshold" would have to parse
  `slo: error_rate canary(0.2.0)=0.004 stable(0.1.0)=0.000` itself, and
  find the threshold somewhere else.

## Decision

### 1. A stage entry: `started_at`, `judged_at`, `slo`

`core/releases.py stage STAGES PCT VERDICT STARTED_AT < SLO_STDERR` adds
to the entry, beside `pct`, `verdict` and `numbers` (unchanged):

- `started_at`: when the shift to PCT took, passed by the launchpad;
- `judged_at`: when the entry is written, right after verify answered;
- `slo`: one object per `slo:` line, in their order,
  `{query, canary, stable, delta, max_delta}`:
  - `query`: the line's query name (`traffic`, `error_rate`,
    `latency_p95`);
  - `canary`, `stable`: the numbers the line prints, `null` where it says
    `no data`, except one case `verify/slo.sh` itself decides: an
    `error_rate` with `no data` on a side whose `traffic` is above zero is
    `0`, because verify judges it as zero errors (ADR 0011's empty
    numerator; found by `./go demo` on 2026-11-13, where the stable's
    error series is absent and the deciding delta would otherwise be
    `null`);
  - `delta`: `canary - stable`, rounded to the three decimals the line
    shows; `null` if either is `null`;
  - `max_delta`: the effective policy's `ERROR_RATE_MAX_DELTA` for
    `error_rate`, `LATENCY_P95_MAX_DELTA` for `latency_p95`, `null` for
    `traffic` (verify has no delta for it). `core/releases.py` takes
    them from its environment when set, else reads the effective policy
    through `core/policy.py`, the reader `verify/slo.sh`'s
    `effective_policy` calls (`policy/merge.env` under
    `WORKLOAD_CONTRACT`'s own); a policy it cannot read gives `null`,
    never a guess. The launchpad's shell does not grow
    (`ci/shell-boundary.sh`, ADR 0052).

A line that does not match the `slo:` shape stays in `numbers` and adds
nothing to `slo`. `verify/slo.sh` is unchanged.

### 2. The in-progress record names the running stage

At each stage, once the shift took, the launchpad writes the record
`in-progress` again with `running: {pct, started_at, judge_after_s}`,
`judge_after_s` being `STAGE_SECONDS`: verify runs after that wait. A
final record (`held`, `rolled-back`) has no `running`. The release log
is appended only on a final record, as before.

### 3. A rolled-back record says why

`fail_stage STAGE REASON` passes `<STAGE>: <REASON>` to the record as
`reason`, the same words as the launchpad's NO-GO line without its
prefix and without "→ rolled back". A `held` record has `reason: null`.

### 4. Logbook 1.4

All optional. `releases[]` entries carry `reason`; their stages carry
`started_at`, `judged_at` and `slo`; `launch.record` is the record as
written, so it carries `running` and `reason` too. The timeline's
`canary` rows take `judged_at` as their `at` when the stage has one, and
carry `slo`.

### 5. The console: the stage in course, and the rollback

A `section#canary` on `#launch`, and at the top of the overview while the
record is `in-progress`, draws the record's stages, one row per stage
(`.canary-stage[data-pct]`):

- the percentage of traffic;
- for `error_rate` and `latency_p95`: canary, stable, delta and
  `max_delta`, and whether the delta is within it (`data-within`);
- when the stage started and when it was judged, and its verdict.

While the record is `in-progress` with `running`, one more row,
`.canary-stage[data-running]`, shows the running percentage and the time
left before the judgement: `started_at + judge_after_s` minus the
console's `read_at` (live), in whole seconds, never below zero, never
the browser's clock. A snapshot has no `read_at`: it shows "judged after
<judge_after_s> s".

On a `rolled-back` record, `#canary-rollback` says "Rolled back
automatically" with the record's `reason` (or, for a record from before
this ADR, the last stage's verdict), and the stage that decided it is
marked `data-decided`.

The poll stays 2 s (ADR 0047 §4). One point per stage, no chart.

### 6. The proofs

- `core/tests/test_releases.py`: the `slo` parse (numbers, `no data`,
  delta, `max_delta` per query, a line off the shape), the times, the
  `running` and `reason` fields.
- `ci/logbook.sh`: the golden's stub record and log carry the fields,
  checked by hand.
- `ci/console-ui.py`: over a derived in-progress record, the running row
  and its time left against `read_at`; over a rolled-back one, the reason
  and the deciding stage.
- `./go demo`: unchanged, timed before and after (§Golden-rule check).

## Alternatives rejected

- **Change `verify/slo.sh` to print JSON.** Its stderr is its human
  output and the gate contract keeps stdout to one line; the `slo:` lines
  are already the record's evidence, and parsing them in one place
  (`core/releases.py`) changes no gate.
- **A time series, a point every poll.** ADR 0047 rejected charts; one
  judged point per stage is what decided.
- **The countdown from the browser's clock.** The console never uses the
  viewer's clock (ADR 0063 §2); `read_at` is the server's.
- **A new file for the live state.** The record already exists, is
  already written in progress, and is what the logbook reads.

## Consequences

- Records and log lines written before this ADR have none of the new
  fields; the console shows what it showed for them.
- The launchpad calls `core/releases.py` once more per stage (the
  in-progress record); `core/releases.py stage` reads the policy files
  once per stage.

## Golden-rule check

`./go demo` runs the launchpad: two more small Python calls per stage and
one per release. Measured before and after on the same machine; the
numbers are in the handoff.
