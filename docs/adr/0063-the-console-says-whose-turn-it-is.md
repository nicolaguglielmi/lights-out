# ADR 0063 — The console says whose turn it is: the kanban fits the flow under four bands, every ticket carries a now line read through a checked copy of the tower's table, the timeline reads top-down, the overview opens with what waits for a person, the editor keeps its verdict in view

- Status: accepted
- Date: 2026-11-09
- Milestone: M17, first slice (ADR 0047's open decision 2 folds M17's
  dashboard into the console)
- Amends: ADR 0059 §1 (how the kanban is drawn; the view and its link are
  unchanged); ADR 0055 §6 (the timeline's and the overview's layout); the
  rule of `docs/plans/console.md` that "the console does not interpret
  states" (§2). ADR 0047 stands: the console reads one document, the
  logbook, still 1.2, and writes only its three POSTs.

## Context

On 2026-11-09 one ticket was followed through the console at 8e968aa,
from the request editor to a rollback. The run used desktop and phone,
light and dark, and the keyboard only. It ran over ci/logbook.sh's stub
root, with the tower document staged in the tower's own shapes. The
notes and screenshots are kept outside the repository. The launch
gallery shows the kanban, a timeline down to the rollback, and the
overview. Findings, ranked by what they cost a first-time viewer:

- **F1.** The 13 columns are a fixed 190 px each. At 1440 px the first
  seven show, and they are mostly empty. `ci-failed`, `needs-human`,
  `held` and `rolled-back` are off-screen, and the drawer covers 420 px
  more. A phone shows 1.7 columns.
- **F2.** Nothing says who has the ball. The moments that need a person
  look like every other state. A held canary reads "GO", and `ci-failed`
  reads as terminal while the rework loop is still running.
- **F3.** The timeline is a flat table with no "now". The rollback's
  numbers are a long scroll down, and there is no way back to the
  kanban.
- **F4.** The answer box is on the timeline only. Go-for-launch neither
  names the held ticket nor is linked from it.
- **F5.** The overview does not start with what needs a person.
- **F6.** The spec gate's line sits below the editor's form. After a
  save the draft stays filled, so a second Enter files a duplicate.
- **F7.** A reworked ticket's first gates NO-GO row shows "—".
- **F8.** The column headers are tower ids, with no grouping into the
  flow.
- **F9.** The `at` column wraps. A `GO: ci` row carries a "pending"
  chip. On a phone the nav takes five rows.

## Decision

1. **The kanban fits the flow (F1, F8, F9).**
   - **The columns stay the logbook's `kanban`, in its order.** Runs of
     consecutive columns are drawn under four bands:
     - **Spec**: requested, questions, spec-ready;
     - **Build**: opened, pr-opened;
     - **Test and review**: review, ci-passed, ci-failed, needs-human;
     - **Release**: merged, main-green, held, rolled-back.

     A state that no band names goes under **Other**, at the position
     where the logbook puts it. A band never moves a column. A band
     names where the ticket is, not who moves it: `pr-opened` sits under
     Build while the gates run, and the now line (§2) says the gates
     run.
   - **A column with no card in the current view is drawn narrow**, with
     its state and count only. This is a drawing rule. It is not part
     of the view or the link, and it is not ADR 0059's collapsed
     columns, which stay the person's choice in the hash. `empty=hide`
     still hides empty columns. The columns with cards share the
     remaining width.
   - **Below the phone breakpoint** the columns stack one under another,
     and the nav scrolls in one line.
   - **Numbers:**
     - **1440×900 and 390×844.** Provenance, external: the launch
       gallery's capture sizes (site/render.py's desktop and phone
       viewports).
     - **A narrow column is 40 px.** Provenance, decision: it holds the
       column's toggle and its state written sideways. With it, the
       golden's board (seven columns with cards, seven empty) is about
       1350 px wide. That fits inside 1440 px less the page's padding.
       At 48 px it does not fit.
     - **A card column is at least 140 px.** Provenance, decision: it
       holds a card's id, a two-line title and its now line without
       cutting a word.
     - **The breakpoint is 720 px.** Provenance, decision: below it the
       viewport holds less than half of the golden's board (about 650
       of 1350 px). One column under another reads better than
       scrolling sideways through more than half the flow.

2. **Every ticket has a now line (F2, F4).** The now line is one
   sentence: who has the ball and what it waits for. It appears on every
   card, at the top of the drawer and at the head of the timeline, with
   the same text in all three. The first rule that matches wins:
   1. **The last history entry failed** (outcome `stranded`).
      - If `wait` names holders (ADR 0053), the ticket waits for them,
        then for `./go resume TICKET=<id>`.
      - Otherwise it is **Your turn**: the line names the step that
        stopped and its line, and shows `./go resume TICKET=<id>` as
        text to copy. The console gains no resume write.
   2. **`questions` with an unanswered last question**: **Your turn** to
      answer the product role's or the spec gate's question. The action
      links to the timeline's answer box.
   3. **`needs-human`**: **Your turn**. The rework cap was reached and a
      person decides. The action links to the timeline's rework rows.
   4. **`held`**:
      - If a human judged it, the line says it was promoted to 100% or
        rejected, and by whom (the reader's outcome line).
      - If its `release.version` is the launch record's, and the record
        has neither `promoted` nor `rejected`, it is **Your turn** for
        go-for-launch at the record's `held_at`. The action links to the
        launch screen.
      - Otherwise the line says it is held with no answer, and the
        launch record now belongs to another version.
   5. **`rolled-back`**: rolled back automatically, followed by the
      reader's outcome line, which names the stage and the number.
   6. **Otherwise**, the line names who runs `NEXT[state]`, the step the
      tower requests next:
      - the product role (`clarify`, and `answer` for an answered
        `questions`);
      - the spec gate;
      - the dev-worker;
      - the gates, attempt n;
      - the reviewer, attempt n;
      - the merge step;
      - main's check;
      - the canary.

      At `ci-failed` the dev-worker reworks the change, attempt n+1 of
      `REWORK_MAX_ATTEMPTS`. At the cap, the rework step hands the
      ticket to a person. A state with no next step says the table
      requests none from it.

   - **Numbers in the line come from the logbook**: `attempt`, the
     `REWORK_MAX_ATTEMPTS` threshold and `held_at`. A threshold that
     cannot be read is left out, never defaulted. The sentences carry
     no number of their own: launchpad's 5% and 25% belong to
     core/launchpad.sh, not to the page.
   - **The line describes the ticket, not the viewer.** It reads the
     same in snapshot mode and for an identity that may not press. The
     place it links to says why the action is disabled (ADR 0047 §4).
   - **Where the table lives: in the page, checked, as the board's is.**
     console/index.html embeds
     `<script type="application/json" id="tower-machine">` with:
     - `next`;
     - `who`, one sentence per step;
     - `bands`.

     It does not embed `precondition`, because the page reads none.
     `./go ci` proves, cold, three things:
     - `next` equals core/tower.py's `NEXT`;
     - `who` names every step of `PRECONDITION` and no other;
     - the bands' states, concatenated, are core/logbook.py's `COLUMNS`
       in order.
   - **Why this is not what ADR 0055 §7 rejected.** That alternative
     had the page compute columns from `state`, with no check. Here the
     columns are still the logbook's and no card moves. The copy
     decides no verdict: outcome, judge and line stay the reader's. It
     names who runs the next step, and it is proven equal to the
     tower's table on every `./go ci`.

3. **The timeline reads top-down (F3, F4).**
   - **The head** holds the now line and its one action (the answer box
     stays where it is). Below it is a strip of the four bands, each
     marked done, now or ahead.
     - For a ticket that ended NO-GO (rolled back, `needs-human`, or
       rejected at go-for-launch), the strip marks the band that
       stopped it.
     - A ticket opened directly marks Spec **skipped**, not done,
       because no spec gate passed. Its `open` row says so.
   - **The rows are the logbook's**, in its order, none dropped or
     reordered. They sit under band headings, and a new heading starts
     wherever the band changes. A reworked ticket therefore reads
     Build, Test and review, Build, Test and review.
   - **The phase → band map belongs to the page and is unchecked.** A
     heading is only a label: a phase the map does not name stays under
     the heading above it.
   - **A link goes back to the kanban** with the card open:
     `#kanban?card=<id>`.
   - **The go-for-launch screen names the ticket** whose
     `release.version` is the record's, and links to its timeline. A
     release that no ticket carries (`./go demo-launch`) says so.

4. **The overview opens with "Waiting for you" (F5).** The first panel
   lists exactly the tickets whose now line is a person's turn, each
   with its line and its action. Then come the tickets by state, under
   the four bands. Perimeters, waits and docs stay below, unchanged.

5. **The editor keeps its verdict in view and files once (F6).**
   - The spec gate's line and the Request button sit in a bar held at
     the bottom of the viewport while the form is filled.
   - After a save the console answers GO, the page:
     - clears the draft;
     - disables Request until the next edit;
     - shows the link to the new ticket's timeline at the top.
   - The server is unchanged. Two equal requests are still two tickets,
     as with `./go request`.

6. **Small fixes (F9).**
   - The `at` column does not wrap.
   - A `gates` row whose tower state is `review` shows GO, not pending:
     the gates passed the change and routed it to the reviewer.

7. **Proof.** The tests are written from this ADR.
   - **Cold, in `./go ci`:** the table check of §2. It is a case of
     ci/logbook.sh, which already reads console/index.html without a
     browser, so it needs no new `CI_CHECKS` line.
   - **In ci/console-ui.py** (Playwright, GitHub Actions), over the
     golden:
     - **At 1440×900** with no drawer open, the whole board fits, with
       no sideways scroll on the page or on the board.
     - **At 390×844** the columns stack, the page does not scroll
       sideways, and the nav is one line.
     - **The now line** is on every card, in the drawer and on the
       timeline. "Your turn" appears exactly on these tickets:
       - 0009 (open question);
       - 0007 (`needs-human`);
       - 0004 and 0005 (stranded, no holder);
       - a held ticket whose version is the launch record's, on a
         logbook the test derives from the golden (as `d_editor`
         derives an L1 one).
     - **The overview's first panel** lists exactly those tickets.
     - **The timeline:** its rows, read in order, are the logbook's.
       Its back link opens the kanban with that card's drawer. The
       launch screen names the derived held ticket.
     - **The editor:** its line and button are inside the viewport
       while a criterion has the focus, at both sizes. A save answered
       GO clears the draft and disables Request. The test says how GO
       was answered: live over a stub root, or intercepted.
     - **Every earlier console-ui check still holds:** no card moved, no
       POST but the allowed ones, the view in the hash, the same text
       in every theme.

## Alternatives rejected

- **A `now` field built by core/logbook.py (logbook 1.3).** The reader
  would own presentation text. The schema, the golden, site/render.py
  and every consumer would move for a sentence a person reads. A 1.2
  logbook already served or collected would have no now line. The
  page's checked table gives a now line to every 1.x logbook.
- **A `next` step field in the logbook (a fact, not text).** It removes
  the copy of `NEXT`, but not the who and band maps, and it still costs
  a minor version.
- **No table, just "in state X".** F2 stays unfixed.
- **Renaming the columns.** ADR 0055 §7: the columns are the tower's
  states. The bands and the now line explain them.
- **A drawer docked beside the board.** It halves the width that §1
  wins back.
- **An answer box inside the drawer.** It would be a second copy of a
  write's UI. The drawer's now line links to the box instead.
- **Sideways scrolling on a phone.** One column of fourteen in view is
  F1 again.
- **Only F2's three moments count as a person's turn.** A stranded
  ticket moves only when a person runs `./go resume` (ADR 0034).
  Leaving it out of "Waiting for you" is F5 again.

Left out, ranked:

- **F7.** The reader keeps the gates line on the last `ci` only. Fixing
  it is a reader change and a logbook minor, and the line is already on
  the `rework` row. It is a follow-up.
- **A committed snapshot of a real demo for the site.** A demo's
  logbook trips gitleaks (site/render.py's comment, ADR 0049 §4). The
  gallery stays drawn from the golden.

## Consequences

- **The one that hurts: the console now reads states, not only the
  reader's verdicts.** A new tower state or step fails `./go ci` until
  console/index.html's table, its who and its bands follow. Two pages
  now carry a checked copy of the table (the board's, ADR 0035).
  `docs/plans/console.md`'s "a new tower state changes the reader, not
  the screen" still holds for the verdict, but no longer for the now
  line.
- **The fit holds for the golden, not for every board.** At 1440 px,
  seven columns with cards fit beside seven narrow ones, and an eighth
  scrolls sideways. Collapsing columns (ADR 0059) is the person's way
  to fit a busier board.
- **The phase → band map can drift from the reader's phases** without a
  red check. The cost is a row under the wrong heading, never a row lost
  or moved.
- **PLAN's M17 first-slice acceptance** names the stranded tickets
  among the person's turns.
- **The site's console stills** are re-rendered from the golden. The
  kanban still no longer needs `empty=hide` to show the flow.
- **Unchanged:** the logbook, its schema, the golden, the snapshot and
  console/serve.py.

## Golden-rule check

`./go demo` does not open the console. The logbook stays 1.2. There is
no image, key, dependency, build or network asset. Passes.
