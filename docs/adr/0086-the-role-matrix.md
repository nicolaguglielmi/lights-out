# ADR 0086 — The role matrix: one row per role on the Overview, its provider, harness, thinking, the tokens of its last run against its budget, and its state, read from the profile, `roles/` and the ledger

- Status: accepted
- Date: 2026-11-14
- Milestone: M53 (docs/PLAN.md, "Sprint 2026-11-14 — Il flusso a colpo
  d'occhio")
- Amends: ADR 0047 §2 (logbook 1.6 carries `roles`, the same minor as
  ADR 0085)

## Context

Read on 2026-11-14:

- Six roles, one directory each under `roles/` with a `role.env`:
  `THINKING`, and for the three that run in a CLI harness `MAX_TURNS`,
  `TIMEOUT_S` and `TOKEN_BUDGET`; the three ADK roles (`product`,
  `release`, `ops-triage`) name `HARNESS=adk` and have no
  `TOKEN_BUDGET`. `ci/roles.sh` holds the number to six.
- The provider and the model are the profile's (`MODEL_PROVIDER`,
  `MODEL`), the harness the role's `HARNESS` or the profile's
  `AGENT_HARNESS` (`core/crew.sh`, `core/steps/clarify.sh`). No id of a
  model is written in `core/`, `gates/`, `verify/` or `roles/` (ADR 0040
  §5): the console must not start.
- What a role actually ran on is in the spend ledger (ADR 0083 §3): every
  run's role, harness, provider, model, tokens and time.
- The product's claim is vendor neutrality: any role on any of three
  providers, each harness under the same guard. Nothing on screen shows
  it. M24 ran the same ten tickets on Anthropic and on Gemini; a reader of
  the console could not tell which.

## Decision

### 1. `roles` in the logbook

A list, one row per directory of `roles/` that holds a `role.env`, in the
order of `core/network.py`'s table (`roles`, the order `#network` draws
them), any other directory after them by name:

`{role, configured, last, used, budget, running, state, line}`

- `configured`: `{provider, model, harness, thinking}` as the next run
  would get them: `provider` and `model` from the reader's environment
  (`MODEL_PROVIDER`, `MODEL`, the profile `./go` loads), null when empty
  (L0); `harness` the role's `HARNESS`, else `AGENT_HARNESS`; `thinking`
  the role's `THINKING`. Read, never written: the logbook names the model
  the profile names.
- `budget`: the role's `TOKEN_BUDGET` as a whole number, null when the
  role has none (the ADK roles: their budget is the step's timeout).
- `last`: the role's newest ledger entry over every ticket the logbook
  read (by `at`, then ticket id), as `{ticket, step, attempt, at, harness,
  provider, model, replayed, tokens, cost_usd, failed}`, null when the
  role never ran. `failed` is ADR 0087's: the run stopped without a PR.
- `running`: ADR 0087's live run of the role, `{ticket, tokens, budget,
  warn}`, null when none. Only the dev-worker has one (§5 there).
- `used`: the tokens of the current run when `running`, else of `last`:
  `tokens.total`, else `input + output`, null when the run reports
  neither. The unit is the budget's (ADR 0041 §5: input with cache reads
  and writes, plus output). A replayed run's tokens are the recording's,
  and `last.replayed` says so.
- `state`: `running` with `running`, else `ran` with a `last`, else
  `idle`.
- `line`: one sentence, the matrix's words, the same in any consumer:
  `running on <ticket>: <used> of <budget> tokens` (with ` — near the
  budget` when ADR 0087's warning holds), `last ran on <ticket> (<step>,
  attempt <n>) at <at>: <used> tokens of <budget>` (`no budget` when
  null, `, replayed` when replayed, `, stopped: <failed>` when failed),
  or `has not run in this record`.

No `roles/` directory makes `roles` `{ok: false, error: "no roles/
directory: the six roles are its subdirectories"}`; a `role.env` that
cannot be read leaves that row's `configured` and `budget` null with an
`error` naming the file (`roles/<role>/role.env is not UTF-8`, or the
system's reason). The directory is `ROLES_DIR`, default `roles`.

### 2. The panel

The Overview gains `section#roles` ("Who runs each role"), after
"Waiting for you": a table `table#role-matrix` with one
`tr.role-row[data-role]` per row, columns Role, Provider, Harness,
Thinking, Tokens and budget, State.

- Provider shows `configured.provider` and the model, and, when the last
  run's differ, `last ran on <provider> <model>` beside them; at L0,
  `replay (recorded runs)`. `td[data-provider]` carries the configured
  provider, empty at L0.
- Tokens and budget: `used` against `budget` as text (`2,438,113 of
  2,600,000`) and as a meter (`meter` with `min=0`, `max=budget`,
  `value=used`, and its text in an `aria-label`) when both are numbers;
  `no budget` otherwise. A run over the budget fills it and says
  `over`.
- State: the row's `line`.
- Above the table, one sentence counted from the rows, `roles_summary`,
  a string the logbook carries beside `roles` (null when `roles` is
  unread), so the page adds no word: `<n> roles on <k> provider: <the
  configured providers, sorted, joined by ", ">` (`providers` when more
  than one), or `<n> roles on no provider: the profile replays recorded
  runs` when none is configured; then, when any role ran, `; last runs on
  <the distinct provider/model of every last, sorted, joined by ", ">`.
- A logbook before 1.6 has no `roles`: the panel says so.

## Tests, from this ADR

- `core/tests/test_logbook.py`, over the stub sources with a stub
  `roles/` of six directories (the stub's `role.env` files copy the
  committed ones' keys) and `MODEL_PROVIDER=prov`, `MODEL=model-a`,
  `AGENT_HARNESS=fake-cli` in the environment:
  - six rows in the network table's order; `configured` of the
    dev-worker `{prov, model-a, fake-cli, high}`, of `product` harness
    `adk`; `budget` 2,600,000 for the dev-worker, 1,500,000 for the
    test-reviewer, 600,000 for the architect, null for the ADK three;
  - the dev-worker's `last` is 0003's rework at attempt 2
    (2026-11-03T08:15:00Z, 410,000 tokens); the stub's run in flight on
    0003 (ADR 0087 §4, 2,210,000 counted) makes it `running`, `used`
    2,210,000, `line` `running on 0003: 2,210,000 of 2,600,000 tokens —
    near the budget`; without the stub's `crew/` it is `ran`, `used`
    410,000, `line` `last ran on 0003 (rework, attempt 2) at
    2026-11-03T08:15:00Z: 410,000 tokens of 2,600,000`; the
    test-reviewer's `last` is 0010's review (30,900 tokens of
    1,500,000); the four others `idle`, `has not run in this record`;
  - with no `MODEL_PROVIDER` and `MODEL` (L0) `provider` and `model`
    null; with `roles/` missing, `roles` is the error; a row's unreadable
    `role.env` is that row's error;
  - `roles_summary` for the stub: `6 roles on 1 provider: prov; last runs
    on gemini/gemini-3.8-flash, prov/model-x`; at L0, `6 roles on no
    provider: the profile replays recorded runs; last runs on …`;
  - `core/`, `gates/`, `verify/`, `roles/` gain no model id (the
    existing `ci/vendor-free.sh` check), and the stub's model ids are
    the test's own.
- `ci/logbook.sh`: `roles` in the golden checked by hand as above, each
  run again with one fact planted wrong.
- `ci/console-ui.py`, over the golden: `section#roles` after
  `#your-turn`, one `tr.role-row` per row in order with its provider,
  harness, thinking and line verbatim, a `meter` where both numbers are
  there, `no budget` where not; over a 1.5 logbook, the sentence and no
  rows; contrast in both themes; nothing POSTed.

## Alternatives rejected

- **The panel on the Thresholds screen.** Those are the numbers that stop
  a change; this is who works and on what. The Overview is where a
  person looks first.
- **The model ids in the page, for the L0 demo.** L0 calls no model; the
  row says replay, and the last run's model comes from the recording.
- **A per-role provider key in `role.env`.** Not asked, and it would be a
  routing feature; the matrix shows what is configured, and a later ADR
  that routes roles to providers (the M24 note of 2026-11-07) fills the
  same columns.
- **The sum of the role's tokens over the ticket.** `TOKEN_BUDGET` is per
  run (ADR 0021 §7); a sum against it would read as a stop that never
  happens.

## Consequences

- A reader sees on one screen which provider and harness each role runs
  on, and how close its last run came to its budget. On M24's Gemini
  leg the dev-worker's row would have read `stopped` three times.
- Stream B's snapshot gets the matrix with the logbook.
