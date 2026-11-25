# ADR 0078 — The why of a NO-GO, one click away: logbook 1.4 gives every rejected attempt a `why` (the gate, its line, its details and, for `sast`, the new findings), withheld until the attempt's `secrets` gate said GO; a click or Enter on any red gate opens one panel with it, the attempt, the attempts left and the next move

- Status: accepted
- Date: 2026-11-12
- Milestone: M47 (docs/PLAN.md, "Sprint 2026-11-12", stream A, first slice)
- Amends: ADR 0047 §2 (the logbook goes to 1.4: optional additions, and
  `details` becomes null where §3 below withholds it); ADR 0071 §1 (the
  withholding rule for a patch now also covers an attempt's details);
  ADR 0072 §1 (`sast.json`'s `new` is copied into the tower by the ci step)

## Context

A NO-GO is the product's most frequent answer, and the console shows it
as one line. Read on 2026-11-12:

- The ci step copies the failed gate's own details into `ci.details`
  (`core/steps/ci.sh`, the `<gate>.details` file, ADR 0038 §5), and the
  rework step copies `ci.details` into each `rework[]` entry
  (`core/steps/rework.sh`). The logbook passes both through
  (`core/logbook.py`, `judged`), but the page draws only the current ci's
  details, as timeline rows (`logbook.py` `timeline`, the `gate` rows);
  an older attempt's details and the reviewer's reason as a list reach
  no screen.
- `gates/sast.sh` writes `sast.json` with the findings this diff
  introduces (`new`: `{rule, file, line}`, ADR 0072 §1). Nothing copies
  it into the tower: a `sast` NO-GO says "this diff introduces 2
  finding(s); list on stderr", and the list dies with the throwaway
  worktree.
- `details` are shown unconditionally. A gate's details can quote the
  code under test: a failing test prints the values it compared, a
  mutant prints the line it mutated. ADR 0071 §1 withholds an attempt's
  patch until its `secrets` gate said GO, so that a key a worker pasted
  never reaches a snapshot; the same key reaches one today through
  `details`.

## Decision

### 1. The record: `ci.sast`, and `failed` and `sast` on every rework entry

- `core/steps/ci.sh`: when the failed gate is `sast` and its
  `sast.json` holds a `new` list, `ci.sast` is that list, each item
  `{rule, file, line}` as `core/sastdiff.py` wrote it; otherwise `null`.
- `core/steps/rework.sh`: a gates entry carries the ci's `failed` and
  `sast` beside the `details` it already carries; a review or a mainline
  entry carries `failed: null` and `sast: null` (the reviewer and main's
  check have no `sast.json`).

No new file, no new step: the ci step copies what the gate already
wrote, as it does for `<gate>.details`.

### 2. Logbook 1.4: `why`, on ci and on each rework entry

`core/logbook.py` writes `lights-out/logbook/1.4`. Additions, all
optional, so a 1.4 logbook validates against every 1.x schema:

- `ci.why`, `null` unless `ci.verdict` is `NO-GO`;
- `rework[].why`, on every entry (every entry is a rejection).

`why` is:

```json
{"attempt": 2, "judge": "gates", "gate": "sast",
 "line": "NO-GO: sast — in the workbench: this diff introduces 1 finding(s); list on stderr",
 "details": ["…"] | {"ok": false, "error": "withheld: …"},
 "sast": [{"rule": "…", "file": "…", "line": 12}] | null | {"ok": false, "error": "withheld: …"}}
```

- `attempt`: the rework entry's own; for ci, the ticket's `attempt`.
- `judge`: the entry's (`gates`, `review`, `base`, `main`); `gates` for ci.
- `gate`: the record's `failed` when it names one; else the first
  NO-GO row of the entry's `gates` (ADR 0071 §1); else `null` (a review,
  or a record from before 1.3 with no gate lines).
- `line`: the entry's line, verbatim.
- `details` and `sast`: the record's, under §3.

### 3. No detail before `secrets` said GO on the same attempt

`why.details` and `why.sast` are the record's lists only when the same
entry's `gates` hold a `secrets` row with verdict `GO`; otherwise each is
`{"ok": false, "error": "withheld: the secrets gate did not say GO on
attempt <n>"}` (a `sast` that is `null` in the record stays `null`: there
is nothing to withhold). It is ADR 0071 §1's rule for the patch, applied
to the same attempt's gate lines, and it covers the cases that rule
names: `secrets` NO-GO, `secrets` not run (a perimeter NO-GO stops before
the gates), and a record with no gate lines at all (written before 1.3).

The withheld lists leave the rest of the document too: where §3
withholds, the 1.3 field `details` of that entry is `null` (its schema
allows it) and the timeline writes no `gate` row for it. A reviewer's
reason is the reviewer's own text and is not withheld when the attempt's
`secrets` said GO, which every review entry's gate lines show.

### 4. One panel, from every red gate

`console/index.html` has one `dialog#why`, opened modal. A click, or
Enter or Space on the focused element, opens it from:

- the card: the outcome chip, when the outcome is NO-GO and the ticket
  has a `why` (a `button.why-open`);
- the back and the timeline: the `gates` row whose attempt was rejected,
  and the `rework` row (it names the rejection that sent it back);
- `#network`: a gate node or a stream gate line whose verdict is NO-GO.

It shows, in this order:

1. the gate's line (`why.line`), and the gate and the judge;
2. the details, one per line, or the withheld reason; for `sast`, the
   new findings as a table (rule, file, line), or the withheld reason;
3. the attempt, and the attempts left: `REWORK_MAX_ATTEMPTS` (the
   logbook's `thresholds`) minus the attempt, never below zero, left out
   when either is unread;
4. the next move: for the attempt the ticket is at, the now line of
   ADR 0063 §2 (whose turn it is, in the same words); for an earlier
   attempt, what followed it: "sent back to the dev-worker: attempt
   <n+1>".

Esc, the Close button and a click on the backdrop close it, and the focus
returns to the element that opened it. The panel writes nothing and
sends no request.

A 1.3 logbook has no `why`: nothing opens a panel, and every screen
draws what it drew before.

The DOM contract `ci/console-ui.py` checks:

- every opener is a `button.why-open` carrying `data-ticket` and
  `data-attempt`, except on `#network`, where a fact's words are fixed
  (ADR 0071 §5: the screen and `./go network` say the same text): there
  the NO-GO gate node of the SVG and the NO-GO line of the stream are
  themselves the opener, with `role="button"`, `tabindex="0"`, class
  `why-open` and the same two attributes, and their text is unchanged;
  only a NO-GO with a `why` has an opener;
- `dialog#why` holds `#why-line` (the line), `#why-gate` (gate and
  judge), `#why-details` (one `li` per detail, or one `.withheld` with the
  reason), `#why-sast` (only when the record has a `sast` list: one `tr`
  per finding with three `td`, rule, file, line, or one `.withheld`),
  `#why-attempt` ("attempt <n>"), `#why-left` ("<k> of <max> attempts
  left", absent when unread) and `#why-next` (the next move's words), and
  a `button.close`.

### 5. The stub and the golden

`fixtures/logbook/stub` gains the two NO-GOs the plan names, on one
ticket: 0003 (`ci-failed`, attempt 2, PR 3) was sent back once by a
`sast` NO-GO at attempt 1 (`secrets` GO, two new findings, both shown),
and its current ci is a `test` NO-GO at attempt 2 (`secrets` GO, its two
failing tests shown). Ticket 0002's rework entry, recorded with no gate
lines, is the withheld case. The golden
becomes `fixtures/logbook/golden-1.4.json`, generated by the reader and
checked by hand in `ci/logbook.sh` against this ADR, field by field;
`golden-1.3.json` stays as the old-logbook fixture, as 1.2 did at 1.3.

## Alternatives rejected

- **Change the type of `details` to carry the withheld reason.** A 1.4
  logbook would fail the 1.0–1.3 schemas, which `ci/logbook.sh` (b)
  validates it against; a minor is optional additions only (ADR 0047 §2).
- **Keep `details` as it is and withhold only in `why`.** The key would
  still reach the snapshot through the 1.3 field.
- **Read `sast.json` from the logbook.** The file lives in a throwaway
  worktree the ci step removes; the tower is the only record that
  outlives the step (ADR 0031).
- **Compute the attempts left in the logbook.** It is one subtraction
  over two fields the logbook already has; a third would be a copy.
- **A panel per screen.** Four drawings of one fact drift; one dialog,
  one builder.

## Consequences

- Ticket 0002's rework entry and any ci recorded before 1.3 have no gate
  lines: their details are withheld where 1.3 showed them. Old records
  lose a convenience, never a fact: the line stays.
- `ci/logbook.sh`, `ci/console-ui.py`, `site/render.py` and
  `.github/workflows/site.yml` move to the 1.4 golden.
- No number is added: no threshold, no timeout.

## Golden-rule check

`./go demo` at L0 does not read the console; the ci step's copy of
`sast.json` is one `jq` over a file the gate already writes. No key, no
model, no second of the demo.
