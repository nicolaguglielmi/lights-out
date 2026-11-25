# ADR 0088 — The provider scoreboard: per provider and harness, from the run logbooks and a curated dataset whose every row names its source

- Status: accepted (M55)
- Date: 2026-11-14
- Milestone: M55 (docs/PLAN.md, "Sprint 2026-11-14 — Il flusso a colpo d'occhio (M52–M56)", stream B)

## Context

M24 ran the same ten tenacity tickets on two providers. Anthropic
(`claude-code`, `claude-sonnet-5-5`) merged 10 of 10; Gemini
(`gemini-cli`, `gemini-3.8-flash`) merged 7 of 10, and its three other
tickets stopped at the dev-worker's `TOKEN_BUDGET`. OpenAI was not run,
because there was no key. The results sit in three handoffs, written in
prose and tables, and nowhere else a visitor can read them:

- phase B, `.claude/context/handoffs/claude-m24-phase-a-5dacb7.md` §6;
- phase C, `docs/plans/m24-phase-c-handoff.md` §3;
- the Gemini leg, `.claude/context/handoffs/claude-m24-phase-d-gemini-1628f2.md` §2 and §5.

Other projects publish a per-model table, refreshed every hour, of
first-try green CI, merges and cost. The owner wants the same for
lights-out, on its own terms: NO-GOs are published like GOs, and a number
says where it comes from (CLAUDE.md).

Read on 2026-11-14, the three towers behind those handoffs still exist
on the owner's machine. They are outside the repository, so a site build
cannot read them. They also lack facts the handoffs carry:

- **A budget-stopped run is not in the tower.** The Gemini tickets 04,
  09 and 10 end at `opened` with `assign` → `step exited 1`. The
  `TOKEN_BUDGET` reason, the launches and the tokens exist only in the
  run logs and the handoff (its §4, finding 1).
- **The cached tokens are not in the tower.** `run.json` drops
  gemini-cli's `cached` (finding 2).
- **Phases B and C have no spend ledger.** They ran on 2026-11-11, and
  the ledger landed with ADR 0083 on 2026-11-12.

## Decision

1. **`./go scoreboard`** runs `core/scoreboard.sh`, a wrapper within ADR
   0052's 30 lines. The logic is in `core/scoreboard.py`, standard
   library only. It reads two kinds of record:
   - **run logbooks** (`lights-out/logbook/1.x`, ADR 0047 §2), the files
     named in `SCOREBOARD_LOGBOOKS` (separated by spaces, none by
     default). A logbook is read as `./go dora` reads it: never the
     tower behind it.
   - **a curated dataset** (`lights-out/scoreboard-data/1`), the file
     named by `SCOREBOARD_DATA`, default `docs/scoreboard/m24-tenacity.json`.

   Both become rows, one per ticket and dev-worker (provider, harness,
   model). The rows are grouped by that triple.

2. **A row**: `ticket`, `provider`, `harness`, `model`, `merged`
   (true or false), `attempts` (the dev-worker attempts the gates
   judged, 0 when no PR was opened), `reworks`, `nogos`, `stop`,
   `tokens`, `cost_usd` and `source`.
   - `nogos` is a list of `{gate, reason}`.
   - `stop` is null or `{kind, line}`.
   - `tokens` is `{input, output, cached}`, each a whole number or null.
   - `cost_usd` is `{ledger, list, cached}`, each a number or null.
   - `source` is `{handoff, run}`. Both are non-empty strings: the
     document and section the numbers were read from, and the record
     behind them (a tower document, a PR, a run's log).

   A dataset row without both strings is a NO-GO that names its ticket
   and provider. **A number with no source does not enter**, which is
   the plan's rule.

3. **From a logbook**, per ticket:
   - The dev-worker runs are the ticket's `tasks`. A network attempt
     (ADR 0036) is keyed by its members' providers and models, joined
     with `+`, and its harness is `network`.
   - A ticket with no task is not a row. A ticket whose every run has
     `replayed: true` is not a row either: no model was asked (ADR 0017
     §2). The output counts the replayed tickets separately. At L0,
     after `./go demo`, that count is the only thing a logbook gives.
   - The row's other fields:
     - `merged` is a `merge` with a sha;
     - `attempts` is the ticket's `attempt`;
     - `reworks` is the length of `rework`;
     - `nogos` holds every `NO-GO:` line of `rework`, followed by the
       last `ci.line` when it is a NO-GO and the ticket is not merged.
     - `stop` is the outcome's `stop`: `budget` becomes `SPEND_BUDGET`,
       `cap` becomes `REWORK_CAP`, and `line` is the outcome's line.
     - `tokens` is the sum of input and output over the runs. `cached`
       stays null, because the logbook does not carry it.
     - `cost_usd.ledger` is the logbook's `spend.tickets[id].cost_usd`,
       which covers every role of the ticket. `list` and `cached` stay
       null.
     - `source.handoff` is `logbook <file>`, and `source.run` is
       `<source.commit> tickets/<id>`.

   A `TOKEN_BUDGET` stop does not reach the tower (Context), so a
   logbook cannot show one. The dataset carries it from the handoff.

4. **A NO-GO line** `NO-GO: <gate> — <reason>` gives `{gate, reason}`.
   The tower's summary line `NO-GO: gates — <inner>: NO-GO: <inner> —
   <reason>` gives the inner gate and its reason, so the summary is not
   counted as a gate. A line that does not parse is kept whole:
   `{gate: "unparsed", reason: <line>}`.

5. **The dataset's other fields:**
   - `scope`: one sentence printed on the page and on stderr. It says
     how many tickets, on which repository, and how many runs. A dataset
     with no scope is a NO-GO.
   - `totals`: provider-level costs a row cannot hold. Each total is
     `{provider, harness, model, basis, usd, bound, note, source}`.
     `basis` is one of `ledger`, `list` or `cached`. `bound` is `at
     most`, `about` or `exactly`. A total for a group with no rows is a
     NO-GO.
   - `not_run`: `{provider, harness, why, source}` for each provider
     that was planned and not run. It is shown as **not run**, with its
     reason, and never as an empty row or a zero. A provider listed both
     here and in the rows is a NO-GO.

6. **The board, per group:**
   - `tickets`, `merged`, `first_attempt` (merged with no rework) and
     `reworks` (the sum);
   - `nogos`, counted by gate, each with its reasons;
   - `budget_stops`, the rows whose stop kind is `TOKEN_BUDGET` or
     `SPEND_BUDGET`;
   - `tokens_per_ticket`: the mean of input plus output over the rows
     that carry tokens, rounded to a whole token, with how many rows it
     covers;
   - `cost_per_ticket`, per basis. It is the group's total for that
     basis divided by its tickets when the dataset gives a total, with
     the total's bound. Otherwise it is the sum of the rows divided by
     their number, when every row of the group has that basis. A basis
     no total and no complete set of rows gives is **not recorded**,
     and the board never fills it in.

   Every basis is shown, recorded or not. Each total's `note` becomes a
   footnote on its group. So when a group has a `ledger` and a `list`
   figure that differ, the page shows both, and the footnotes say why
   they differ. The Gemini handoff does the same.

7. **The output.** stderr gets the scope, then one line per group in a
   fixed order, then one line per provider not run. stdout gets one
   line of the gate contract: `GO: scoreboard — <g> groups run, <n> not
   run, <t> tickets; <html>`, or `NO-GO: scoreboard — <why>` when a file
   is missing or not JSON, a schema is unknown, or a rule above refuses
   it. The page goes to `SCOREBOARD_OUT`, by default
   `.crew/scoreboard/index.html`. It is one self-contained HTML file
   with no script and no external resource, light and dark from
   `prefers-color-scheme`, every text at WCAG AA.

   The page says what the numbers are and are not: the dataset's scope,
   that the rows are one run per ticket and provider (no variance), and
   that this is not a benchmark. It shows the board, the NO-GOs by gate
   and reason, every row with its source, and the providers not run. A
   number is printed with the bound its source gave it.

8. **Published with the site.** The site workflow runs `python3
   core/scoreboard.py --html _site/scoreboard/index.html` on the
   committed dataset. The landing page links `scoreboard/`.

9. **The M24 dataset** is `docs/scoreboard/m24-tenacity.json`, 20 rows
   with two totals per provider and one `not_run`. Every row was read
   from its handoff and checked against its tower document on
   2026-11-14: attempts, merge, PR, rework lines, tokens per attempt and
   the Gemini spend ledger. Where the two differ, the row takes the
   handoff and says why in its source. Example: Gemini's tenacity-03
   counts both launches in the handoff and only the second in the tower.

## Tests, from this ADR

`core/tests/test_scoreboard.py`.

### A dataset of four rows

| row | group | merged | attempts | reworks | nogos | stop | tokens in/out |
|---|---|---|---|---|---|---|---|
| t-1 | prov-a/h-a/m-a | yes | 1 | 0 | — | — | 1,000/100 |
| t-2 | prov-a/h-a/m-a | yes | 2 | 1 | `mutation`: `3/4 (0.750 < 0.800)` | — | 3,000/300 |
| t-3 | prov-b/h-b/m-b | no | 0 | 0 | — | `TOKEN_BUDGET` | 5,000/500 |
| t-4 | prov-b/h-b/m-b | yes | 1 | 0 | — | — | 2,000/0 |

The dataset also has three totals (prov-a `list` at most 1.00; prov-b
`ledger` exactly 4.00 and `list` at most 10.00) and one `not_run`
(prov-c/h-c, "no key").

Expected:

- prov-a: 2 tickets, 2 merged, 1 at the first attempt, 1 rework,
  mutation 1, 0 budget stops, 2,200 tokens per ticket over 2, list at
  most $0.50 per ticket, ledger and cached not recorded, and its list
  total's note as its one footnote.
- prov-b: 2 tickets, 1 merged, 1 at the first attempt, 0 reworks, no
  NO-GO, 1 budget stop, 3,750 tokens per ticket, ledger $2.00, list at
  most $5.00, and both totals' notes as footnotes.
- prov-c: not run, "no key".
- stdout: `GO: scoreboard — 2 groups run, 1 not run, 4 tickets; <html>`.
- The page holds:
  - the scope;
  - "not a benchmark";
  - every row's two source strings;
  - "not run" with "no key";
  - the footnote;
  - no `<script` and no `http` resource.

### The refusals, one NO-GO line each

- a row with an empty `source.handoff`, and one with no `source.run`,
  each named;
- a total for a group with no rows;
- prov-a in both `not_run` and the rows;
- no `scope`;
- an unknown `schema`;
- a missing file.

### A logbook of four tickets

| ticket | tasks | rework lines | final | spend |
|---|---|---|---|---|
| L1 | p/h/m: 100/10, then 200/21 | `NO-GO: gates — mutation: NO-GO: mutation — 1/2 killed (0.500 < 0.769)` | merged | 0.5 |
| L2 | one replayed task | — | — | — |
| L3 | p/h/m: 50/5 | `NO-GO: test — 2 failed`, `CHANGES-REQUESTED — reviewer` | outcome `stop: budget`, line `SPEND_BUDGET_TICKET_USD=$1.00 reached` | 0.3 |
| L4 | none | — | — | — |

Expected rows:

- L1: merged, attempts 2, reworks 1, nogos `[mutation: 1/2 killed
  (0.500 < 0.769)]`, tokens 300/31, ledger 0.5, source `logbook
  <file>` and `<commit> tickets/L1`.
- L3: not merged, reworks 2, nogos `[test: 2 failed]`, stop
  `SPEND_BUDGET` with its line, tokens 50/5, ledger 0.3.
- L2 counts as one replayed ticket, and L4 is not a row.

Expected group p/h/m: 2 tickets, 1 merged, 0 at the first attempt, 3
reworks, mutation 1 and test 1, 1 budget stop, 193 tokens per ticket,
ledger $0.40 per ticket, list not recorded.

### The committed dataset

- It loads with no refusal.
- Anthropic: 10 tickets, 10 merged, 8 at the first attempt, 2 reworks,
  mutation 2.
- Gemini: 10 tickets, 7 merged, 7 at the first attempt, 0 reworks, 3
  budget stops, all `TOKEN_BUDGET`.
- OpenAI: not run.

## Alternatives rejected

- **Read the three towers.** They are on one machine, outside the
  repository, and they lack the budget stops and cached tokens
  (Context). Committing them would also commit every transcript and
  patch.
- **Commit the logbooks of those towers.** That has the same gaps. A
  logbook is the console's read model, and a published table is not
  what it is versioned for.
- **Fill a missing basis from the price table** (tokens × ADR 0083's
  prices). It would give Anthropic a per-ticket ledger figure that no
  ledger ever recorded, and a Gemini cached figure from a cached price
  `profiles/prices.json` does not hold. The board says "not recorded".
- **An hourly refresh.** M24 was one run per ticket and provider. A
  schedule would re-render the same numbers, or spend on providers,
  which this sprint does not do.
- **Call it a benchmark.** It is 10 tickets on one repository, run
  once. The page says so.

## Consequences

- A visitor reads M24's results, NO-GOs and budget stops included, with
  the handoff and run behind each row.
- A new run adds rows to a dataset, or a logbook to
  `SCOREBOARD_LOGBOOKS`, and the board needs no code change.
- `./go demo` does not run the scoreboard, and its time does not change.
