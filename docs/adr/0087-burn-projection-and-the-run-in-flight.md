# ADR 0087 — Budget burn and projection: the ledger counts the runs that stop without a PR, the spend panel shows the burn per hour and the day's projection against its limit, and the run in flight shows its tokens against `TOKEN_BUDGET` with a warning before the stop

- Status: accepted
- Date: 2026-11-14
- Amended by: ADR 0092 §7 (the budget, and the 80% warning, are the provider's `TOKEN_BUDGET_<PROVIDER>` when the role sets one)
- Milestone: M54 (docs/PLAN.md, "Sprint 2026-11-14 — Il flusso a colpo
  d'occhio")
- Amends: ADR 0083 §3 (a failed attempt's run enters the ledger), §5 and
  its amendment of 2026-11-13 §2-§3 (`./go spend` prints two more lines,
  before the budget's); ADR 0041 §5 (every CLI harness writes `run.json`
  before it exits on `TOKEN_BUDGET`); ADR 0047 §2 (logbook 1.6 carries
  `spend.burn` and `tickets[].live`, the same minor as ADR 0085)

## Context

Read on 2026-11-14:

- M24 on Gemini (handoff `claude-m24-phase-d-gemini-1628f2.md`, on its landing
  merge 59e93e0) lost three tickets of ten to the dev-worker's `TOKEN_BUDGET`,
  twice each, and nobody could see it coming: the console shows a run
  only once its step has ended, and these steps ended in a failure.
- Those six runs are not in the spend ledger. The tower appends a run to
  `spend` from the step's result (ADR 0083 §3); a crew that fails exits
  the step non-zero, and the tower records the failure with no result.
  The handoff measured about $12 of the $20.88 list-price bound in the
  five runs that stopped on budget; the ledger showed $0.003 for each of
  those tickets (their product role's run). M50's limits read the ledger,
  so they could not see the money those runs spent: the day's
  projection would be wrong by the same amount.
- Two of the four CLI harnesses cannot leave a `run.json` on a budget
  stop: `adapters/gemini-cli/run.sh` and `adapters/antigravity-cli/run.sh`
  exit on the budget marker before they write it; `claude-code` and
  `codex` write it first.
- The budget hooks keep the run's running count in `$CREW_OUT/tokens`
  (`.crew/<ticket>/out/tokens` on the host), the number they compare to
  `TOKEN_BUDGET` before each call (ADR 0041 §5; gemini-cli's per model
  call since a66e6df). `crew.sh` empties `.crew/<ticket>/` when an
  attempt starts and the harness writes `out/run.json` when it ends:
  between the two, `out/tokens` is the attempt in flight.
- The spend panel (ADR 0083 amendment §5) prints `./go spend`'s lines.
  It has no rate and no projection, and no clock: the logbook reads none
  (ADR 0047), so equal sources give equal bytes.

## Decision

### 1. A failed attempt's run enters the ledger

Before running `assign` or `rework`, the tower notes the time. When the
step exits non-zero and `.crew/<ticket>/out/run.json` exists, is a JSON
object and was written at or after that time, the tower appends one
entry to the document's `spend`, in the same write that records the
failure: the entry ADR 0083 §3 gives for `{task: <the run's task fields,
role ROLE or dev-worker>}`, priced the same way, with:

- `attempt`: 1 for `assign`, the document's `attempt` + 1 for `rework`
  (the attempt that ran);
- `failed`: the budget marker the harness left in `out/stop`
  (`TOKEN_BUDGET`, `MAX_TURNS`), else `the run failed`.

An entry without `failed` is a run whose step succeeded, as before. A
`run.json` from before the step (a crew that failed before it emptied the
directory) is not counted twice. The ticket's budget and the day's then
count what that run spent, so a `./go resume` after a stop is checked
against it. `review` and the product role's steps are not touched: their
harness runs inside the step, which already records what it ran or fails
before a model is called. The network harness's members are not read: a
member that stops leaves the network's `run.json` unwritten (a
follow-up).

### 2. Every CLI harness writes `run.json` on a budget stop

`adapters/gemini-cli/run.sh` and `adapters/antigravity-cli/run.sh` write
`run.json` from the transcript's statistics before they exit on the
`TOKEN_BUDGET` marker, as `claude-code` and `codex` do; the exit code and
the stderr line are unchanged. `gates/harness-conformance.sh` case 6
(`TOKEN_BUDGET`) asserts `run.json` exists with its `harness`, `provider`
and a `tokens` object, for every harness it runs.

### 3. The burn and the day's projection

`core/spend.py burn(docs, limits)`, over every ledger entry, with no
clock of its own:

- `end`: the latest `at` of any entry; `day`: its date (UTC). The
  entries of that day are the day's; none, and there is no burn.
- `first`: the earliest `at` of the day's entries. `hours`: `(end −
  first)` in hours, at least 1. Provenance, decision: a rate over less
  than an hour is one run's cost spread over its own minutes (M24 phase
  D: tenacity-03's first run cost $1.39 in 495 s, which would read as
  $10/h); one hour is the bucket the panel draws.
- `spent_usd`: the known cost of the day's entries; `unknown`: how many
  of them have no cost. `per_hour_usd`: `spent_usd / hours`.
- `left_h`: hours from `end` to the end of the day (24:00 UTC).
  `projected_usd`: `spent_usd + per_hour_usd × left_h`: the day's spend
  if the burn goes on until midnight. Known cost only; the unknown runs
  are named, never priced.
- `limit_usd`: `SPEND_BUDGET_DAY_USD`, null when empty or malformed.
  `over`: `projected_usd >= limit_usd` (the tower's own "at or over",
  ADR 0083 §4), null without a limit.
- `hourly`: 24 numbers, the known cost of the day's entries by the hour
  of their `at`, for the sparkline.

All amounts rounded to the micro-dollar, as the ledger. `./go spend`
prints two lines between the days and the budget, the same words the
logbook carries in `spend.lines`:

- `burn: $<per_hour> per hour on <day> ($<spent> known in <n> run(s) from
  <first HH:MM> to <end HH:MM> UTC, over <hours, one decimal> h)`, and
  `, <u> of cost unknown` before the `)` when any;
- `projection: $<projected> by the end of <day> UTC at that rate, as of
  <end HH:MM> UTC; ` then `SPEND_BUDGET_DAY_USD=$<limit>: over` or `:
  under`, or `SPEND_BUDGET_DAY_USD no limit`, or `the day's limit cannot
  be read`.

With no entry: `burn: no runs in the ledger` and `projection: none
without a run`. The stdout line is unchanged.

"As of the last run" is the honest reading of a record without a clock:
the burn changes only when a run ends, and the projection says from when
it projects.

### 4. The run in flight

`tickets[].live`, null unless all of these hold: the ticket is at
`opened` or `ci-failed`, its last history entry has no error,
`<CREW_DIR>/<id>/out/tokens` holds a whole number and
`<CREW_DIR>/<id>/out/run.json` does not exist (`CREW_DIR`, default
`.crew`). Then `{role, attempt, tokens, budget, warn, line}`:

- `role`: `dev-worker`, the role `assign` and `rework` run; `attempt`: 1
  at `opened`, the document's `attempt` + 1 at `ci-failed`;
- `tokens`: the hook's count; `budget`: the role's `TOKEN_BUDGET`
  (`<ROLES_DIR>/dev-worker/role.env`, `ROLES_DIR` default `roles`), null
  when it has none;
- `warn`: `tokens >= 0.8 × budget`. Provenance, decision: the owner's,
  confirmed on 2026-11-14; the brief asks for a warning before the stop.
  On M24's Gemini runs each model call resent about 45,000 tokens
  (measurement, the phase D handoff, section 2): 80% of 2,600,000 leaves
  520,000 tokens, about eleven calls between the warning and the stop.
- `line`: `dev-worker, attempt <n>: <tokens> of <budget> tokens
  (<percent>%)`, then ` — near TOKEN_BUDGET, the run stops at <budget>`
  when `warn`; `<tokens> tokens, no budget` when none.

The live count is read, never written; the next logbook after the run
ends has no `live` and the ledger has the run (§1).

### 5. On screen

- The spend panel gains, under the summary, `#spend-burn` (the burn line)
  with `svg#spend-hours`, a sparkline of `hourly` (24 bars, `role="img"`,
  an `aria-label` naming the day and each non-zero hour's cost), and
  `#spend-projection` (the projection line) with `data-over` `true`,
  `false` or empty; `true` draws it as a warning, in words as well as
  colour (the line says `over`). Then `ul#spend-live`, one `li` per
  ticket with a `live`, its line, when any.
- A card with `live` shows a badge `span.live` with `data-warn`, the
  tokens and the budget in short (`2.2M / 2.6M`), a `meter`, and the line
  as its accessible name; the card's back and the timeline show the line.
  `warn` draws the badge in the waiting colour with the word `near`.
- The role matrix (ADR 0086) reads the same `live` for the dev-worker's
  row.
- No chart library: an inline SVG of 24 rectangles.

### 6. Logbook 1.6

`spend.burn` (§3, null with no entry), `spend.lines` with the two lines
of §3, `tickets[].live` (§4), additions only, beside ADR 0085's and ADR
0086's.

## Tests, from this ADR

- `core/tests/test_spend.py`:
  - burn on a day with entries at 09:00 ($2.00), 10:30 ($1.00), 12:00
    ($3.00): `hours` 3.0, `per_hour_usd` 2.0, `left_h` 12.0,
    `projected_usd` 30.0, `hourly[9]` 2.0, `hourly[10]` 1.0, `hourly[12]`
    3.0, the rest 0; `SPEND_BUDGET_DAY_USD=30` over, `=30.01` under, empty
    null; an entry of the day before never counts; an unknown entry of
    the day is counted in `unknown`, not priced, and named in the burn
    line; two entries ten minutes apart give `hours` 1.0; no entry gives
    None and the two `none` lines.
  - `lines` order: tickets, roles, days, burn, projection, budget.
- `core/tests/test_tower.py`, with step scripts of the test's own:
  - an `assign` that writes `.crew/<t>/out/run.json` (priced model,
    1,000,000 in, 0 out) and `out/stop` `TOKEN_BUDGET`, then exits 1: the
    ticket stays at `opened` with the error, and `spend` gains one entry,
    `step` assign, `attempt` 1, `failed` `TOKEN_BUDGET`, its cost;
  - a `rework` at attempt 2 doing the same: `attempt` 3;
  - a `run.json` older than the step: no entry; no `run.json`: no entry;
    a failing `ci` step with a `run.json` there: no entry;
  - then, with `SPEND_BUDGET_TICKET_USD` under that cost, `./go resume`'s
    `assign` is refused on the budget line.
- `core/tests/test_logbook.py`, over the stub (whose `crew/0003/out/tokens`
  holds 2,210,000 and whose ledger is as ADR 0083's amendment): 0003's
  `live` `{dev-worker, 3, 2210000, 2600000, true, "dev-worker, attempt 3:
  2,210,000 of 2,600,000 tokens (85%) — near TOKEN_BUDGET, the run stops
  at 2,600,000"}`; 2,079,999 not `warn`, 2,080,000 `warn`; with
  `out/run.json` beside it, with the ticket at `review`, with an error
  last, with no tokens file: null; `spend.burn` for the stub's 2026-11-03
  ($1.1625 per hour, 1.0 h, 15.75 h left, $19.471875 projected, no
  limit, `hourly[8]` 1.1625) and its two lines.
- `ci/logbook.sh`: the 1.6 `spend.burn`, the two lines and 0003's `live`
  by hand, each run again planted wrong.
- `ci/console-ui.py`, over the golden: `#spend-burn`, `svg#spend-hours`
  with 24 bars whose heights order as `hourly`, `#spend-projection` with
  its line and `data-over`, `ul#spend-live` with 0003's line; the 0003
  card's `span.live[data-warn=true]` and its line on the back; over a
  derived logbook with a day limit under the projection, `data-over=true`;
  contrast in both themes; nothing POSTed.
- `gates/harness-conformance.sh` case 6, on Actions: `run.json` after the
  stop, every harness.

## Alternatives rejected

- **The logbook reads a clock for "now".** It would make two reads of the
  same sources differ, which ADR 0047 rules out, and stream B's snapshot
  could not be checked byte for byte. The console has `read_at`; the
  projection would be the page's own number, which the spend panel's rule
  (every word is the command's) rules out.
- **The burn over the last hour only.** One run dominates it: it jumps
  from zero to a run's cost and back.
- **The tower records the failed run from the crew's stderr.** The stats
  are the harness's, in `run.json`; parsing a log would be a second
  source for the same numbers.
- **Cutting the run at the warning.** `TOKEN_BUDGET` is the stop; the
  warning is for a person, who can stop the crew or raise the budget for
  the next attempt.
- **Pricing cached input lower.** The handoff's second finding; it needs
  `run.json` to keep the harness's `cached` count and a cached price per
  model. Its own change.

## Consequences

- The spend M24's Gemini leg could not see is in the ledger: a ticket
  that stops on budget twice now shows both runs and their cost, and the
  limits count them.
- The card of a ticket whose attempt is in flight shows how close it is
  to its budget while it runs, from the 2 s poll at L1; at L0 the replay
  harness writes no count and nothing is shown.
- The panel projects the day from the last run's time; a quiet afternoon
  keeps the morning's projection until the next run.
