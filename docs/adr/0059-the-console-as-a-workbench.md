# ADR 0059 — The console as a workbench: an interactive kanban that moves no card, a light and dark theme, ADR search from the project map in logbook 1.2, the specs screen and a request editor checked by the spec gate itself, saved through `./go request`'s recipe

- Status: accepted
- Date: 2026-11-08
- Milestone: M33
- Amends: ADR 0047 §2 (the logbook goes to 1.2: the project map's ADRs
  join it) and §3 (a third write, a new ticket's request; one route that
  writes nothing, the spec check); ADR 0055 §2 (`./go request` records
  who requested and through what, as `./go answer` does) and §7 (the
  kanban gains a drawer, search, keys, a view in the link, collapsible
  columns and a highlight when the tower moves a card; the no-drag rule is
  unchanged); ADR 0054 §1 (the map keeps each ADR's status and text, and
  the ADRs and tickets an ADR or a ticket names: map schema 2); ADR 0035
  (the board gains the same theme switch). Consistent with ADR 0029 §3:
  the spec editor that the plan's "Dopo" note set aside comes back here
  because a client running one instance needs it; nothing in it serves
  many instances.

## Context

M29's console is read in a meeting, not worked in. The kanban is a
picture: a card opens the whole timeline, a filter is lost on reload, a
view cannot be sent to a colleague. The theme follows the system only.
The project's decisions, the ADRs, are outside the console, although
M28's map already knows which files each one cites. And a request is
written in any editor; whether its criteria pass the spec gate is found
out after `./go request`, from a `questions` row.

The owner asked on 2026-11-08 for the four pieces, inside ADR 0047 (one
document, the logbook; standard-library Python and one HTML file; no
asset from the network) and ADR 0055 §7 (a card moves only when the
tower moves its ticket).

## Decision

1. **The kanban is interactive and still moves no card.**
   - A card's column is the logbook's `kanban`, as in ADR 0055 §7. The
     page holds one view, `{q, feature, priority, group, empty,
     collapsed, card}`, and every interaction changes the view and
     nothing else: no `draggable`, no drag or drop listener, no request
     but the GETs the page already makes. Interactions are:
     - **a drawer** beside the board with the selected card's state,
       outcome verdict and line, its last timeline row, its perimeter,
       its wait (ADR 0053), the ADRs that govern its perimeter (§3) and a
       link to the full timeline;
     - **search** over the id, the title and the perimeter: a path
       matches a card when one of its globs matches it (`*` crosses `/`,
       as `core/perimeter.sh` matches) or contains it;
     - **keys**: the arrows move the focus between cards (left and right
       across columns, up and down inside one), Enter opens the drawer,
       Escape closes it, `/` focuses the search;
     - **collapsible columns**: a collapsed column shows its state and
       count only.
   - **The view is the link.** The hash carries it:
     `#kanban?q=…&feature=…&priority=…&group=…&empty=hide&collapsed=a,b&card=0005`,
     keys in that order, defaults left out, values percent-encoded. A
     page opened on a hash draws the same lanes, columns, cards,
     collapsed columns and open drawer as the page that wrote it.
   - **Live, a moved card shows its move.** The page remembers each
     card's column from the last logbook it drew; when a new logbook
     puts a card in another column, the card is drawn in its new column
     with a mark naming the column it left, highlighted for 4 s
     (provenance, decision: two beats of the console's 2 s poll, ADR
     0047 §4, so the highlight outlasts the refresh after the one that
     moved it). With `prefers-reduced-motion` the slide is off and the
     mark stays.
   - A refresh redraws only when the logbook changed, and keeps the
     focus, the drawer and the search box's text and caret.
     `/v1/logbook.json` answers with an `ETag`, and `304` to a matching
     `If-None-Match`: a 2 s poll of an unchanged logbook costs no body.

2. **A light and a dark theme, the system's by default.** A switch in
   the header with three values, `system`, `light`, `dark`; `system`
   follows `prefers-color-scheme`. The choice is kept in the browser's
   `localStorage` under `lights-out.theme`; every read and write is in a
   `try`, and a browser that refuses storage gets the system theme and a
   switch that works until the page closes. The theme is the colour
   tokens on `<html data-theme>` and nothing else: no screen reads it,
   no text changes with it. `board/index.html` gets the same switch, the
   same tokens rule and the same key (the board and the console are
   different origins, so each remembers its own choice).

3. **ADR search reads the project map through logbook 1.2, not a second
   route.**
   - **The map keeps what search needs** (ADR 0054 §1, map schema `2`):
     each ADR's `- Status:` line and text, and `mentions` edges from an
     ADR or a ticket to each ADR (`ADR NNNN`) and ticket (`ticket NNNN`)
     its text names that the map holds. A mention is not a citation:
     the drift check (ADR 0054 §3) does not read it, so an ADR naming
     one the plan has reserved is not drift. A map of schema `1` is
     refused by every reader, as today; `./go map` rebuilds it.
   - **The logbook carries them**: `adrs: {map_sha, items: [{number,
     path, title, status, text, files, tickets, adrs}]}`, read from
     `MAP_FILE` (default `.crew/map.sqlite`) in read-only mode. `files`
     are the paths the ADR cites that exist at the map's commit;
     `adrs` the ADRs it mentions or that mention it; `tickets` the
     tickets it mentions or that mention it, and the logbook's tickets
     it governs. A map built at another commit than HEAD is still read;
     `map_sha` says which. No map: `adrs` is `{ok: false, error}` with
     `./go map` in the error, and the screen shows it.
   - **A ticket's ADRs**: each logbook ticket gains `adrs`, the ADRs
     that govern its perimeter: those citing a file one of its globs
     matches. The page draws it; it computes no relation of its own.
   - **The screen** searches number (`59`, `0059`, `ADR 0059`), title,
     status and text, every word of the query in one ADR, case
     ignored, and a path among its `files`; each result shows its
     status, its files, its tickets and its ADRs, each a link.
   - **Why not a second route.** A `/v1/map.json` would make the console
     read something besides the logbook (ADR 0047 §2), name `.crew/` in
     `console/` (which `ci/logbook.sh` forbids), and leave the snapshot
     mode, and the site's stills drawn from it, with no ADRs. In the
     logbook the map is one more source of the reader's, and a consumer
     that collects the logbook gets the decisions with the record. The
     cost is size: the text of this repository's ADRs is about 700 kB
     (measured 2026-11-08: 698 110 bytes over 56 files), which the
     `ETag` of §1 keeps off the 2 s poll.

4. **The specs, and a request editor the spec gate checks.**
   - **The specs screen**, from logbook fields 1.1 already has: every
     ticket with its state, the confirmed criteria and who confirmed
     them, the last `spec` verdict with its line and time, and the open
     question with its answer box (ADR 0055 §2).
   - **The editor** has the fields of a request: title, context,
     criteria (each a Given, a When and a Then), perimeter (one path or
     glob per line), feature and priority. `python3 core/spec.py draft`
     turns them into the ticket's Markdown, under the next free id; the
     page shows that file as it would be written.
   - **The live check is the gate itself.** `POST /v1/spec-check` takes
     the fields, has `core/spec.py draft` write the draft into a
     temporary directory, runs `sh gates/spec.sh` on it, deletes the
     directory and answers `{ticket, markdown, line}`: the gate's own one
     line, `GO: spec` or `NO-GO: spec — <why>`. It writes nothing in the
     repository or the tower; the page calls it 300 ms after the last
     keystroke (provenance, decision: one call per pause, not per key).
     Refused with `CONSOLE_AUTH=public`, where it would run a process per
     request from the open network; allowed in snapshot mode, which runs
     no plane and writes nothing.
     Why not the rules in JavaScript: two copies of `core/spec.py`
     would need a test proving them equal forever, and the first change
     to the vague list would split them. Why not a model: the gate is
     deterministic by design (ADR 0055 §4).
   - **The product role's questions come through the tower.** A question
     asked on an unsaved draft would leave no record, and ADR 0055 makes
     every question a record with who answered. So: at L1, with a key,
     saving the draft is how the product role is consulted; its
     questions open the ticket at `questions` and the answer box takes
     them. At L0 the editor says before saving that the product role
     replays recordings, a new request has none, and the spec gate alone
     will judge it (ADR 0055 §3); it never shows a question it did not
     receive.
   - **Saving is the console's third write.** `POST /v1/request` takes
     the same fields; the console runs the check again and refuses a
     NO-GO draft (at L0 it would loop between `questions` and the gate
     with nothing to change it); then `core/spec.py draft --write`
     creates `TICKETS_DIR/<id>.md` with an exclusive create, and the
     console runs `make request TICKET=<id>`, the recipe `./go request`
     runs, with `REQUEST_BY` the identity and `REQUEST_VIA=console`.
     `core/tower.py request` records both on the `request` history
     entry, `cli` and `unauthenticated` by default, as `answer` does
     (ADR 0055 §2), and the timeline's request row shows them.
     - The rules are the answer's: refused in snapshot mode (403) and
       with `CONSOLE_AUTH=public`; behind `proxy` a request without the
       identity header gets 401 and any identity the proxy sets may
       request (as it may answer: `CONSOLE_LAUNCHERS` stays the
       go-for-launch list); `Content-Type`, `Origin` and `Host` as every
       POST; one press at a time.
     - The id is the next after the highest four-digit id in
       `TICKETS_DIR` (provenance, external: the `tickets/NNNN.md` names
       `core/map.py` reads). Two saves at once: the second create fails,
       409, nothing requested.
     - The file is written, not committed. The console says so and names
       it; committing it is the person's, with the change or before.
   - **Why a write and not a download.** The answer box (ADR 0055 §2)
     already lets a client move a ticket from the console; handing back
     a file to commit, then `./go request` in a terminal, would make the
     editor the one screen that cannot finish its own job. The write
     adds no power the CLI lacks: it is `./go request` with the file the
     gate just passed.
   - Body sizes: 16 KiB for both routes (provenance, decision: twice the
     answer route's 8 KiB, ADR 0055 §2, for a request's several criteria).
     Timeouts: 10 s for the check (provenance, decision: the gate reads
     one file, no network), 900 s for the request, the answer's.

5. **Proof.** In `./go ci`, cold, no browser:
   - `ci/logbook.sh` builds logbook 1.2 from its stub sources, a stub map
     included, byte-equal to `fixtures/logbook/golden-1.2.json`; checks
     `adrs` and each ticket's `adrs` against the map's citations and the
     tickets' perimeters; checks `index.html` declares no `draggable`
     and no drag or drop listener and fetches `/v1/` only, with four
     POST routes; `serve.py` runs only `core/logbook.sh`,
     `core/promote.sh`, `gates/spec.sh`, `core/spec.py draft`, `make
     answer` and `make request`. At runtime: `/v1/spec-check` gives the
     line `gates/spec.sh` gives on the same file, on a GO draft and on
     one NO-GO draft per rule of ADR 0055 §4, and leaves the repository
     and the tower as they were; `/v1/request` is refused in snapshot,
     public and without the proxy header, refuses a NO-GO draft with
     nothing written, and on a GO draft writes the next id and runs
     `make request` with who pressed and `via: console`; the `ETag`
     answers 304.
   - `core/tests`: `core/spec.py draft`, the tower's request by/via, the
     map's schema 2 (status, text, mentions; mentions not drift).

   In a browser, `ci/console-ui.py` (Playwright, the image `site.yml`
   pins, a job of `ci.yml`; the workbench has no browser): over the
   golden in snapshot mode, a scripted sequence of every interaction of
   §1 leaves every card in its logbook column and sends no POST; a view
   set by interactions, opened in a new page from its hash, draws the
   same board; the main text of every screen is the same in the three
   themes, and with storage refused; the editor's line is the gate's on
   the drafts above; the board's switch changes its theme only. Over a
   logbook read from this repository's own map, the ADR search finds an
   ADR by its number, by a word of its title and by a file it cites.

6. **No seventh role, no new port, no dependency.** `product` asks
   through the tower as before; the map is ADR 0054's file; Playwright
   is the site's pinned image, not a workbench package.

## Alternatives rejected

- **Drag-and-drop with the tower's consent** (a drop that requests the
  step instead of moving the card): a card would still look moved
  before the tower judged it, and a drop on `merged` is exactly the walk
  past the gates ADR 0055 §7 forbids. Keys and a drawer give the speed
  without the power.
- **Theme by a cookie or a query parameter**: a cookie travels to the
  server for nothing; a parameter would make the view link carry a
  colour, which is a viewer's taste, not part of the view.
- **A second read-only route for the map** (§3, why not).
- **The spec rules ported to JavaScript** (§4, why not).
- **The product role asked on the unsaved draft**, a model call from the
  console outside the tower: no record, and a seventh path to a model.
- **A downloaded file instead of the write** (§4, why a write).
- **Saving a NO-GO draft** and letting the tower bounce it: at L0 no
  role can change its criteria, so it would wait at `questions` forever.

## Consequences

- The logbook is `lights-out/logbook/1.2` with
  `core/logbook/1.2.schema.json`; 1.1 consumers ignore the new fields.
  `fixtures/logbook/golden-1.1.json` becomes `golden-1.2.json`;
  `site/render.py` and `site.yml` follow it.
- The map is schema `2`: a map left from M28 is refused until `./go map`
  rebuilds it; the merge step rebuilds it anyway.
- The console has three writes and one route that writes nothing;
  `console/README.md`, `docs/plans/console.md` and `docs/reference.md`
  say so. `./go request` takes `REQUEST_BY`.
- `ci.yml` gains a `console-ui` job: the interactions are proved where a
  browser is, on every push, not in `./go ci`.
- The site gains stills of the specs editor and the ADR search; the
  kanban still shows its drawer.

## Golden-rule check

`./go demo` neither opens the console nor reads the map's new fields:
its ticket 0001 runs from `requested` to the rollback as before, and the
merge step rebuilds the map at schema 2 in the same time it built schema
1 (the text it now stores is read in the same pass). No key, no account:
at L0 the editor's check is the gate, and the product role's absence is
said, not faked.
