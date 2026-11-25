# ADR 0071 — The governed network, on screen and in text: logbook 1.3 keeps each attempt's workers, its patch (capped, and shown only once the secrets gate passed it) and every gate's line; a `#network` screen draws the tower, the six roles, the workers, the gates lighting GO or NO-GO, the rework loop and the human gate, beside the stream of attempts and the files by perimeter; `./go network` prints the same facts in the same words from the same logbook; at L0 with no key the screen replays the logbook's own record, labelled as a replay

- Status: accepted
- Date: 2026-11-11
- Milestone: M17, third slice
- Amends: ADR 0047 §2 (the logbook goes to 1.3: optional additions only);
  ADR 0059 §1 (a new screen with its view in the link); ADR 0038 §5 (a
  rework entry also keeps the rejected attempt's gate lines); ADR 0036
  (a network attempt's `members` reach the tower document); ADR 0048 (the
  site gains one clip, captioned as a replay). ADR 0063's rejected
  alternative, sentences carried by the logbook, is weighed again and
  stays rejected. ADR 0047 §6 stands: the console and the CLI read the
  logbook and nothing behind it, and the console gains no write.

## Context

The owner asked on 2026-11-11, after Google Antigravity's "Agent Teams"
post (its video was described by the owner; the post was not re-read for
this ADR), for a screen of the agent network at work. Antigravity shows a
free-form agent map, each worker's edits and a file tree. lights-out's
version should show what Antigravity does not: the governed network.
The same day the owner added that every fact on that screen must also be
reachable from `./go`, in text.

Read in this checkout: ADRs 0036, 0038, 0047, 0048, 0052, 0054, 0059,
0063 and 0068; `core/tower.py` (`task()`, `RUN_KEYS`, how `tasks[]` is
appended); `core/steps/{assign,rework,ci,review}.sh`; `core/crew.sh`;
`adapters/network/run.sh`; `adapters/replay/run.sh`; the Makefile's
`gates` recipe; `core/logbook.py`; `core/logbook/1.2.schema.json`;
`console/index.html` (`route`, `noteMoves`, the `tower-machine` table);
`ci/shell-boundary.sh`; `ci/board.sh`; `ci/roles.sh`; and the headers of
`ci/logbook.sh`, `ci/console-ui.py` and `site/render.py`.

What the logbook already has, and what it lacks:

- **Already there, per ticket:** every attempt's role, harness, model and
  cost (`tasks[]`); every rejection (`rework[]`); the last attempt's gate
  verdict (`ci`); the review; the canary stages and the human answer
  (`releases[]`); the history with `at`; the perimeter and its ADRs.
  Who holds which files is in `perimeters_held`.
- **A network attempt's workers.** `adapters/network/run.sh` writes
  `members` into `run.json`, but `task()` drops them. A member replayed
  from the hand-written fixtures of ticket 0012 says
  `recorded.hand_written: true`.
- **The patch.** `OUT/patch` sits beside `run.json`, and `crew.sh`
  deletes it on the next attempt. On a rework its base is the judged head,
  so the patch holds that attempt's own changes.
  - Measured on 2026-11-11 by the session that asked for this ADR: the
    largest recorded patch is `fixtures/0001/dev-worker/patch`, 5,244
    bytes. Ticket 0012's members' patches are 2,554 and 2,316 bytes.
  - Read on 2026-11-11 on Firestore's quotas page: the largest document is
    1 MiB (1,048,576 bytes).
- **Each gate's line.** `make gates` writes each gate's line, in the
  order run, to `$GATES_OUT/lines`, and stops at the first NO-GO. The
  Action's summary already reads that file (ADR 0057 §6). `ci.sh`
  records only the summary line.
- **The shell ratchet.** `ci/shell-boundary.sh` lists `core/steps/ci.sh`
  at 85 code lines and `rework.sh` at 51. Neither may rise.
- **No CLI prints the page's sentences today.** The now line exists only
  in the page.

## Decision

1. **Logbook 1.3: optional additions only** (ADR 0047 §2), in
   `core/logbook/1.3.schema.json`. A field that was never recorded is
   `null`. A source that exists but cannot be read is `{ok: false,
   error}`. Nothing is defaulted.

   **Sources, in the tower document:**
   - **`core/tower.py task [--patch] <run.json>`.** `assign.sh` and
     `rework.sh` pass `--patch`; `review.sh` does not, so a reviewer's run
     never stores a patch.
     - **`members`**, present only when `run.json` has them:
       `[{id, perimeter, hand_written, …task(member.run)}]`. Each member
       goes through the same `task()` rule. `hand_written` is `true` when
       the run or its `recorded` says so, and `null` otherwise.
     - **`patch`**: `{text, bytes, truncated}` from the `patch` file
       beside `run.json`. `text` is cut at the last newline at or before
       65,536 bytes.
     - **`files`**: `[{path, added, removed, member}]`, counted on the
       whole patch, not the cut text.
       - A binary file has `added` and `removed` `null`.
       - `member` is the member whose perimeter globs match the path
         (`*` crosses `/`, as in `core/perimeter.sh`). It is `null` for a
         single worker. ADR 0036 §4 makes the match unique.
     - With no patch file, `patch` and `files` are `null`.
   - **`core/steps/ci.sh`** records `ci.lines`: the run's
     `$GATES_OUT/lines`, in order. A perimeter NO-GO records its one line.
     With no file, `lines` is `null`.
   - **`core/steps/rework.sh`**: every new `rework[]` entry copies
     `lines: .ci.lines`, whichever judge rejected the attempt. When the
     reviewer or main's check rejects it, the gates' GO lines are kept.
   - **No listed shell count rises** (ADR 0052 §4). If a line is needed,
     the document's assembly moves into `core/tower.py` and the entry
     goes down.

   **What the reader adds** (`core/logbook.py`, schema
   `lights-out/logbook/1.3`):
   - **`tickets[].tasks[]`** gains `members`, `patch` and `files`, passed
     through.
     - A patch is shown only if its attempt's `secrets` gate said GO.
       Otherwise `patch` is `{ok: false, error: "withheld: the secrets
       gate did not say GO on attempt <n>"}`. This covers an attempt still
       in flight, and one stopped before `secrets` ran.
     - Without this rule, a key a worker pasted would reach a public
       snapshot and the site's video.
   - **`tickets[].ci` and every `rework[]` entry** gain `gates: [{gate,
     verdict, line}]`. They are parsed from `lines` by the gate contract's
     shape (CLAUDE.md), and `line` is kept verbatim.
     - A line that does not match the shape gives `gate` and `verdict`
       `null`.
     - With no `lines`, `gates` is `null`. The raw `lines` are not
       exposed.
   - **`tickets[].files`**: `[{path, glob, member, attempts, tests, adrs,
     held_by}]`, one row per path any attempt touched.
     - `glob` is the first perimeter glob that matches the path.
     - `member` comes from the latest attempt that touched the path, and
       `attempts` lists the attempts that touched it.
     - `tests` lists the project map's `tests` edges into the path (ADR
       0054). It is `[]` when the map knows the file and no test covers
       it, and `null` when the map does not know the file or is unread.
     - `adrs` lists the `adrs.items` that cite the path. It is `null` when
       the map is unread.
     - `held_by` lists the holding `perimeters_held` entries whose globs
       match the path. It is `null` when that source is unread.
     - The reason for a `null` is the top-level error, which the views
       quote.
   - **Nothing new at the top level.**

2. **The screen, `#network`.** It is a nav entry, and its view is in the
   link: `#network[/<id>][?attempt=<n>&replay=1]` (ADR 0059 §1; keys in
   this order, defaults left out).
   - **The ticket shown:** the one in the link. With no id, it is the
     ticket with the newest `updated_at` (ties go to the highest id), and
     the screen follows it on every refresh.
   - **Choosing another ticket:** the `#net-ticket` select writes the
     link.
   - **`attempt`** picks the attempt the graph lights and whose patch is
     open. The default is the latest.
   - **Layout:** the graph and the stream sit side by side, with the
     files below. Below 720 px they stack (ADR 0063's breakpoint, so no
     new width).
   - **No new library, canvas, build, dependency or network asset.** The
     graph is inline SVG with fixed slots. The network refuses a fifth
     member (ADR 0036, `max_members=4`), so there are four worker slots.

   **The DOM contract the tests hold:**
   - **The screen:** `section#net[data-ticket][data-schema][data-replay=off|on|done]`.
   - **The tower:** `#net-tower`.
   - **The six roles:** `.net-role[data-role=<roles/ dir>][data-ran=true|false]`.
     `data-ran` is true only for a role the ticket's record names:
     `tasks[].role`, `review.role`, or `product` when
     `product.consulted` is true.
   - **The workers:** `.net-worker[data-worker][data-harness][data-model][data-replayed][data-hand-written]`.
     A network attempt draws its members; any other attempt draws its one
     task. `data-model=""` means no model.
   - **The gates:**
     `.net-gate[data-gate][data-verdict=GO|NO-GO|not-run][data-attempt]`.
     First come the attempt's `gates`, in the order run. Then come the
     table's gates (§5) that are absent from them, as `not-run`.
   - **The reviewer's edge:** `#net-review[data-verdict]`.
   - **The rework loop:** `#net-rework[data-count][data-max]`, back to
     the dev-worker. It is drawn only when `rework[]` is not empty.
     `data-max` is the logbook's `REWORK_MAX_ATTEMPTS`; when that is
     unread, it is left out.
   - **The canary stages:** `.net-stage[data-pct][data-verdict]`.
   - **The human gate:** `#net-human[data-verdict=GO|NO-GO|waiting|not-reached]`,
     with who answered and through what.
   - **The stream:** `ol#net-stream > li.net-attempt[data-attempt]`,
     newest first. Each attempt has:
     - its workers;
     - `li.net-line[data-gate][data-verdict]`, each holding the line
       verbatim;
     - `details.net-patch[data-truncated] > pre`, holding the patch text,
       or the reason it is withheld.
   - **The files:** `tr.net-file[data-path][data-glob][data-member][data-held-by]`,
     with `.net-tests` and `.net-adrs` cells (each ADR links to
     `#adr/NNNN`). They are grouped by perimeter glob; a glob that no file
     matched says so.
   - **`.net-fact`:** every sentence or value the screen shows carries
     this class (§5).
   - **No `draggable` and no POST.** A node moves only when the logbook
     does.

3. **Live, and at L0 with no key.**
   - **Live:** a node lights up when the logbook moves it. On each new
     logbook (ADR 0047 §4's 2 s poll, behind the ETag), the page compares
     the shown ticket with the version it last drew, as `noteMoves`
     does:
     - new history entries mark the node or edge they touch;
     - a new attempt adds an entry to the stream;
     - each change is announced in `#announce`.
     - The mark lasts 4 s (ADR 0059 §1). With `prefers-reduced-motion`
       the mark stays and the motion goes.
   - **What is honestly live:** a patch and its gate lines reach the
     logbook when the attempt's step ends. The stream is per attempt,
     not per keystroke.
   - **At L0:** a live run under `./go demo` is the replay harness
     really moving the tower.
   - **The replay**, for a snapshot, the site, or anyone who presses
     `#net-replay`:
     - **What it shows:** the final drawing, revealed one timeline row at
       a time, in the logbook's row order. That order is history order,
       so it is `at` order (ADR 0055 §6). Each row reveals the element it
       names, and the rework count counts only the rows revealed so far.
     - **The beat:** one row per 2 s. Provenance, decision: one live
       poll, so the 400 ms flight and the 4 s mark keep their meaning.
     - **The label:** `#net-replay-label` reads "Replay of the recorded
       logbook: nothing is running". It is visible for the whole replay.
     - **What it does not do:** it sends no request. Refreshes are held
       until it ends.

4. **The CLI: `./go network [TICKET=<id>] [ATTEMPT=<n>]`.**
   - **Where it lives:** `core/network.py`, standard library only. It
     reads one logbook file and nothing else.
   - **The recipe:** it runs `core/logbook.sh` with the reader's line
     sent to stderr, then the view.
     - `NETWORK_LOGBOOK=<file>` reads that file and runs no reader (the
       snapshot's counterpart).
     - The default ticket and attempt follow §2's rules.
   - **Stdout:** the view, in this order:
     1. the graph's state: tower, roles, workers, each gate's verdict for
        the attempt, review, rework loop, stages, human gate;
     2. the stream: attempts newest first, each with its files (+/−) and
        its gate lines;
     3. the chosen attempt's patch, verbatim, between two marker lines;
     4. the files by perimeter.
   - **It is a view, not a gate.** The one-line contract binds `ci/`,
     `gates/` and `verify/`, and a NO-GO in the record is a fact, not a
     failure of the command.
     - Never any colour, so `NO_COLOR` is met by construction.
     - Equal logbooks and arguments give equal bytes. It reads no clock,
       so it shows no ages.
     - Exit 0 when it printed the view.
     - Exit 1, with one stderr line and empty stdout, when the logbook is
       absent, not JSON, or not major 1, or when the reader failed.
     - Exit 2, likewise, for a `TICKET` or `ATTEMPT` the logbook does not
       hold.
   - **No follow mode.** The simple fallback still works: run it again.
     During a run, `./go demo` already streams the `[tower]` lines.
     - A follow loop would be a second poller beside `console/serve.py`.
     - Redrawing a terminal needs ANSI codes.
     - Alternative kept: `FOLLOW=1` appends the new history rows every
       2 s (ADR 0047 §4's interval), and never redraws.

5. **One source for the words.** `core/network.py` holds `TABLE =
   {roles, gates, words}`, and `python3 core/network.py --table` prints
   it.
   - **The page embeds a copy:** `<script type="application/json"
     id="network-words">`, checked as `tower-machine` is (ADR 0063 §2).
   - **`roles`** are the six `roles/*/role.env` directories.
   - **`gates`** are the Makefile's `GATES :=` line (the `ci/board.sh`
     precedent).
   - **`words`** holds every sentence either view prints that is not a
     logbook value printed verbatim. Examples: "not run: an earlier gate
     said NO-GO", "attempt {n} of {max}, sent back by the {judge}",
     "hand-written, no model ran", "withheld: …".
   - **A fact has the same text in both views.** Each `.net-fact` holds
     exactly the text of one line of the CLI's output, after its
     indentation. The patch is compared as a block.
   - **The network screen shows no now line.** The card already carries
     it (ADR 0063), so the CLI needs no copy of its rules.

6. **Older logbooks degrade without an error.**
   - **A 1.2 logbook:**
     - each attempt draws one worker from `tasks[]`;
     - the gates are one summary edge,
       `.net-gate[data-gate=gates]`, with `ci.verdict` and `ci.line` (or
       the rework entry's `line`);
     - the stream and the files say "this logbook (1.2) records no
       patches or gate lines";
     - the perimeter, its holders and its ADRs still show.

     Per-gate edges inferred from `ci.failed` and today's order would
     draw history the record does not hold.
   - **A 1.3 logbook with a source unread:**
     - an unread map or `perimeters_held` shows the top-level error where
       `tests`, `adrs` or `held_by` would be;
     - a withheld patch shows its reason.

7. **The site.** `site/render.py` opens the console in snapshot mode
   over `fixtures/logbook/golden-1.3.json`, at `#network/<the golden's
   network ticket>?replay=1`.
   - It records until `data-replay=done`, and writes `media/network.mp4`
     and `network.jpg`. Neither enters git (ADR 0048).
   - Every frame is captioned "Replay of a recorded logbook · nothing is
     running".
   - The render stops if the page's replay label is not visible, or if
     that ticket has no `members`.
   - **The clip's length** is that ticket's timeline rows × 2 s,
     measured at render time, not fixed. The frame is render.py's
     existing 1600×900.

8. **Proof**, written from this ADR, over `golden-1.3.json`.
   - **The golden:** `golden-1.2.json` becomes `golden-1.3.json`, the
     ADR 0059 precedent. It is produced by the reader over
     `fixtures/logbook/stub/`, extended with:
     - a network ticket (0010) of two members, one of them hand-written,
       whose first attempt the mutation gate says NO-GO, reworked, then GO
       on all eight gates, approved by the reviewer, held at 25% and
       answered GO by a person;
     - an attempt still in flight whose patch is withheld (0004);
     - ticket 0001's rollback, and 0002's human GO, as before;
     - a stub-map test file covering a perimeter file.

     A patch over the cap and an attempt rejected at `perimeter` are
     proved on logbooks derived from the golden (`ci/console-ui.py`) and
     in the unit tests, so the golden carries no 64 KiB of filler.
   - **`core/tests/test_tower.py`:**
     - `members` follow `task()`'s rule, with `hand_written` set from
       `recorded`;
     - with `--patch`: the text under and over the cap (cut at a
       newline, with `bytes` the whole size); `files` counted on the
       whole patch; a binary file's counts `null`; `member` by glob; no
       patch gives `null`;
     - without `--patch`, no patch keys.
   - **The suite that drives step-ci and step-rework today** (the
     gate-writer names it):
     - `ci.lines` equals `$GATES_OUT/lines` on a GO and on a NO-GO;
     - a perimeter NO-GO records its one line;
     - a rework entry's `lines` equal the rejected attempt's, for each of
       the gates, review and main judges;
     - the counts in `ci/shell-boundary.sh` do not rise.
   - **`core/tests/test_logbook.py`:**
     - the `gates` parse: `GO: x`, `GO: x — d`, `NO-GO: x — r`, and a
       line off the contract;
     - the pass-through, and `null` when a field is absent;
     - the withholding rule: secrets GO, NO-GO, not run, and in flight;
     - `files`: the union of attempts, `glob`, `member`, `attempts`;
       `tests` `[]` versus `null` by the map; `adrs`; `held_by`; an
       unread source.
   - **`core/tests/test_network.py`:**
     - over the golden: the default ticket and attempt; `TICKET` and
       `ATTEMPT`;
     - exit 1 on an absent file, a file that is not JSON, and major 2;
       exit 2 on an unknown ticket or attempt, with empty stdout;
     - byte-equal output twice, and no ESC byte;
     - every non-verbatim sentence comes from `TABLE.words`: a copy with
       one template changed changes the output;
     - a 1.2 logbook derived from the golden prints the summary edge and
       the "no patches" sentence.
   - **`ci/logbook.sh`, cold:**
     - the golden is byte-equal over the stubs and validates against
       1.0–1.3;
     - `#network-words` equals `--table`, its `roles` equal `roles/`, and
       its `gates` equal the Makefile's `GATES`;
     - `make network NETWORK_LOGBOOK=<golden>` prints on stdout exactly
       what `core/network.py` prints;
     - the page still names four POST routes, fetches `/v1/` only, and
       has no `draggable`.
   - **`ci/console-ui.py`:**
     - the route, the default ticket, and the select writing the link; a
       link opened anew draws the same screen;
     - the contract of §2 for each golden ticket: role nodes; workers
       with harness, model and hand-written; gate edges in run order,
       then `not-run`; the rework count and max; stages with the
       rollback; the human gate's GO and who gave it;
     - the stream: lines verbatim, and the patch equal to the logbook's
       or its reason;
     - the file rows equal the logbook's;
     - the set of `.net-fact` texts equals the CLI's fact lines for each
       golden ticket (Python runs in the image);
     - the replay: the label is visible, rows are revealed in order on
       the 2 s beat (on Playwright's clock), the final frame equals the
       static drawing, and no request is sent;
     - live, simulated by intercepting `/v1/logbook.json` (the test says
       so): the mark, the announcement, and no motion when reduced
       motion is set;
     - the 1.2 degradation;
     - no POST, no drag;
     - ADR 0068 §6's contrast and focus, in both themes;
     - every earlier case still green.

## Alternatives rejected

- **A seventh "orchestrator" node or role.** The tower already
  orchestrates, and it is the centre node, not a role. `ci/roles.sh`
  holds six.
- **A live keystroke stream from `.crew/`.** The console would read
  `.crew/` (ADR 0047 §6, refused by `ci/logbook.sh`). Workspaces are
  wiped per attempt and, at L2, live on job machines. And a keystroke is
  not what was judged.
- **A second route, `/v1/network.json`.** The snapshot and the site would
  lose it (ADR 0059 §3).
- **A graph library, or canvas.** A library is a dependency. Canvas has no
  DOM for tests or screen readers. Six roles, four workers and nine gate
  edges (eight gates plus a perimeter NO-GO) fit fixed SVG slots.
- **The console reading git or the code host for patches.** L0's Gitea
  is gone after `down`, and the snapshot and the site have no code host.
- **Antigravity's free-form agent map.** lights-out's network is declared
  in the ticket: at most four members, disjoint perimeters (ADR 0036). A
  free-form map would draw a freedom the harness refuses.
- **Sentences rendered into the logbook** (ADR 0063's alternative): the
  reader would own presentation, and logbooks already collected would
  have none.
- **Patches archived under `.crew/` for the reader.** At L2 the tower
  writes elsewhere, and one attempt would have two sources. Kept as the
  fallback if the document budget bites.
- **Each member's own patch stored.** Attribution by perimeter is exact,
  because no path is changed by two members.
- **The declared gate order in the logbook.** The reader would parse the
  Makefile. The checked table gives the order to every 1.x logbook.

## Consequences

- **The one that hurts:**
  - **Patches now live in the tower document and the logbook.** The
    secrets rule keeps them out of what is shown. The StateStore still
    holds every attempt's patch, as the code host's branch already does,
    since crew pushes before the gates.
  - **The document grows.** Each attempt's patch is held twice, in
    `task` and in `tasks[]`. At the cap that is (4 + 1) × 64 KiB =
    320 KiB, under a third of Firestore's 1 MiB. The logbook grows with
    it. The ETag keeps an unchanged logbook off the poll, but every
    change re-sends the whole file.
- **The CLI's text becomes a contract.** Changing a sentence means
  editing the table and both views, and the tests follow.
- **Release and ops-triage stay dark per ticket.** The logbook ties no
  brief or post mortem to a role for a ticket.
- **A replay frame is the final record, partly revealed.** It never shows
  a value the record does not hold, and it never shows an earlier value
  that was later overwritten.
- **Two files are added:** `core/logbook/1.3.schema.json` and
  `core/network.py`. `site.yml` and `render.py` follow the golden's new
  name.

## Golden-rule check

`./go demo` opens no console and runs no `./go network`. Its steps read
two files they already produce (the patch and the gate lines) into the
document they already write. It gains no stage, image, key or
dependency. The snapshot, the replay and the site need no plane and no
account. Passes.

## Open for the owner

The owner was not available, so each item carries a provisional decision
(2026-11-11, owner to confirm). Overturning one means only the change
named.

1. **Where patches are stored.** Provisional: in the tower document,
   capped. Alternative kept: an archive under `.crew/` read by the
   reader.
2. **The cap**, 65,536 bytes. Provenance, decision: about 12.5 times the
   largest recorded patch (5,244 bytes, measured), and 5 copies fit in
   under a third of Firestore's 1 MiB (external). Alternative kept:
   16,384 bytes (about 3 times the largest), if the logbook's size bites
   before a real patch does.
3. **Withholding a patch until secrets says GO.** Provisional: yes.
   Alternative kept: show every patch, as the code host does.
4. **The replay starts only on request**, even in snapshot mode, so a
   snapshot never looks live. Alternative kept: it starts by itself in
   snapshot mode.
5. **No follow mode in the CLI** (§4). Alternative kept: `FOLLOW=1`.
6. **The default ticket is the newest updated.** Alternative kept: the
   first ticket in the "your turn" list.
7. **Release and ops-triage stay dark.** Alternative kept: logbook 1.4
   ties the brief's author role and a post mortem to the ticket.

## Decided at build (2026-11-11)

Readings the tests and the code had to agree on, each where the text above
left room. None adds a field or a number without provenance.

1. **The graph lights the attempt shown, the review included.** An earlier
   attempt's `#net-review` is `changes-requested` when its rework entry's
   judge is the reviewer, otherwise `not-reached`; only the latest attempt
   shows the record's `review`.
2. **A judge recorded with no gate lines** (a 1.3 logbook whose `ci` or
   rework entry predates `lines`) draws the one summary edge of §6, not
   eight `not-run` edges: those would draw history the record does not
   hold. A rework entry the reviewer or main's check wrote followed a gates
   GO, so its summary edge is GO.
3. **A ticket with no attempt yet** still draws every gate, `not run yet`.
4. **The canary stages and the human answer** are read from `releases[]`
   (the entry of that ticket and that release version), not from the
   timeline rows that copy them; the human node names who answered and
   through what.
5. **A ci entry vouches for the latest attempt only when its `head_sha` is
   the PR's head.** Otherwise that attempt has no gates and its patch is
   withheld.
6. **The heading names a ticket as the console does** (ADR 0063's
   `named`): a title that already starts with its id is not prefixed twice.
7. **The 1.2 sentence names the minor** ("this logbook (1.2) …") and is said
   in the stream and in the files.
8. **The replay** dims the graph's nodes until their row and draws a fact
   only once its row is revealed; the rework count counts the rework rows
   revealed so far. `?replay=1` starts it as the screen is drawn.
9. **The live mark lasts 4 s with or without reduced motion**; reduced
   motion removes the pulse, not the mark.
10. **The patch's files** are counted hunk by hunk up to the next header;
    format-patch's `-- ` signature is told from a removed line by the git
    version line that follows it.
11. **Below 720 px the graph keeps a 620 px minimum width and scrolls in
    its panel.** Provenance, decision: about 80% of the 760-unit drawing,
    chosen by eye on the golden's ticket 0010 at 390 px.
12. **A replay's graph draws the attempt of the last row revealed**, so the
    attempt the gates sent back lights its NO-GO before the next one runs;
    the facts beside it are still the final drawing's, revealed row by row.
    The rework loop starts at the gate that said NO-GO on the attempt drawn.
