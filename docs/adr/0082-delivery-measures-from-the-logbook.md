# ADR 0082 — The delivery measures, from the logbook: four DORA keys over a window, a measure the record cannot give is not measured

- Status: accepted (M48)
- Date: 2026-11-12

## Context

lights-out records when a ticket was requested (the tower's `history`),
when a release ended held or rolled back and when a person answered it
(`releases.jsonl`, ADR 0047 §2 P2). Nobody adds those times up. The
owner asked for the four delivery measures a team already knows, the
DORA keys, so a reader can compare lights-out with the team they have.

The source is DORA's own guide, "DORA's software delivery metrics: the
four keys" (dora.dev/guides/dora-metrics-four-keys, last updated
2026-01-05), which names five measures today:

| DORA's name | DORA's definition |
|---|---|
| Deployment frequency | the number of deployments over a given period, or the time between deployments |
| Change lead time | the time a change takes from committed to version control to deployed in production |
| Change fail rate | the ratio of deployments that require immediate intervention following a deployment |
| Failed deployment recovery time | the time it takes to recover from a deployment that fails and requires immediate intervention |
| Deployment rework rate | the ratio of deployments that are unplanned but happen as a result of an incident in production |

The plan asks for the first four (docs/PLAN.md, sprint 2026-11-12, M48).
The fifth needs a deployment to know it was caused by an incident, which
no record carries yet; it is not measured and not printed.

## Decision

1. **The source is the logbook.** `./go dora` writes the logbook first
   (`core/logbook.sh`, stdout to stderr) and reads only that file, as
   every consumer does (ADR 0047 §2); `DORA_LOGBOOK=<file>` reads a
   logbook already written instead. It reads `releases[]` (each with
   `at`, `verdict`, `ticket`, `stages`, `answer`) and `tickets[]` (each
   with `id` and `history`). A release entry with no `at` (the
   `release.json` fallback the logbook adds for a record not yet in the
   log) is not counted: it has no time to put in a window.
2. **The window.** The measures cover the releases whose time falls in
   the last `WINDOW_DAYS` days, `now - WINDOW_DAYS x 24 h` to `now`,
   both ends included; an answer later than `now` has not been given
   yet. Default 30. Provenance, decision: the owner's,
   docs/PLAN.md, sprint 2026-11-12, M48. Any value that is not a whole
   number of 1 or more is a NO-GO naming it.
3. **The four measures, as lights-out records them.**
   - **Deployment frequency**: the GO answers (`answer.answer == "GO"`)
     whose `answer.at` is in the window, counted, and that count divided
     by `WINDOW_DAYS`, per day. A GO answer is the moment the canary
     takes 100% of traffic (`core/promote.sh`): that is lights-out's
     deployment to production. Zero is a measure, not a missing one.
   - **Change lead time**: for each GO answer in the window whose release
     names a ticket the logbook has, `answer.at` minus the time of that
     ticket's first history entry (`request`, or `open` for a ticket
     opened with no request); the median, in seconds, and how many
     samples. A GO release with no ticket (`./go release` run by hand)
     counts in the frequency and is reported as `without_ticket`, never
     in the median.
   - **Change fail rate**: the releases in the window (by their `at`)
     that were decided, either rolled back by verify (`verdict ==
     "rolled-back"`) or held and answered; of those, the failed ones are
     the rolled-back and the ones a person answered NO-GO (which rolls
     the canary back). Failed over decided. A held release with no
     answer yet is neither: it is reported as `pending`.
   - **Failed deployment recovery time**: for each failed release of the
     window, from the `started_at` of its last stage (the stage the
     failed canary was serving when it was judged, ADR 0080) to its
     rollback: the release's `at` for a verify rollback, `answer.at` for
     a NO-GO. The median, in seconds, over the failed releases that
     carry `started_at`; the ones that do not are reported as
     `unmeasured`.
4. **Not measured, with the reason; never estimated.** A measure with no
   sample prints `not measured` and why, and `.crew/dora.json` carries
   `{"measured": false, "why": ...}`: no GO with a ticket in the window
   (lead time), no decided release (fail rate), no failed release, or no
   failed release with a stage `started_at` (recovery time). A source the
   logbook could not read (`{"ok": false, "error"}`) makes every measure
   that needs it not measured, with the logbook's error as the reason.
   A logbook that is not there or is not JSON is a NO-GO.
5. **The output.** Stdout is one line of the gate contract: `GO: dora —
   <file>, <n> of 4 measured over <W> days`, or `NO-GO: dora — <why>`.
   Stderr has one line per measure. `.crew/dora.json` (`DORA_OUT`) has
   `{schema: "dora/1", window_days, from, to, measures:
   {deployment_frequency, lead_time, change_fail_rate, recovery_time}}`;
   a measured entry has `measured: true` and its numbers, the medians in
   whole seconds. Medians are the statistics module's: the mean of the
   two middle samples for an even count.

## The gap between DORA and the record

- **Lead time starts earlier.** DORA starts at the commit; lights-out
  starts at the request, before any code exists, so the clarify, spec,
  rework and review time are all inside it. The plan chose that start
  (M48: "dal `request` al 100%"); it is longer than DORA's by the time
  before the first commit, and a ticket opened with no request starts at
  `open`.
- **A deployment is a promotion.** DORA counts deployments to
  production; lights-out counts canaries promoted to 100%. A canary at
  5% or 25% serves production traffic, but is counted only in the fail
  rate's denominator once decided.
- **A failure is a rollback before 100%.** DORA's fail rate counts
  deployments that needed intervention after they shipped; lights-out
  rolls back before 100%, so its failures are caught canaries, and a
  promoted release that later fails is not in the record (no incident
  is tied to a release yet). The fail rate is therefore the canary's
  catch rate, and can only undercount DORA's.
- **Recovery starts at the stage.** DORA measures from the failure to
  the recovery; lights-out does not record when the failure began,
  only when the stage serving it started. Recovery time is therefore an
  upper bound on the time the failed change served traffic in that
  stage. Before ADR 0080 adds `started_at` to the stages, it is not
  measured.
- **Rework rate** is not measured (Context).

## Tests, from this ADR

`core/tests/test_dora.py`, on this fixture logbook, `now =
2026-11-30T00:00:00Z`, `WINDOW_DAYS = 30` (window from
2026-10-31T00:00:00Z):

| ticket | first history entry |
|---|---|
| 0101 | request, 2026-11-10T08:00:00Z |
| 0102 | open, 2026-11-11T09:00:00Z |
| 0103 | request, 2026-11-12T10:00:00Z |
| 0104 | request, 2026-10-01T00:00:00Z |

| release | ticket | at | verdict | last stage started_at | answer |
|---|---|---|---|---|---|
| R1 1.1.0 | 0101 | 2026-11-10T12:00:00Z | held | 2026-11-10T11:55:00Z | GO 2026-11-10T14:00:00Z |
| R2 1.2.0 | 0102 | 2026-11-11T12:00:00Z | rolled-back | 2026-11-11T11:58:00Z | none |
| R3 1.2.1 | 0102 | 2026-11-12T12:00:00Z | held | 2026-11-12T11:55:00Z | GO 2026-11-12T13:00:00Z |
| R4 1.3.0 | 0103 | 2026-11-13T12:00:00Z | held | 2026-11-13T11:50:00Z | NO-GO 2026-11-13T12:30:00Z |
| R5 1.4.0 | none | 2026-11-14T12:00:00Z | held | 2026-11-14T11:55:00Z | none |
| R6 1.4.1 | none | 2026-11-15T10:00:00Z | held | 2026-11-15T09:55:00Z | GO 2026-11-15T11:00:00Z |
| R7 1.0.0 | 0104 | 2026-10-02T00:00:00Z | rolled-back | 2026-10-01T23:58:00Z | none |
| R8 1.4.2 | none | none (`release.json`) | held | — | none |

Expected, computed by hand:

- deployment frequency: R1, R3, R6 → 3 deployments, 3 / 30 = 0.1 per
  day (R7 is outside the window, R8 has no time).
- lead time: 0101 14:00 − 08:00 on 2026-11-10 = 21,600 s; 0102
  2026-11-12T13:00 − 2026-11-11T09:00 = 100,800 s; median 61,200 s, 2
  samples, 1 without a ticket (R6).
- fail rate: decided R1, R2, R3, R4, R6 = 5; failed R2, R4 = 2; 0.4;
  pending R5 = 1.
- recovery time: R2 12:00:00 − 11:58:00 = 120 s; R4 12:30 − 11:50 =
  2,400 s; median 1,260 s, 2 samples, 0 unmeasured.

The same fixture with every `started_at` removed: recovery time not
measured, the reason naming `started_at`; the other three unchanged. An
empty logbook (no tickets, no releases): frequency measured at 0, the
other three not measured. A logbook whose `releases` is `{"ok": false,
"error": "x"}`: all four not measured, each reason carrying `x`. A
window of 1 day ending 2026-11-13T12:30:00Z: frequency 1 (R3's GO at
2026-11-12T13:00:00Z), 1.0 per day; lead time 100,800 s, 1 sample; fail
rate 1.0 (R4 alone, by its `at`); recovery 2,400 s. A window of 1 day
ending 2026-11-13T12:15:00Z: R4's answer is after the end, so R4 is
pending and the fail rate is not measured. `WINDOW_DAYS=0` and `WINDOW_DAYS=x`: a
NO-GO naming `WINDOW_DAYS`. The command itself: `core/dora.sh` on a
fixture logbook prints exactly one stdout line, `GO: dora — ...`, and
writes the file; on a missing logbook one `NO-GO: dora — ...` line,
exit 1.

## Alternatives rejected

- **Reading the tower and `releases.jsonl` directly.** It would repeat
  the logbook's join of an answer to its release and break ADR 0047
  §2's rule that a consumer reads the logbook; the console's panel (the
  sprint's final slice) will read the same measures from the same
  document.
- **Mean instead of median.** One stuck ticket would move the lead time
  by days. DORA's guide gives no statistic; the median is the one the
  DORA team's Four Keys project used, and the one that ignores a single
  outlier.
- **Estimating recovery time from the release's `at` alone** (for
  example, minus a stage's nominal duration). That is a guess; the plan
  forbids it, so it is not measured until the record has the time.
- **A held, unanswered release as a success.** It has not reached 100%
  and may still be rejected; counting it either way would move the fail
  rate before anything happened.

## Consequences

- `./go dora` works on any plane that has a logbook, L0 included, with
  no key; at L0 after `./go demo` it reports the demo's own releases.
- The measures improve when the record does: ADR 0080's `started_at`
  turns recovery time on with no change here.
- The console panel is the sprint's final slice, after stream A lands;
  it can embed `dora.json` in the logbook or read the same file.

## Amendment 2026-11-13: the measures in the logbook (1.5) and on the Overview

The sprint's console slice (docs/PLAN.md, sprint 2026-11-12, "Fetta
finale"): the four measures on the console's Overview. The console reads
the logbook and nothing behind it (ADR 0047 §2), and the logbook has no
clock: equal sources give equal bytes. The measures need a window's end.

1. **`dora`, top level of logbook 1.5** (the same minor as ADR 0083's
   amendment, additions only): `core/dora.py`'s `measures` over the
   logbook's own `releases` and `tickets`, with `WINDOW_DAYS` as `./go
   dora` reads it (default 30), plus `lines`, the four measure lines and
   the window line `./go dora` prints, without their `[dora] ` prefix, in
   its order. The same object `.crew/dora.json` holds, with `lines`.
2. **The window ends at the record's last event**, not at a clock: the
   latest of the tickets' history `at`, the releases' `at` and their
   answers' `at`. The console's ages already take that reference in a
   snapshot (ADR 0068 §2). `./go dora` run at that moment prints the same
   measures; run later, its window ends later, and a release can leave
   it. The panel says which end it used. A record with no time at all,
   or a `WINDOW_DAYS` that is not a whole number of 1 or more, makes
   `dora` `{ok: false, error}` with the reason (`./go dora`'s own words
   for the window).
3. **The panel.** The Overview gains `section#dora`, before
   `section#spend`: `#dora-window` holds the window line, then one
   `.dora-measure[data-key=<key>][data-measured=true|false]` per measure
   in §5's order, its text the measure's line verbatim. A measure not
   measured says so with the reason, as the command does. The page
   formats no number of its own. `dora` unread shows its error; a
   logbook before 1.5 says it has no measures.

### Tests, from this amendment

- `core/tests/test_logbook.py`, over the stub sources (the window ends
  at 0009's last history entry, 2026-11-07T08:10:07Z; 30 days from
  2026-10-08T08:10:07Z), by hand: deployment frequency 2 (0.2.5-n and
  0.3.0-b's GO), 2/30 per day; lead time 0010 07:45 − 06:00 = 6,300 s,
  0002 10:30 − 08:00 = 9,000 s, median 7,650 s, 2 samples, 0 without a
  ticket; change fail rate 2 of 4 decided (0.2.0-a rolled back, 0.4.0-c
  answered NO-GO), 0.5, 0 pending (0.5.0-d has no `at`); recovery time
  0.2.0-a 09:29:00 − 09:27:30 = 90 s, 1 sample, 1 unmeasured (0.4.0-c's
  stage has no `started_at`). `lines` equal `dora.lines` of that result
  without the prefix. `WINDOW_DAYS=0` and `x`: `dora` an error naming
  `WINDOW_DAYS`. A store with no ticket and an empty release log: an
  error saying the record has no time. The releases unread: four not
  measured, as §4.
- `ci/logbook.sh`: the same numbers checked by hand on the golden, each
  run again with one fact planted wrong.
- `ci/console-ui.py`: over the golden, `section#dora` on `#overview`,
  its window line and its four measures equal `dora.lines`, each with its
  key and `data-measured`; a derived logbook with one measure not
  measured shows the reason; `dora` unread shows the error; a 1.4
  logbook, the no-measures sentence.
