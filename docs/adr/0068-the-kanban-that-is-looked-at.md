# ADR 0068 — The kanban that is looked at: Trello's look without Trello's drag, a card face with labels and a badge row, a modal back for the drawer, a moved card that flies, a "Your turn only" filter, AA contrast proven in both themes

- Status: accepted
- Date: 2026-11-10
- Milestone: M17, second slice
- Amends: ADR 0059 §1 (the drawer becomes the card's back, a modal
  dialog; the view gains `turn`; a moved card flies instead of sliding);
  ADR 0063 §1 (how cards and columns are drawn; widths unchanged) and §2
  (the embedded table gains `actor`, checked like `who`). ADR 0063's
  rejected answer box is weighed again and stays rejected. ADR 0047 and
  ADR 0055 §7 stand: one logbook, still 1.2, three writes, no card moved
  by the page.

## Context

ADR 0063 made the board readable: it fits 1440 px, and every card says
whose turn it is. The owner asked on 2026-11-09 for the second slice: the
look and pace of Trello, and the best usability possible, without
losing what keeps the board honest (PLAN, M17 second slice). Screenshots
of the golden logbook (9 tickets in 14 columns, 7 with cards), taken
2026-11-10 and kept outside the repository, show the following:

- The cards are dense text. Priority and feature are grey chips at the
  bottom.
- The docked drawer hides the Release band and the Other column.
- The bands look the same, and nothing shows who has the ball without
  reading a sentence.
- The phone stack is about three screens tall, with no way to jump to a
  band.

At 1440 px the golden board fits exactly with ADR 0063's widths, so
nothing here may widen a column.

## Decision

1. **"Trello-style" is the look, the motion and the speed. It is not
   dragging.**
   - **The look:** each list is a rounded panel on a tinted board. Cards
     are white with a soft shadow and lift on hover. Coloured labels sit
     at the top of the card and a badge row at the bottom. Each band has
     a thin coloured rule above its name. Band colours never reuse the
     GO, waiting or NO-GO hues.
   - **Fixed:** the 40/140 px columns, the 720 px breakpoint and the
     1440 fit of ADR 0063. There is no density switch. The badge row
     wraps; it never widens a column.
   - **Unchanged:**
     - no `draggable` and no drag or drop listener. A card moves only
       when the tower moves its ticket.
     - the logbook 1.2 is the only input.
     - one hand-written HTML page, inline SVG icons, no build, no
       dependency and no network asset.
     - the view stays in the hash.
     - the now line's text is the same on the card, the back and the
       timeline.

2. **The card face.** It has four parts, top to bottom.
   - **Labels:**
     - Priority: P0 to P3, one colour each.
     - Feature: one of six colours. The colour is the sum of the name's
       UTF-16 code units, modulo 6. The same feature has the same colour
       on every board, and adding a feature edits no table. Two features
       may share a colour, but the label always carries the name. Six:
       provenance, decision, borrowed from Trello's default label set.
     - A ticket without a priority or a feature has no label for it.
   - **The title**, then **the now line**, both unchanged.
   - **The badge row:**
     - **The actor, as an icon.** When the now line's turn is `tower`,
       the actor is the embedded table's new `actor[step]`: `product`,
       `spec`, `dev`, `gates`, `reviewer`, `tower` (merge and mainline)
       or `canary`. Otherwise it is the turn itself: `you`, `stopped` or
       `done`. A state from which the table requests no step (the
       golden's `assigned`) gets no actor badge.
     - **Time in state.** It counts from the `at` of the first entry in
       the run of history entries at the end that carry the ticket's
       state. If the last entry's state is not the ticket's state, the
       time is left out. The "now" it counts to:
       - live: the console's `read_at`;
       - snapshot: the newest history `at` in the logbook, so a snapshot
         and the site's stills read the same on any day.

       It reads the viewer's clock in neither mode. It is shown in
       minutes, hours or days, inside a `<time datetime>` whose title is
       the exact instant.
     - **Rework attempts, as pips:** filled for the ticket's `attempt`,
       out of `REWORK_MAX_ATTEMPTS` from the logbook's thresholds. If
       either cannot be read, the pips are left out, never defaulted
       (ADR 0063 §2).
     - **The outcome chip.**

3. **The card's back is a modal dialog.**
   - **How it behaves:**
     - It is a native `<dialog>` opened with `showModal()`. Focus moves
       in and stays in, and the board behind it is inert.
     - Esc or a click on the backdrop closes it, and the focus returns
       to the card.
     - It holds `aside#drawer[data-id]`, so `card=<id>` in the hash and
       ADR 0059's tests hold.
     - It is at most 720 px wide, and full screen below 720 px.
       Provenance, decision: ADR 0063's breakpoint, so the page has one
       width number, not two.
   - **What it shows:**
     - the labels and the title;
     - the now line, with its one action drawn as a primary link:
       - Answer it, which opens the timeline's answer box;
       - See the attempts;
       - Go-for-launch;
       - or `./go resume TICKET=<id>` as text to copy (no clipboard
         call);
     - the open question's text, when there is one;
     - the four-band strip, equal to the timeline's;
     - a compact timeline: phase, verdict, line and `at`, in the
       logbook's row order;
     - the perimeter, the wait, the ADRs and the link to the whole
       timeline.
   - **Why the docked drawer goes:** it covered the Release band, and a
     modal over a dimmed board is what a Trello card's back is.
   - **The answer box stays out of the back**, and go-for-launch stays a
     link, not a button. ADR 0059 §1 holds that every kanban interaction
     changes the view and nothing else, and every console-ui run proves
     it by recording that the page sent no POST. A write in the back
     would make the kanban a second place that writes. The question's
     text can be read in place, and the one action takes you to the
     only box.

4. **Motion.**
   - **A moved card flies.** When a refresh moves a card (ADR 0059's
     mark), it flies from its old position to its new one, using FLIP
     with the Web Animations API.
     - The flight takes 400 ms. Provenance, decision: it ends inside one
       2 s poll (ADR 0047 §4), so two flights never overlap, and inside
       ADR 0059's 4 s mark.
     - The back fades and scales in.
     - With `prefers-reduced-motion` there is no flight, scaling or
       lift. ADR 0059's mark stays.
   - **Every move is announced** in an `aria-live="polite"` region:
     "<id> moved from <old> to <new>". Provenance, external: WCAG 2.1 SC
     4.1.3, Status Messages.
   - **Long columns, from 720 px up:** a column taller than the window
     scrolls inside itself, and its header stays visible. The board
     scrolls sideways, so a header cannot stick to the page; only a
     column that scrolls itself can keep its header in view.

5. **Usability.**
   - **"Your turn only"** is a toggle button with `aria-pressed`. It is
     part of the view: hash key `turn=you`, and the key order becomes
     `q, feature, priority, turn, group, empty, collapsed, card`.
   - **The selects stay.** They come with native keyboard and
     screen-reader support, and they are the DOM contract the tests
     already use.
   - **Search** still filters (ADR 0059). It also marks the match in the
     card's title with `<mark>`. A card matched only by its perimeter is
     shown without a mark.
   - **Below 720 px**, a sticky bar with one button per band scrolls to
     that band. It changes no hash and adds no history entry.

6. **Accessibility.** Provenance, external: WCAG 2.1 AA, in both themes.
   - Text contrast is at least 4.5:1, or 3:1 for large text (SC 1.4.3).
   - UI components and focus indicators are at least 3:1 against what
     is next to them (SC 1.4.11, applied to focus indicators as its
     Understanding document does).
   - Focus is always visible (SC 2.4.7), through `:focus-visible` on
     every control and card.

7. **No Stitch design loop yet** (ADR 0023 §6, ADR 0047 open decision
   6).
   - Stitch's export format is still unverified; it was not checked
     again for this ADR.
   - A design in `design/` that no test reads would drift from the
     page.
   - The console stays built by hand, and `console/README.md` says so.
     The alternative is kept: rebuild the page from a Stitch design,
     against the same tests.

8. **Proof.** The tests are written from this ADR.
   - **Cold, in `./go ci`:** a case of ci/logbook.sh checks that `actor`
     names every step of core/tower.py's `PRECONDITION` and no other,
     with values from §2's list.
   - **In ci/console-ui.py** (Playwright, GitHub Actions), over the
     golden unless a case says otherwise:
     - **Each card's face:** the labels; the actor by §2's rule; the
       time in state against the reference "now"; the pips; the colour,
       the same for every card of a feature.
     - **The back:**
       - it is modal, with the board inert;
       - the focus moves in, and comes back to the card on close;
       - Esc closes it, and so does a click on the backdrop;
       - the now line equals the card's, and the strip equals the
         timeline's;
       - the compact rows are in the logbook's order;
       - the one action is there, and the question's text when there is
         one;
       - no POST is sent.
     - **"Your turn only"** shows exactly 0009, 0007, 0004 and 0005. It
       is written to the hash, and the link opens it again.
     - **The flight and the announcement**, on a live console simulated
       by intercepting `/v1/console.json` and `/v1/logbook.json` (the
       test says so):
       - without reduced motion, the moved card runs an animation;
       - with reduced motion, it runs none;
       - the mark and the announcement appear in both cases.
     - **A long column**, on a logbook the test derives from the golden,
       scrolls inside itself with its header in view.
     - **Contrast and focus**, in both themes: every text in the board
       and in the open back, measured against the first opaque
       background behind it.
     - **The phone's band bar**, and the `<mark>` that search adds.
     - **No drag:** no `draggable` attribute, and no drag or drop
       listener registered. An init script that wraps
       `addEventListener` records this.
     - **Every earlier console-ui check** is still green.
   - **The site's stills** are re-rendered, with a new still of a card's
     back.
   - **Usability:** ADR 0063's audit is run again after the change (six
     questions per state, desktop and phone, light and dark, keyboard
     only) and compared with the audit of 2026-11-09, outside the
     repository. A test with people who do not know the project happens
     only if the owner organises it.

## Alternatives rejected

- **Drag as a gesture that asks the tower for a step.** It reverses ADR
  0059's rejected alternative. That decision is the owner's, and needs
  its own ADR.
- **The answer box in the back** (§3). Kept as the alternative if the
  owner wants one place fewer to go: the same answer panel, drawn in the
  back.
- **Chips instead of the selects.** The same view, rewritten, with no
  new capability, losing native keyboard support and the tested DOM.
- **Feature colours from a table.** Every new feature would need an
  edit, or it would fall back to grey.
- **Time in state measured against the viewer's clock.** A snapshot, and
  the site's stills, would age every day.
- **Scroll-snap to the bands.** With a plain sideways scroll nothing
  breaks: the golden fits at 1440 px and stacks below 720 px, and
  collapsing columns (ADR 0059) already fits a busier board.
- **A compact/comfortable density switch.** It is a second layout to
  prove for a taste, not a need.

Left out, ranked: avatars of people (the logbook names roles; only a
human row's `by` is a person); card covers and images; F7 (still a
reader change, ADR 0063).

## Consequences

- **The one that hurts:**
  - **Two cards can no longer be read side by side.** The back is
    modal, and the board behind it is inert.
  - **The face carries more that the page computes:** colour by hash,
    age and pips. Only `actor` is checked against the tower.
  - **The contrast proof pins the tokens.** Any palette change must
    pass it in both themes.
- **The site's stills change**, and the gallery gains the card's back.
- **Unchanged:** the logbook, its schema, the golden, the snapshot and
  console/serve.py.

## Golden-rule check

`./go demo` does not open the console. The logbook stays 1.2. There is
no image, key, dependency, build or network asset. Passes.
