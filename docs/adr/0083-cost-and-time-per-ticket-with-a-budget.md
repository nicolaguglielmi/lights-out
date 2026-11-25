# ADR 0083 — Cost and time per ticket: a dated price table in `profiles/`, a spend ledger in the tower, a budget per ticket and per day that stops the next attempt

- Status: accepted (M50)
- Date: 2026-11-12
- Amended by: ADR 0092 §5-§6 (a `cached_input` price; cached input priced at it when the run reports `tokens.cached`, and `cost_basis` on every ledger entry)
- Amended by: ADR 0087 (a failed attempt's run enters the ledger; `./go spend`
  prints the burn and the day's projection before the budget line)

## Context

Every role's `run.json` reports its tokens and duration, and the tower
copies them into the ticket's document (ADR 0047 §2, P1). A run has a
token budget of its own (`roles/*/role.env` `TOKEN_BUDGET`, ADR 0021
§7). Nothing turns tokens into money, nothing adds a ticket's runs up,
and nothing stops a ticket that keeps reworking, or a day of tickets,
before the money is gone. Two more gaps, found reading the code
(2026-11-12): the tower keeps every dev-worker attempt in `tasks`, but
`review` and `product` keep only the last run (`rework` clears `review`),
so the earlier reviews of a ticket are not on the document at all.

The plan asks for prices with their source and date, the cost of every
run beside its tokens, two limits that stop the next attempt and send the
ticket to `needs-human` with a one-line reason, `./go spend`, and no
invented price (docs/PLAN.md, sprint 2026-11-12, M50).

## Decision

1. **The price table is `profiles/prices.json`**, the one place besides
   `adapters/` where a model id may be written (ADR 0040 §5). One entry
   per provider, model and period: `{provider, model, input, output,
   from, until, source, checked}`, prices in USD per million tokens,
   `from`/`until` dates (UTC, both included, either may be absent),
   `source` the vendor's own pricing page and `checked` the day it was
   read. Provenance, external, each read on 2026-11-12:

   | provider | model | input | output | period | source |
   |---|---|---|---|---|---|
   | gemini | gemini-3.8-flash | 0.75 | 3.75 | until 2026-12-31 | aistudio.google.com/docs/pricing (ai.google.dev's page redirects there), "Standard", paid tier; output includes thinking tokens |
   | gemini | gemini-3.8-flash | 1.50 | 7.50 | from 2027-01-01 | the same page, the announced price |
   | anthropic | claude-sonnet-5-5 | 2.00 | 10.00 | — | platform.claude.com/docs/en/about-claude/pricing, base input and output; no long-context tier for this model |
   | openai | gpt-6.1-sol | 2.00 | 10.00 | — | developers.openai.com/api/docs/pricing, Standard, prompts up to 272K tokens |

   A model the table does not name, or a run outside every period of its
   model, has no price: **cost unknown**, never a neighbour's price, never
   a prefix match.
2. **The cost of a run** is `input x price.input / 1e6 + output x
   price.output / 1e6`, from `tokens.input` and `tokens.output`, at the
   price whose period holds the day the run was recorded; rounded to the
   micro-dollar. A replayed run (`replayed: true`) costs 0: no model was
   called (ADR 0017 §2). A network's run (ADR 0036) is the sum of its
   members', unknown if any member's is. Unknown, with the reason, when
   the run names no provider or model, reports no input or output tokens,
   or has no price.
3. **The ledger.** After every step, the tower appends to the document's
   `spend` list one entry per run the step's result carries: `task` (the
   dev-worker's, `assign` and `rework`), `review` (the reviewer's) and
   `product` (the clarify and answer steps'), each an object with a
   `harness`; a `product` with `consulted: false` is not a run (ADR 0055:
   at L0 with no recording no model was asked). An entry is `{step,
   attempt, role, harness, provider, model, replayed, tokens, duration_s,
   cost_usd, cost_unknown, at}`: the cost beside the tokens, `cost_usd`
   null and `cost_unknown` the reason when there is no price. The list is
   appended, never rewritten, so a review a rework cleared is still
   counted. `task`, `tasks`, `review` and `product` are unchanged, and the
   logbook does not read `spend` yet.
4. **The budget.** `SPEND_BUDGET_TICKET_USD` and `SPEND_BUDGET_DAY_USD`
   in `policy/rework.env`, beside the cap they extend, read from the
   tower's own checkout like it. Empty by default: no limit, and `./go
   demo` unchanged. Provenance, decision: the owner's, docs/PLAN.md,
   sprint 2026-11-12, M50 ("vuoti di default"); a number set there must
   carry its own provenance (ADR 0047's check on policy keys). A value
   that is neither empty nor a positive decimal number refuses the step,
   naming the key.
   - **When.** Before the two steps that launch a dev-worker attempt,
     `assign` and `rework`; a run in progress is never cut (its own
     `TOKEN_BUDGET` does that), and `review`, `clarify` and `answer`
     are not stopped.
   - **What is counted.** The ticket: every `spend` entry of its
     document. The day: every `spend` entry dated today (UTC) of every
     ticket the Tracker lists (`TICKETS_DIR/*.md`, as the logbook reads
     them), read through the state store.
   - **Over.** A limit is reached when the spend counted is at or over
     it, or when any entry counted has an unknown cost: a sum with an
     unknown term cannot be shown under a limit. The reason is one line
     naming the key, the limit and the spend, or the run whose cost is
     unknown and why.
   - **`rework` over the limit**: the tower runs `core/steps/rework.sh`
     with `SPEND_STOP=<line>`, which records the rejected attempt in
     `rework` as always and prints `needs-human` with `budget: {line}`,
     without launching the next attempt.
   - **`assign` over the limit**: refused like a red main (ADR 0056 §5):
     the history entry carries the line as its error and the ticket stays
     at `opened`, resumable with `./go resume` once the day has turned or
     the policy has changed. Not `needs-human`: no attempt and no PR
     exist yet, and a ticket at `needs-human` holds its perimeter
     (ADR 0053 §5).
5. **`./go spend`** reads every ticket's document through the state
   store and prints on stderr the cost and time per ticket (the known
   cost, how many runs, how many of unknown cost, the agents' time as the
   sum of `duration_s` of the runs that were not replayed, and the
   elapsed time from the first to the last history entry), per role and
   per day. A replayed run reports the recorded run's duration, not its
   own (found at the first `./go demo` with this ADR: ticket 0001's
   replayed attempt reported 328 s of agents inside 218 s of ticket); stdout is one line of the gate
   contract, `GO: spend — <n> tickets, <r> runs, $<x> known, <u> of cost
   unknown`, or `NO-GO: spend — <why>` when the store cannot be read.

## The gap between the cost and the bill

- **Cached input is priced as input.** Claude Code's input counts cache
  reads and writes (`adapters/claude-code/run.sh`), Codex's and Gemini
  CLI's input include cached tokens; the bill discounts cache reads
  (0.05x on Claude Sonnet 5.5). The cost overstates such runs.
- **Tokens in neither input nor output are not priced.** Gemini CLI's
  `total` can exceed input plus output: the recorded run of ticket 0001
  reports 2,329,279 in, 2,838 out, 2,435,115 total (measurement,
  `fixtures/0001/dev-worker/run.json`), 102,998 tokens unaccounted. The
  plan prices input and output; the rest is not guessed at.
- **Long-context prices are not applied.** OpenAI's prompts over 272K
  tokens cost more; a run's totals do not say how large each request
  was, so the base price is used.
- **The release role's run is not in the ledger.** `core/promote.sh`
  runs it from L1 without recording its `run.json` in the tower; that
  file belongs to the sprint's stream A, so recording it is left to a
  follow-up, as is the logbook carrying `spend` (the console slice).

## Tests, from this ADR

`core/tests/test_spend.py`, with a price table of the test's own
(provider `prov`; `model-a` at 1.00 in and 5.00 out; `model-b` at 0.75
and 3.75 until 2026-12-31, 1.50 and 7.50 from 2027-01-01):

- `model-a`, 2,000,000 in and 100,000 out: $2.50. `model-b`, 1,000,000
  and 1,000,000, on 2026-12-31: $4.50; on 2027-01-01: $9.00. Replayed:
  $0. `model-z`: unknown, the reason naming `prov/model-z`. No `tokens`:
  unknown. A network of `model-a` members (1,000,000/0 and 0/100,000):
  $1.50; with one `model-z` member: unknown.
- The committed `profiles/prices.json`: every entry has provider,
  model, a non-negative input and output, an `https://` source and a
  `checked` date; no two periods of one model overlap.
- The report, on two documents. Ticket 0201: assign, dev-worker,
  `model-a`, 2,000,000/100,000, 300 s, 2026-11-10T10:00:00Z ($2.50);
  review, test-reviewer, `model-a`, 400,000/20,000, 60 s,
  2026-11-10T10:10:00Z ($0.50); rework, dev-worker, `model-a`,
  1,000,000/40,000, 200 s, 2026-11-11T09:00:00Z ($1.20); history from
  2026-11-10T09:00:00Z to 2026-11-11T10:00:00Z. Ticket 0202: assign,
  dev-worker, `model-z`, 50 s, 2026-11-11T12:00:00Z (unknown); review,
  test-reviewer, replayed, 5 s, 2026-11-11T12:05:00Z ($0); history from
  2026-11-11T11:00:00Z to 2026-11-11T12:30:00Z. Expected: 0201 $4.20, 3
  runs, 0 unknown, 560 s of agents, 90,000 s elapsed; 0202 $0.00, 2
  runs, 1 unknown, 50 s (the replayed review's 5 s not counted), 5,400
  s; dev-worker $3.70, 3 runs, 1 unknown, 550 s; test-reviewer $0.50, 2
  runs, 60 s; 2026-11-10 $3.00, 2 runs;
  2026-11-11 $1.20, 3 runs, 1 unknown; in all $4.20, 5 runs, 1 unknown.
- The budget, on 0201's spend: `SPEND_BUDGET_TICKET_USD=4.20` stops,
  4.21 does not; on a day whose entries are 0201's $1.20 alone,
  `SPEND_BUDGET_DAY_USD=1.20` stops, 1.21 does not, and yesterday's
  entries never count; 0202's unknown entry stops either limit; empty
  limits never stop; `x`, `0` and `-1` are refused naming the key.
- The tower: a step whose result carries a `task`, a `review` and a
  `product` appends three entries with the cost beside the tokens; a
  `product` with `consulted: false` appends none; a result with no run
  appends none. `rework` over a limit runs the step with `SPEND_STOP`
  set to the line, under it with `SPEND_STOP` empty; `assign` over a
  limit leaves the ticket at `opened` with the line as the history
  error, runs no step and publishes nothing.
- `core/steps/rework.sh` with `SPEND_STOP` set, at attempt 1 of 4:
  `needs-human`, the rejected attempt appended to `rework`,
  `budget.line` the line, and no crew run.
- `core/spend.sh`: one `GO: spend — ` line with no ticket; one `NO-GO:
  spend — ` line with no `STATE_STORE`.

## Alternatives rejected

- **The cost inside `task()`** (`core/tower.py`), beside the tokens of
  `task`, `tasks` and `review`. It would change every document key the
  logbook copies and every test that pins them, and it would still lose
  the reviews a rework clears; the ledger keeps every run once.
- **Prices in `profiles/l1.env`.** A profile names one model; the price
  of the others would have nowhere to live, and env keys cannot hold a
  model id.
- **An unknown cost counted as zero under a limit.** The budget would
  pass a ticket whose spend nobody knows: the limit exists to stop that.
- **`needs-human` at `assign`.** See §4: no attempt to decide about, and
  it would hold a perimeter for a ticket that never started.
- **Stopping `review` and `clarify` too.** A review stopped halfway
  leaves a PR with no verdict; the limit acts at the one point where
  stopping leaves a clean state, between attempts.

## Consequences

- With the limits empty nothing changes: `./go demo` replays, every run
  costs 0, and `./go spend` says so.
- A price change is a commit to `profiles/prices.json` with its new
  source date; a run keeps the cost computed when it was recorded.
- The console slice (after stream A) adds `spend` to the logbook and a
  panel; the release role's run joins the ledger when promote records it.

## Amendment 2026-11-13: the spend in the logbook (1.5), the budget's reason on the card, the panel

The sprint's console slice (docs/PLAN.md, sprint 2026-11-12, "Fetta
finale"). Three gaps found reading the code on 2026-11-13: the logbook
does not read `spend` (§3), so a console has nothing to show; a ticket the
budget sent to `needs-human` shows the last rejected attempt's gate line
(`rework[-1].line`, `core/logbook.py`'s `outcome`), not the budget's
reason; and `./go ci` after `./go dora` or `./go logbook` is a NO-GO on
`secrets`, because gitleaks' `generic-api-key` rule reads the logbook's
threshold rows (the row whose `key` is LATENCY_P95_MAX_DELTA, entropy 3.65,
`.crew/logbook.json`, gitleaks 8.30.1, reproduced 2026-11-13).

1. **Logbook 1.5** (amends ADR 0047 §2, as ADR 0078 did for 1.4):
   additions only, all optional, so a 1.5 logbook validates against every
   1.x schema and a 1.4 logbook stays readable. Schema
   `core/logbook/1.5.schema.json`; golden
   `fixtures/logbook/golden-1.5.json`, written by the reader over the stub
   sources; `golden-1.4.json` stays as the old-logbook fixture. ADR 0082's
   amendment adds `dora` to the same minor.
2. **`spend`, top level**: `core/spend.py`'s `report` over the tower
   documents the logbook read for `tickets` (§5, the same numbers `./go
   spend` prints), plus `limits` and the command's own words:
   `{tickets: {<id>: {cost_usd, runs, unknown, agent_s, elapsed_s}},
   roles: {<role>: {cost_usd, runs, unknown, agent_s}}, days: {<YYYY-MM-DD>:
   {cost_usd, runs, unknown}}, total: {cost_usd, runs, unknown}, limits,
   lines, summary}`. `limits` is `{SPEND_BUDGET_TICKET_USD,
   SPEND_BUDGET_DAY_USD}`, each a number or null (no limit), read from
   `policy/rework.env` as the tower reads it (§4), or `{ok: false, error}`
   naming the key when a value is malformed. `lines` are the lines `./go
   spend` prints on stderr without their `[spend] ` prefix, in its order
   (per ticket, per role, per day, then the budget); `summary` is the
   detail of its stdout line (`<n> tickets, <r> runs, $<x> known, <u> of
   cost unknown`). Tickets the logbook cannot read make `spend` the same
   `{ok: false, error}`. No clock is read: the report has none.
3. **The budget line.** `./go spend` gains one stderr line, the last:
   `budget: SPEND_BUDGET_TICKET_USD=$<x>` or `SPEND_BUDGET_TICKET_USD no
   limit`, then the same for `SPEND_BUDGET_DAY_USD`, joined by `, `; a
   malformed value is `budget: <the error §4 names>`. The stdout line is
   unchanged.
4. **The budget's reason on the card.** A `needs-human` ticket's
   `outcome` gains `stop`: `"budget"` when its document carries
   `budget.line` (§4, `rework` over a limit), and then `line` is that
   line; `"cap"` otherwise, with `line` the last rework entry's, as in 1.4.
   `judge` stays the last rework entry's: that judge still rejected the
   attempt. The console's now line for a budget stop is `Your turn: <the
   budget line>; a person decides`; for the cap it is 1.4's.
5. **The panel.** The Overview screen gains `section#spend`, after "Where
   the tickets are": `#spend-summary` holds `summary`, and the lines in
   three lists, `ul#spend-tickets`, `ul#spend-roles`, `ul#spend-days`, one
   `li` per line verbatim, then `#spend-budget` with the budget line. The
   page formats no number of its own: every word is the command's. A
   `spend` the logbook could not read shows its error; a logbook before 1.5
   says it has no spend.
6. **`./go ci` after the logbook.** `ci/gitleaks-worktree.toml` gains one
   allowlist, scoped like the cosign key's (ADR 0025, Consequences): rule
   `generic-api-key` only, path `^\.crew/logbook\.json$` (the default
   `LOGBOOK_FILE`) AND a line that is exactly a threshold row's key,
   `"key": "<UPPER_SNAKE>"`. The same row at any other path, any other
   rule, and any other value in that file (a lower-case or mixed key, a
   token) are still findings (checked by planting each, 2026-11-13).
   `ci/secrets.sh` adds `.crew/logbook.json` to the paths it proves
   ignored and untracked before it trusts an allowlist. Not writing the
   rows another way: `key` is a 1.0 field, and a renamed or reformatted
   field would break every logbook consumer to suit one scanner rule.

### The release role's run

Still not in the ledger: `core/promote.sh` runs outside the tower and
names no ticket, and recording its run is a new write path ADR 0079 §2
rules out. It needs its own ADR; at L0 the brief is rendered by a script
and no model runs, so nothing is missing from `./go demo`.

### Tests, from this amendment

- `core/tests/test_spend.py`: `lines` gives the report's lines in §5's
  order and the budget line last, for no limits, for a ticket limit of
  $1.00 and for a malformed value; `./go spend`'s stderr ends with the
  budget line and its stdout is one line, unchanged.
- `core/tests/test_logbook.py`, over the stub sources, whose tower
  documents now carry a ledger: ticket 0003, assign at attempt 1
  (dev-worker, 1,000,000 in and 20,000 out, $0.825, 300 s,
  2026-11-03T08:05:00Z) and rework at attempt 2 (dev-worker, 400,000 and
  10,000, $0.3375, 120 s, 2026-11-03T08:15:00Z); ticket 0010, assign
  replayed ($0, 48 s, 2026-11-02T06:10:00Z) and review by the
  test-reviewer at a model with no price (cost unknown, 30 s,
  2026-11-02T06:40:00Z); the stub's `policy/rework.env` sets
  `SPEND_BUDGET_TICKET_USD=1.00`. Expected, by hand: 0003 $1.1625, 2
  runs, 0 unknown, 420 s of agents, 1,200 s elapsed; 0010 $0, 2 runs, 1
  unknown, 30 s (the replayed 48 s not counted), 5,400 s; every other
  ticket 0 runs, with its elapsed time; dev-worker $1.1625, 3 runs, 420
  s; test-reviewer $0, 1 run, 1 unknown, 30 s; 2026-11-02 $0, 2 runs, 1
  unknown; 2026-11-03 $1.1625, 2 runs; in all $1.1625, 4 runs, 1 unknown;
  `summary` `9 tickets, 4 runs, $1.16 known, 1 of cost unknown`; limits
  `{SPEND_BUDGET_TICKET_USD: 1.0, SPEND_BUDGET_DAY_USD: null}` and the
  last line `budget: SPEND_BUDGET_TICKET_USD=$1.00, SPEND_BUDGET_DAY_USD
  no limit`. Ticket 0007 (the cap) has `stop: "cap"` and its 1.4 line; the
  same document with `budget: {line}` has `stop: "budget"` and that line;
  every ticket not at `needs-human` has no `stop`. Tickets unread: `spend`
  is the same error. A malformed limit: `limits` is an error naming the
  key, the report is still there.
- `ci/logbook.sh`: the golden is `golden-1.5.json`; the 1.5 fields checked
  by hand against the stub as above, each run again with one fact planted
  wrong; `golden-1.4.json` validates as the old fixture.
- `ci/console-ui.py`: over the golden, `section#spend` on `#overview`
  holds `summary` and every line of `spend.lines` in its list, in order;
  over a logbook derived from it with `spend` unread, the error; over a
  1.4 logbook, the no-spend sentence; ticket 0007 derived with `stop:
  "budget"` and a budget line has that line in its now line, on the card
  and in `#your-turn`.
- `ci/secrets.sh`'s allowlist: proven as in item 6 on the pinned
  gitleaks, recorded here; `./go ci` after `./go dora` is GO.

## Amendment 2026-11-14: an ADK run's output counts its thinking tokens

*Owner's decision, 2026-11-14, with ADR 0090's turn-cap amendment.*

- **Measured.** The demo intake's second architect run (Gemini 3.8 Flash,
  high thinking, stopped at `ADK_MAX_TURNS=10`) reported, summed over its
  ten responses' `usage_metadata`: 59,753 prompt, 443 candidates and 2,696
  thoughts tokens, 62,892 total. The ledger priced 59,753 in and 443 out,
  **$0.046**. Google bills thinking as output (the price table's own
  note: "output includes thinking tokens"): 59,753 in and 3,139 out is
  **$0.057**.
- **Decision.** The ADK harness (`lights_out_adk.run`) reports
  `tokens.output` as the candidates plus the thoughts of every response.
  `tokens.total` is unchanged, and now equals input plus output for these
  runs. The ledger's formula (§2) is unchanged; its input is right.
- **Consequence.** Every live ADK run's cost goes up by its thinking:
  the console's spend panel, the role matrix and `./go spend` show the
  higher figure from the next run on. Runs already in a ledger keep the
  number they were priced at; a replayed run still costs $0.
- The gap in "The gap between the cost and the bill" that remains is the
  coding harnesses': Gemini CLI's `total` above input plus output is not
  priced, as before. It is not guessed at.
