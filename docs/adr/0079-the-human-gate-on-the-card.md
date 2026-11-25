# ADR 0079 — The human gate on the card: the held release's card is the one card that drags, onto "Promote to 100%" or "Rollback"; the drop opens the same panel as the launch screen's buttons, nothing moves without its confirmation, and the record keeps the reason and the brief answered

- Status: accepted
- Date: 2026-11-12
- Milestone: M45 (docs/PLAN.md, "Sprint 2026-11-12", stream A, second slice)
- Amends, for this one gesture only and by the owner's decision of
  2026-11-12: ADR 0059 §1 (the kanban's rejection of drag-and-drop) and
  ADR 0068 §1 (no `draggable`, no drag or drop listener). Both stay in
  force for every other card. Also ADR 0047 §3 (the answer's record gains
  `reason`, `brief_sha256` and `brief_author`) and ADR 0078 (logbook 1.4
  carries them).

## Context

The one human decision before 100% exists: `core/promote.sh`, run by
`./go promote` or by the console's `POST /v1/go-for-launch` (ADR 0047 §3).
The console reaches it from a separate screen, `#launch`, with two buttons
behind a browser `confirm()`. The kanban shows the held ticket with the
now line "Your turn: go-for-launch" and a link to that screen (ADR 0063).

Read on 2026-11-12:

- The record's answer is `{weight, at, by, via}` on GO and
  `{at, by, via}` on NO-GO (`core/releases.py`, `answer`). It says who
  answered, not why, nor which brief they read. The console sends the
  brief's sha256 and `promote.sh` refuses an answer to another brief, but
  the sha is not written down: the audit trail cannot say which text the
  human approved.
- ADR 0059 §1 rejected dragging because a card moves when the tower moves
  its ticket, never by hand; ADR 0068 §1 kept the rule. The owner's
  decision of 2026-11-12 brings the drag back for this gesture alone: a
  held release is the only card whose next move belongs to a person and
  has exactly two outcomes.

## Decision

### 1. The record: `reason`, `brief_sha256`, `brief_author`

`core/releases.py answer` writes, beside `by` and `via`, in the record's
`promoted` or `rejected` and in the release log's `answer` line:

- `reason`: `GO_FOR_LAUNCH_REASON`, stripped; `null` when empty;
- `brief_sha256`: the sha256 of `$PROMOTE_OUT/brief.md`, the brief
  `promote.sh` has just written or checked against the sha the human
  answered; `null` if there is none;
- `brief_author`: the first line of `$PROMOTE_OUT/brief.author`; `null`
  if there is none.

`./go promote GO_FOR_LAUNCH=NO-GO GO_FOR_LAUNCH_REASON=…` gives the CLI
the same field. The CLI does not require a reason: `promote.sh`'s
contract and `production-readiness` are unchanged.

The logbook (1.4) carries the three fields wherever it carries an answer:
`releases[].answer` and `launch.record.promoted`/`rejected`, and the
timeline's `sign-off` row gains `reason`. All optional.

### 2. No new write path

The drag and the launch screen's buttons send the same
`POST /v1/go-for-launch` as today, under the same rules: `CONSOLE_AUTH`,
`CONSOLE_LAUNCHERS`, Origin and Host, the press lock, the record and the
brief checked again just before `promote.sh` runs. The body gains one
optional field, `reason` (a string). The server's one new rule: a
`NO-GO` whose `reason` is missing or blank is refused with 400 and runs
nothing. The 4 KiB cap on the body already bounds the reason; no new
number. The server passes it as `GO_FOR_LAUNCH_REASON`.

### 3. The one card that drags

On the kanban, the card of the ticket whose `release.version` is
`launch.record.version`, when that record is `held` and unanswered and
the launch is pressable or preparable, carries `draggable="true"` and
`data-launch="<version>"`. Every other card keeps no `draggable`, and no
drag or drop listener is registered anywhere else.

While such a card exists, the board shows two targets, `#drop-promote`
("Promote to 100%") and `#drop-rollback` ("Rollback"). Each is a
`<button>`: it accepts the card's drop, and a click or Enter on it does
the same as the drop. That is the keyboard alternative: Tab to the
target, press Enter. Dropping or pressing opens the panel of §4. It
moves nothing.

### 4. One panel, from the drop and from the buttons

`dialog#launch-confirm`, opened modal from a drop, from a target, and
from the launch screen's GO and NO-GO buttons, which no longer call
`confirm()`. It shows what the decision rests on, all from the logbook:

- `#lc-brief`: the brief's text, author and sha256;
- `#lc-thresholds`: the verify thresholds (`thresholds[]` judged by
  `verify`), each with its value and provenance;
- `#lc-stages`: every stage of the record, its percentage, verdict and
  numbers;
- `#lc-risk`: the ticket's risk level (`ci.risk_level`);
- `#lc-gates`: the PR's gate lines (`ci.gates`), one per line.

Then `textarea#lc-reason`, required for a rollback and optional for a
promotion, and `button#lc-confirm`, disabled until the reason is given
when it is required. When the brief is not prepared yet,
`button#lc-prepare` prepares it (`answer: PREPARE`, as today) and the
confirm stays disabled until the brief is on disk. Esc, `button.close`
and the backdrop close the panel; nothing is sent until `#lc-confirm`.
The answer line `promote.sh` printed is shown in the panel.

### 5. The proofs

- `ci/promote.sh`: a GO and a NO-GO with a reason record `reason`,
  `brief_sha256` (the brief's own) and `brief_author` in the record and in
  the log; without a reason, `reason` is `null`.
- `ci/logbook.sh`: the server refuses a NO-GO with no reason before
  anything runs and passes the reason on a good press; `index.html`
  declares `draggable` only on the held card and registers drag and drop
  listeners only for it and the two targets.
- `ci/console-ui.py`: the drag (Playwright's `drag_to`) and Enter on a
  target open the panel with the logbook's facts; nothing is POSTed
  before the confirmation; the confirmed POST carries the reason; a
  rollback cannot be confirmed without one; no other card is draggable.
- `./go demo-console`: a held release rolled back with a reason through
  the console, then a second one promoted with a reason, each checked in
  the record, the log and the plane.

## Alternatives rejected

- **Make the reason mandatory on the CLI too.** It changes `promote.sh`'s
  contract, which `production-readiness` and scripted callers rely on.
  The console requires it because the panel is where the person reads the
  evidence.
- **Let every card drag, the move refused unless it is a release.** The
  page would advertise a gesture that fails nine times out of ten.
- **A drop that posts at once.** The drag is fast and easy to do by
  mistake. The confirmation is the gate.
- **Record the brief's text in the log.** The brief is already on disk
  and in the record's directory. Its sha names it, and the evidence pack
  (ADR 0081) can copy it.

## Consequences

- The kanban's "no drag" rule now has one stated exception. The checks
  that proved it was absent now prove it is present only there.
- `./go demo-console` runs two releases instead of one: about one more
  `STAGE_SECONDS × 2` in its CI job, not in `./go demo`.

## Golden-rule check

`./go demo` does not run the console. `releases.py answer` reads two
files `promote.sh` has already written. No key, no model, no second of
the demo.
