# ADR 0055 — The dev phase visible: three tower states before the ticket, the `product` role asks or confirms, a deterministic spec gate, one timeline per ticket and a read-only kanban in the console

- Status: accepted
- Date: 2026-11-07
- Milestone: M29
- Amends: ADR 0019 §1 (the transition table gains three states and three steps); ADR 0023 §2(a) and ADR 0047 §3 (the console's second write, answering the product role, comes back, mirroring `./go answer`); ADR 0047 §2 (the logbook goes to 1.1) and §7 (the chain screen becomes the timeline; an overview screen joins); ADR 0020 §1 (the `adk` harness takes the owner's answers as later turns of one conversation).

## Context

Read in this checkout on 2026-11-07, after the console was recovered onto
this branch (ADR 0047, one commit on 105dea2):

- The tower starts at `opened` (`core/tower.py`, ADR 0019 §1). Nothing
  records the request, the questions about it or whether its acceptance
  criteria can be tested. An ambiguous request shows up as rework attempts
  (ADR 0038), up to `REWORK_MAX_ATTEMPTS`.
- The `product` role exists since M7 (ADR 0020 §3) with two tools,
  `ask_product_owner(question)` and `confirm_acceptance(criteria)`, an
  eval, and two recordings: ticket 0005's waiting-list request without
  criteria (it asks, then confirms after the owner's answer: four model
  turns) and the same request with criteria (it confirms: two turns).
  Both were recorded at L1 on 2026-09-16. No tower step runs the role.
- The replay model is keyed by the first user message and indexed by the
  model turns already in the conversation (ADR 0020 §2). The `adk`
  harness sends one user message per run (`lights_out_adk/run.py`), so a
  recorded conversation with an answer cannot be replayed by the harness
  today, only by `adk eval`.
- No recording exists for ticket 0001, the ticket `./go demo` carries to
  the rollback. Recording one is the owner's (plan §7 M29, the M18
  precedent): a fixture is never written by hand.
- ADR 0047 §3 dropped the console's answer to the product role "until a
  `./go` command answers it". ADR 0023 §2 allows it as the second write.
- M27 (perimeter overlap at `assign`, ADR 0053) and M28 (the project
  map and the docs a merge leaves to realign, ADR 0054) were built in
  parallel and landed first (main 373da51). Their records: a refused
  `assign` is a failed history entry and the ticket stays `opened`; the
  StateStore's `perimeters/held` names who holds which globs; the merge
  step writes `map: {sha, docs_ticket}`.

## Decision

1. **Three states before `opened`, in the tower's one table.**
   - `./go request TICKET=<id>` writes the document at `requested` and
     publishes `clarify`. `./go open` stays as it is: a ticket opened
     directly skips the three states, as every existing demo but `e2e`
     does.
   - The steps, each with the one state it starts from (ADR 0019 §1):

     | step | from | to |
     |---|---|---|
     | `clarify` | `requested` | `questions` or `spec-ready` |
     | `answer` | `questions` | `questions` or `spec-ready` |
     | `spec` | `spec-ready` | `opened`, or `questions` on NO-GO |
     | `assign` | `opened` | `pr-opened` (unchanged) |

     `NEXT` gains `requested → clarify`, `spec-ready → spec` and
     `opened → assign`. `questions` requests nothing: it waits for a
     person.
   - The board's embedded table follows (ADR 0035, `ci/board.sh`).
   - The logic is Python (ADR 0052): `core/spec.py`. The step scripts are
     the thin glue that runs the role's harness and the gate.

2. **`product` asks or confirms; a person answers.**
   - `clarify` and `answer` run the same script: the `product` role on the
     request (`tickets/<id>.md`), with every answer given so far sent as a
     later user turn of the same conversation. The last decision call
     decides: `ask_product_owner` appends `{question, from: "product",
     asked_at}` to the document's `questions` and moves to `questions`;
     `confirm_acceptance` writes `spec.criteria` and moves to
     `spec-ready`. The run's harness, model, turns and tokens go into
     `product`, by the rule of ADR 0047 P1.
   - **The harness change:** `adapters/adk/run.sh` takes an optional
     `ANSWERS`, a JSON list of strings, sent in order after `INPUT` in the
     same session. Replay keys and indexes do not change, so the recorded
     four-turn conversation replays as it was recorded. Under replay the
     answers' text is not compared with the recording (the key is the
     first message only, ADR 0020 §2): a different answer replays the same
     criteria. The README says so.
   - `./go answer TICKET=<id> ANSWER=<text>` is a tower command, like
     `resume` (ADR 0034): it requires `questions` and an unanswered last
     question, writes `{answer, answered_by, answered_via, answered_at}`
     on it, and publishes `answer`. `ANSWER_BY` defaults to
     `unauthenticated` and `ANSWER_VIA` to `cli`, ADR 0047's rule for
     go-for-launch.
   - **The console's second write** is `POST /v1/answer`, which runs
     `make answer`, the recipe `./go answer` runs, with the console's
     identity and `via: console`, under ADR 0047 §3's checks (JSON content
     type, Origin, Host, one press at a time) plus one of its own: the
     ticket and the question index must be the open question the logbook
     shows, or nothing runs. In `proxy` mode any identity the proxy
     vouches for may answer and is recorded by name; `CONSOLE_LAUNCHERS`
     still bounds go-for-launch only. A snapshot answers nothing.

3. **At L0 the role runs only where a recording exists, and says so where
   it does not.**
   - With `AGENT_HARNESS=replay`, when the harness reports that no
     recording exists for this request (`run.json` `no_recording: true`,
     written by the adapter when the replay model raises its own
     "no recording" error), `clarify` does not fail and does not invent an
     answer: it records `product: {consulted: false, why: <the adapter's
     line>}`, takes the criteria the request itself states, and moves to
     `spec-ready`. The spec gate (§4) judges them as it judges any spec.
     Any other harness failure fails the step (ADR 0034), as before.
   - At any other level the role always runs; there is no pass-through.
   - **What L0 shows, plainly.** `./go demo` carries ticket 0001 from
     `requested` to the rollback: `clarify` with the product role not
     consulted (no recording for 0001), the spec gate GO, then the M9
     chain unchanged. The `questions` state with a real replayed question
     and answer is proven in `./go ci` on ticket 0005's recorded request,
     whose text is read from the recording itself, never retyped. Showing
     `questions` for 0001 at L0 takes the owner's `./go record-evals
     PROFILE=l1` on that request.

4. **The spec gate is deterministic, one line, no model.**
   - `gates/spec.sh <spec.md>` through `. ci/lib.sh`: `GO: spec` or
     `NO-GO: spec — <reason>`. The spec is the request with its
     `## Acceptance criteria` section replaced by the confirmed criteria,
     composed by `core/spec.py` into `.crew/<id>/spec.md`.
   - NO-GO, in this order, naming the first failure:
     - no `## Acceptance criteria` section, or no criterion in it;
     - a criterion (a paragraph of the section) without `Given`, `When`
       and `Then`, in that order;
     - a `Then` with no outcome after it, or whose outcome uses a phrase
       from the vague list below;
     - no `## Perimeter` section, or one with no `- ` entry (ADR 0021 §5).
   - **The vague list**: *works*, *correctly*, *properly*, *as expected*,
     *as intended*, *appropriately*, *gracefully*, *seamlessly*,
     *user-friendly*, *intuitive*, *better*, *improved*, *fast*,
     *quickly*, *robust*, *handled*, matched as whole words,
     case-insensitive. Provenance, decision: this ADR's, written from
     `roles/product/prompt.md`'s own definition ("a criterion that names
     no observable outcome") as the words that say an outcome is good
     without saying what is observed. Every committed ticket's criteria
     pass it (checked when it was written). A word joins only with a
     NO-GO case in `ci/dev-phase.sh`.
   - The `spec` step records `spec: {verdict, line, at}`. On NO-GO it
     appends `{question: <the reason>, from: "spec", asked_at}` and moves
     back to `questions`: the person answers, and `product` runs again
     with the reason and the answer as one more turn.
   - No numeric threshold enters this ADR.

5. **The tower keeps every attempt's task.** When a step's result carries
   `task` (assign, rework), the tower also appends it, with the document's
   `attempt`, to `tasks`. A rule of `core/tower.py`, so `assign` and
   `rework`, which M27 and M28 changed, are not touched. The timeline shows
   each attempt's turns, tokens and duration from there.

6. **The console: one timeline per ticket, and an overview.**
   - The logbook goes to `lights-out/logbook/1.1`, a minor (ADR 0047 §2):
     each ticket gains `questions`, `product`, `tasks`, `spec.verdict`,
     `spec.line`, `spec.criteria`, and `timeline`, the ordered phases the
     reader builds from those records, one row each: request, questions
     and answers, spec verdict, assign (perimeter, and the wait M27
     records), each worker attempt with its cost, each gate line, review,
     rework, merge, canary stages, the human answer, 100% or rollback.
     Every row names the record it comes from. A phase without its record
     is absent, never filled.
   - Screen 2, the chain, becomes the timeline. A new overview shows the
     tickets by state, the perimeters held by tickets that are open and
     not yet merged, and the docs a merge left to realign.
   - **M27's wait and M28's docs, from their records.** A refused
     `assign` (ADR 0053 §5-§6) is a `wait` row; the `assign` row that
     follows says how many refusals and since when. A ticket at `opened`
     whose last `assign` was refused carries `wait: {since, refusals,
     holders}`, the holders being the tickets of `perimeters/held` that
     hold now and whose globs overlap its own (ADR 0053's own overlap
     rule); none named means the base or a missing perimeter refused it.
     The logbook carries `perimeters_held` and `docs_to_realign` (each
     merge's `map.docs_ticket` and that ticket's state). The holders are
     read at the logbook's time, not at the refusal's: the tower records
     no reason with a failed step. No number stands in.
   - ADR 0023's rules hold: every screen mirrors `./go logbook` and
     `./go audit`; the answer mirrors `./go answer`.

7. **A kanban, read-only, with feature and priority from the ticket.**
   *Added 2026-11-07, owner's scope addition.*
   - **Two optional header fields** of `tickets/<id>.md`, among the `- Key:
     value` lines before the first `## `, versioned with the ticket:
     `- Feature: <slug>`, a slug of lowercase letters and digits joined by
     single hyphens (`^[a-z0-9]+(-[a-z0-9]+)*$`), naming the feature the
     ticket belongs to; and `- Priority: P0|P1|P2|P3`, P0 the most urgent.
     The spec gate (§4) checks them after the perimeter: a field present
     with any other value, or set twice, is NO-GO. A ticket without them is
     valid and is shown as *unprioritised* or *no feature*: they describe
     how a client plans, not whether a test can be written, so their
     absence must not stop a ticket the gate otherwise passes.
   - **The columns are the tower's states**, in the table's order:
     `requested`, `questions`, `spec-ready`, `opened`, `pr-opened`,
     `review`, `ci-passed`, `ci-failed`, `needs-human`, `merged`, `held`,
     `rolled-back`; a state outside this list gets a column of its own,
     after them. A card sits in the column of its ticket's `state`, nothing
     else. The logbook carries the board, `kanban: [{state, tickets: [id,
     ...]}]`, built by the reader, so the page draws it and computes no
     column of its own; `ci/logbook.sh` proves every card's column is its
     ticket's state and every ticket is on exactly one card.
   - **Within a column**: P0 first, P3 last, unprioritised after P3; then
     the oldest first, by the history's first `at`; then the id.
   - **No drag-and-drop, no button on a card.** A card moves only when the
     tower moves its ticket. A person who could drag a card to `merged`
     would have walked it past the gates, which is the one thing this
     harness exists to prevent. Feature and priority change by editing the
     ticket; the console stays read-only for them and ADR 0023 §2 keeps
     its two writes.
   - **The page filters** by feature and priority and **groups** the
     columns into rows by either; filtering and grouping read the logbook's
     fields and never change a column.
   - **Priority does not order the tower.** At L0 the orchestrator pulls
     one event at a time in the order they were published, and the queue
     at `assign` is M27's (ADR 0053, the perimeter wait). Ordering it by
     priority is a scheduling rule with its own failure modes (a P3 that
     never runs); it waits for M27's queue to exist and for its own ADR.
     Here priority is display only.

8. **No seventh role.** `product` was one of the six (ADR 0020).
   `ci/roles.sh` is unchanged.

## Alternatives rejected

- **The spec gate judged by a model.** Not reproducible at L0, and a
  gate that a model judges is a review, which already exists (ADR 0021).
- **A spec gate on any `Then` that names no number or literal.** Ticket
  0005's recorded criteria ("the first member on the waiting list is
  promoted to attendee") are observable without one; it would refuse
  specs the role confirmed.
- **Pass-through without a trace when no recording exists.** The timeline
  would show a product decision nobody made. `consulted: false` is the
  record of that.
- **Fail `clarify` at L0 when no recording exists.** `./go demo` would
  stop at `requested` until the owner records, and the console could not
  show a ticket from request to rollback without a key.
- **A hand-written product recording for ticket 0001.** Forbidden (plan
  §7 M29).
- **The answer step as a state of its own (`answered`).** A fourth state
  the plan does not name; `answer` from `questions` keeps the table at
  one state per step.
- **Writing the confirmed criteria back into `tickets/<id>.md`.** The
  ticket is the person's document; the tower writes its record, the
  composed spec lives under `.crew/`.
- **A kanban whose columns the page computes from `state`.** It would be
  correct today, and the first page edit that groups two states would
  make the board disagree with the tower with no check to catch it; the
  reader builds it and the ci proves it.
- **Drag-and-drop between columns**, or a priority edited from the
  console: a third write that moves a ticket without the tower, or edits
  the person's document (§7).
- **Priority as a required field, NO-GO when absent.** Every ticket
  committed before this ADR would fail, and nothing a gate judges depends
  on it.
- **Showing M27's wait and M28's docs from a guess** (age of the ticket,
  files touched by the merge): a number with no record behind it (ADR
  0023 §1).

## Consequences

- **The one that hurts first: the worker does not see the confirmed
  criteria yet.** `assign` hands it `tickets/<id>.md`, unchanged here so
  that M27 and M28 could change `assign` without a conflict. Until
  `assign` reads `.crew/<id>/spec.md`, a criterion `product` added after
  an answer reaches the timeline and the gate, not the worker. That is
  the first change after this one lands.
- `./go demo`'s tower now starts at `requested`: two more steps, about as
  long as one adk import and one gate, inside the same budget. Its stages
  and verdict do not change.
- At L0, without the owner's recording, every ticket but 0005's recorded
  request shows `product: not consulted`. That is the truth, and the
  timeline says it.
- Answers under replay are not checked against the recording (§2).
- The logbook's golden and its schema move to 1.1; 1.0 stays committed,
  and the 1.1 golden validates against both (ADR 0047 §2's minor rule).

## Golden-rule check

`./go demo` gains no image and no key: the product step at L0 replays or
records that it was not consulted, the spec gate is Python in the
workbench. `./go console` after `./go demo` shows ticket 0001 from
`requested` to the rollback, with no key. Passes.
