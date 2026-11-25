# ADR 0085 — The flow strip: one row of nodes per ticket, from the request to 100%, each with its verdict, its time and its one-line reason, computed by the logbook and drawn on the card, its back and its timeline

- Status: accepted
- Date: 2026-11-14
- Milestone: M52 (docs/PLAN.md, "Sprint 2026-11-14 — Il flusso a colpo
  d'occhio")
- Amends: ADR 0047 §2 (logbook 1.6 carries `tickets[].flow` and
  `ci.seconds`); ADR 0015 (the `gates` recipe also records each gate's
  seconds); ADR 0063 §3 (the band strip stays, the flow strip sits under it)

## Context

Read on 2026-11-14:

- A card says whose turn it is (ADR 0063), its attempts as pips and its
  outcome chip (ADR 0068); its back and the timeline screen list the
  phases as rows. Nothing shows the whole path at once: which gate a
  ticket is stopped at, what is still ahead, and how long each part took.
  The owner looked at a dashboard that draws each change's flow as a line
  of nodes and liked how it shows it; we take the way of showing, not that
  project's model (no autonomy ladder, no auto-merge to 100%).
- The facts are already in logbook 1.5, scattered: the history's `at` of
  every step, the ci's gate lines in the order they ran (ADR 0071 §1),
  the why of a NO-GO (ADR 0078), the stages' `started_at` and `judged_at`
  (ADR 0080), the human answer with who, how and why (ADR 0079).
- One fact is missing: how long each gate took. The `gates` recipe
  (`Makefile`) runs the gates one after the other and keeps only their
  lines; the ci step's history entry gives the time of all of them
  together.
- The console's rule (its header comment): the page computes no verdict of
  its own. A strip whose node states the page derived would break it, and
  stream B's static snapshot of the console would have to derive them
  again.

## Decision

### 1. The nodes, in order

Ticket, perimeter, each gate in the order the tower runs them, PR,
canary, human gate, 100%:

| node | what it is | from |
|---|---|---|
| `ticket` | the request and the spec gate, up to `opened` | history `request`/`open`, `spec`; `questions` |
| `perimeter` | `assign`: the perimeter claim (ADR 0053 §5), the dev-worker's first attempt and the crew's perimeter check, up to the PR | history `assign`, `pr` |
| `gate` × 8 | one per gate of the Makefile's `GATES`, in that order (`core/network.py`'s table, which `ci/logbook.sh` proves equal to the Makefile) | `ci.gates`, `ci.failed`, `ci.seconds` |
| `pr` | what happens to the PR after its gates: the review when the risk asks for one, then the merge | history `ci`→`review`/`ci-passed`, `review`, `merge` |
| `canary` | the release's stages | the release log's stages of the ticket's release |
| `human` | go-for-launch | the release log's answer (ADR 0079 §1) |
| `full` | 100% of the traffic | the same answer, when it is GO |

"PR" follows the gates although the PR is opened before them: the node is
the PR's fate once judged, which is what the owner's order names.

### 2. A node

`{node, gate, verdict, started_at, ended_at, seconds, line}`:

- `gate`: the gate's name on a `gate` node, null on the others.
- `verdict`: `GO`, `NO-GO`, `running` or `not-reached`. `running` means
  the tower requested the node's step and the record has no verdict yet
  (the step runs or waits in the bus), or a person's answer is awaited.
- `started_at`, `ended_at`: the record's own times, null where it has
  none. `seconds`: `ended_at − started_at` in whole seconds when both are
  there; on a `gate` node, the gate's own seconds from `ci.seconds` (§4);
  null otherwise. Never the reader's clock: a running node has
  `started_at` and no `seconds`.
- `line`: one line saying why, the record's own words where it has them
  (the spec line, the assign refusal, the gate's line, the review's first
  line, the stage's verdict, the release's reason, who answered), else a
  fixed sentence of this ADR.

### 3. The rules, node by node

A ticket stranded by a failed step (its last history entry has an
`error`) shows that error as the `NO-GO` line of the node its step
belongs to (`clarify`, `answer`, `spec` → ticket; `assign` → perimeter;
`ci` → the first gate, every other gate not reached; `review`, `merge` →
pr; `mainline`, `release` → canary), and every node after it not reached.

- **ticket.** Opened directly (`open` in the history): `GO`, line `opened
  directly: no request, no spec gate`, `started_at` = `ended_at` = that
  entry's `at`, `seconds` null (no spec gate ran). Otherwise
  `started_at` is the `request` entry's `at`; `GO` at the last `spec`
  entry that moved to `opened`, `ended_at` its `at`, line the spec's line
  (`GO: spec` when the document has none). At `questions` after a spec
  entry that moved to `questions`: `NO-GO`, `ended_at` that entry's
  `at`, line `NO-GO: spec — <the last question>`. At `requested`, `spec-ready`, or `questions` asked by the
  product role: `running`, line `the product role asked: <question>; it
  waits for an answer` or `the product role and the spec gate`.
- **perimeter.** `started_at` is the `at` of the entry that brought the
  ticket to `opened`. `GO` at the first `assign` entry without an error,
  `ended_at` its `at`, line `PR #<n> opened, every file inside <globs
  joined by ", ">` (`the PR opened, …` when the document has no PR
  number; the crew checks the perimeter before the PR exists, ADR 0021
  §5). At `opened` with no error last: `running`, line `the
  dev-worker, attempt 1`. Refused or failed at `assign`: `NO-GO`, the
  error.
- **gates.** The current attempt's `ci` (the one logbook 1.4 judges). A
  gate with a row: its verdict and its line verbatim; after the `NO-GO`
  row, every gate `not-reached`, line `not run: <failed gate> said NO-GO
  first`. A `ci` with no rows (written before 1.3): `ci.failed` is `NO-GO`
  with `ci.line`, the gates before it `GO` with no line, after it not
  reached; a `GO` ci with no rows is `GO` on every gate with `ci.line`. No
  `ci` yet and the ticket at `pr-opened`: every gate `running`, line `the
  gates judge attempt <n>; the step reports when all have run`,
  `started_at` the `at` of the entry that brought it to `pr-opened`.
  While a rework runs (`ci-failed`), the gates keep the judged attempt's
  verdicts: the card's now line says the next attempt runs.
- **pr.** `started_at` is the `ci` entry of the current attempt that moved
  to `review` or `ci-passed`. At `review` or `ci-passed`: `running`, line
  `the <role> reviews attempt <n> (risk <level>)` (the document's
  review role, else the step's default, the test-reviewer) or `the merge (risk
  <level>, <decision>)`. A changes-requested review: `NO-GO`, its first
  line (the document keeps a review only for the attempt it judged: a
  rework clears it). Merged (`merged` or a state after it: `main-green`,
  `held`, `rolled-back`): `GO`, `ended_at` the `merge`
  entry's `at`, line `merged <first 7 of merge.sha>`, then `, approved by
  the <role>` when a review approved it, or `, no review: risk <level>`.
- **canary.** The ticket's release in the release log (its ticket and its
  `release.version`, the last line). `started_at` the first stage's
  `started_at` (else the `at` of the entry that brought the ticket to
  `main-green`); `ended_at` the last stage's `judged_at` (else the `at`
  of the `release` history entry). `held`: `GO`,
  line `held at <held_at>%: <n> stage(s) GO`. `rolled-back`: `NO-GO`, line
  the release's `reason`, else its last stage's verdict, else the
  document's `release.verdict`. At `main-green` with no error: `running`,
  line `the release step: deploy, then the stages`.
- **human.** `held` with an answer: its answer (`GO`/`NO-GO`),
  `started_at` the `held` entry's `at`, `ended_at` the answer's `at`,
  line `<promoted|rejected> by <by> via <via>` and `: <reason>` when it
  has one. `held` with none: `running`, line `waits for go-for-launch at
  <held_at>%`. Rolled back by the canary: `not-reached`, line `the canary
  rolled back before it`.
- **full.** Promoted: `GO`, `started_at` = `ended_at` = the answer's
  `at`, `seconds` null, line `100% of the traffic on <version>`. Rejected:
  `not-reached`, line `rolled back by a person`.
- A node with no rule above is `not-reached`, every field but `node` and
  `gate` null.

### 4. Each gate's seconds

The `gates` recipe writes `$(GATES_OUT)/seconds`, one line `<gate>
<seconds>` per gate it ran, from `date +%s` before and after the gate.
`core/steps/ci.sh` records it as `ci.seconds`, `{gate: seconds}` (null
when the file is absent: an older recipe, or the perimeter refusal that
runs no gate); `core/steps/rework.sh` copies it into the rework entry
beside `lines`. Second resolution: a gate takes from about a second
(`risk`) to minutes (`mutation`); a finer clock would add a dependency
to the recipe for no reader. Nothing else changes in the recipe: same
lines, same verdict file, same exit. The light tier (`python3 -m core
gates --light`, ADR 0061) is not the ci step's and records no seconds.

### 5. Logbook 1.6

Additions only, all optional, so a 1.6 logbook validates against every
1.x schema and a 1.5 logbook stays readable: `tickets[].flow` (§1–§3),
`tickets[].ci.seconds` and `tickets[].rework[].seconds` (§4). Schema
`core/logbook/1.6.schema.json`; golden `fixtures/logbook/golden-1.6.json`,
written by the reader over the stub sources; `golden-1.5.json` stays as
the old-logbook fixture. ADRs 0086 and 0087 add `roles`, `spend.burn`
and `tickets[].live` to the same minor.

### 6. The strip on screen

`ol.flow` with one `li.flow-node` per node, in order, carrying
`data-node`, `data-gate` (gate nodes), `data-verdict` and, when it has
seconds, `data-seconds`. Each node shows a mark for its verdict that is
not colour alone (✓, ✗, a ring for running, an empty dot for not
reached), and its time (`12s`, `3m`, `2h`; a running node shows `…`).
The colours are the console's GO, NO-GO, waiting and none tokens, which
`ci/console-ui.py` already holds to WCAG 2.1 AA in both themes; the
strip is measured the same way.

- **On the card's face** the strip is compact: the nodes as segments, no
  text but the marks, the reason in a `title`. It is not a tab stop: the
  card is the keyboard's unit on the board (ADR 0068 §5, the arrows move
  between cards), and eleven stops per card would bury it. The strip has
  `role="img"` and an `aria-label` that reads every node's name and
  verdict in order, so a screen reader hears it with the card.
- **On the card's back and the timeline screen** (`#ticket/<id>`, as
  `ol#flow`) every node is a tab stop (`tabindex="0"`) with its name, its
  verdict and its time as text, and its line in a `.flow-why` element
  linked by `aria-describedby`, shown on hover and on focus. A `NO-GO`
  gate node whose attempt has a why (ADR 0078) opens the why panel by
  click and by Enter, as the other openers do.
- A logbook before 1.6 has no `flow`: the page draws no strip and says
  nothing about it (the band strip of ADR 0063 stays).

The page draws the logbook's nodes as they are: it computes no verdict,
no time and no line.

## Tests, from this ADR

Written before the code, by hand from the stub sources (the stub's tower
documents gain `ci.seconds` on ticket 0010 and on 0003's rework entry):

- `core/tests/test_logbook.py`, `flow` over the stub, each ticket's nodes
  in the order of §1 (fourteen: ticket, perimeter, eight gates, pr,
  canary, human, full):
  - 0010 (opened directly, two attempts, review approved, held, promoted
    by bob via cli at 07:45): ticket `GO` `opened directly…` with no
    seconds; perimeter `GO` from 06:00:00 to 06:05:00, 300 s, `PR #10
    opened, every file inside …`; the eight gates `GO`, each line
    verbatim from `ci.gates`, each with the stub's `ci.seconds`; pr `GO`
    from 06:55:00 to 07:06:00, 660 s, line `merged m10, approved by the
    test-reviewer`; canary `GO` from 07:08:00 (its stages have no times)
    to 07:30:00, `held at 25%: 2 stage(s) GO`; human `GO` from 07:30:00 to
    07:45:00, 900 s, `promoted by bob via cli`; full `GO`, `100% of the
    traffic on 0.2.5-n`. With the answer taken out of the release log:
    human `running`, `waits for go-for-launch at 25%`, full
    `not-reached`; with a NO-GO answer and a reason, human `NO-GO`
    `rejected by … via …: <reason>`, full `not-reached` `rolled back by a
    person`.
  - 0003 (two attempts, the second NO-GO at `test`): the first five gates
    `GO`, `test` `NO-GO` with its line, `mutation` and `test-proof`
    `not-reached` with `not run: test said NO-GO first`; pr, canary,
    human, full `not-reached`.
  - 0009 (spec NO-GO, at questions): ticket `NO-GO`, line `NO-GO: spec —
    <the question>`, `started_at` 08:00:00; every other node `not-reached`.
  - 0005 (assign failed): ticket `GO`, perimeter `NO-GO` with `step exited
    1`, everything after it `not-reached`.
  - 0004 (stranded at ci): the first gate `NO-GO` with the error, the
    rest of the gates and after them `not-reached`.
  - 0001 (rolled back): canary `NO-GO` with the record's reason; human
    `not-reached`, `the canary rolled back before it`.
  - 0008 (review changes-requested): the eight gates `GO` with `GO: ci`
    (a ci with no rows), pr `NO-GO` `first line`.
  - A ticket at `pr-opened` with no ci: the eight gates `running`; at
    `review`: pr `running`.
  - Every node of every ticket has exactly the seven keys; no `seconds`
    without both times except on a gate.
- `ci/tower.sh` (h), the real `core/steps/ci.sh` over the base's
  Makefile: with the two gates that say GO (h3), `ci.seconds` has exactly
  `risk` and `go-demo`, each a whole number of 0 or more, and `ci.lines`
  is unchanged; with the planted NO-GO (h4), exactly `risk` and
  `nogo-demo`, none after it; the perimeter refusal (h2) has `ci.seconds`
  null. `ci/tower.sh` also proves `core/steps/rework.sh` copies
  `ci.seconds` into the rework entry.
- `ci/logbook.sh`: the golden is `golden-1.6.json`; the 1.6 `flow` of 0010
  and 0003 checked by hand as above, each run again with one fact planted
  wrong; `golden-1.5.json` validates as the old fixture.
- `ci/console-ui.py`, over the golden: one `ol.flow` per card, with the
  logbook's nodes in order (`data-node`, `data-gate`, `data-verdict`) and
  an `aria-label` naming each; on the back and on `#ticket/0010` `ol#flow`
  whose nodes are tab stops, each focus showing its `.flow-why` with the
  logbook's line and `aria-describedby` pointing at it; on `#ticket/0003`
  Enter on the `test` node opens `dialog#why` and Esc returns the focus to
  it; contrast of every text in the strip and of the focus ring in both
  themes; over a 1.5 logbook no `.flow`; nothing POSTed.

## Alternatives rejected

- **The page derives the nodes.** It would be the first verdict the
  console computes, stream B's snapshot would need the same code, and
  only a browser test could check it. The logbook is where every rule of
  the console's reading lives.
- **One node per attempt, or the gates of every attempt.** The pips and
  the back already show the attempts; the strip shows where the ticket is
  now, and the why panel shows any attempt's NO-GO.
- **Every node focusable on the card's face.** Eleven stops per card on a
  board of dozens, against the arrows ADR 0068 gives the board.
- **A gate's time from the ci step's history entry, shared out.** It
  would invent a split; the recipe can measure it.
- **A chart library for the strip.** It is a list of fourteen items.

## Consequences

- A ticket's whole path is in one row on its card, and in the same words
  on its back, its timeline and stream B's static snapshot.
- The recipe writes one more file in `GATES_OUT`; tickets judged before
  this ADR have no `ci.seconds`, and their gate nodes no time.
- Logbook 1.6 is the format stream B's snapshot reads.
