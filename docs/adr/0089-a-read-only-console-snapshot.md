# ADR 0089 — A read-only console snapshot: the console's own page with the logbook inlined, one file, nothing behind it

- Status: accepted (M56)
- Date: 2026-11-14
- Milestone: M56 (docs/PLAN.md, "Sprint 2026-11-14 — Il flusso a colpo d'occhio (M52–M56)", stream B)

## Context

To see the console today, a visitor needs a plane: clone, run `./go
demo`, then `./go console`. `CONSOLE_SNAPSHOT=<logbook>` (ADR 0047 §4)
removes the plane, but a server is still needed, because the page reads
`/v1/console.json` and `/v1/logbook.json` with `fetch`. The site shows
stills of the console's screens, taken from a synthetic golden logbook
(`site/render.py`). It shows no real run that a visitor can click
through.

Other projects publish read-only static snapshots of their dashboard.
The owner wants that for the console: a real run, frozen into one file,
in which a visitor sees a canary roll back, with nothing to clone.

This sprint, stream A adds three panels to `console/index.html`
(ADRs 0085–0087). Stream B must not depend on them and must not list
them.

## Decision

1. **`core/freeze.py`**, standard library only, takes the console's page
   (`console/index.html`, unchanged) and a logbook file. It writes one
   HTML file, which is the page with three things added:
   - **The data, inlined.** It adds two `<script type="application/json">`
     elements: `#frozen-logbook`, the logbook's bytes as JSON, and
     `#frozen-console`, a `console.json` answer:

     ```
     {mode: "snapshot", auth: "public", identity: null,
      can_launch: false, reason, read_at, read_error: null, snapshot: <label>}
     ```

     Every `<` in either is written `<`, so no value can close the
     script element.
   - **A `fetch` of its own, before the page's first script.**
     - A `GET` of `/v1/logbook.json` answers 200 with the inlined
       logbook and an `ETag`.
     - A `GET` of `/v1/console.json` answers 200 with the inlined
       answer.
     - Any other request, every POST included, answers 403 with
       `{"error": "a frozen snapshot: nothing behind it, nothing is
       sent"}` and leaves the file.
   - **A banner** as the body's first element. It says that this is a
     frozen snapshot, gives the label, and says it is read-only.

   It also adds a `Content-Security-Policy` meta (`default-src 'none'`,
   with inline scripts and styles and `data:` images and fonts), so the
   browser itself refuses any request. Each insertion sits between
   `<!-- frozen: begin -->` and `<!-- frozen: end -->`. Nothing else in
   the page is touched.

2. **Controls disabled, with no list of panels.** The console already
   disables its writes in snapshot mode: go-for-launch with `can_launch:
   false`, the answer box with `mode: "snapshot"`, and save with `auth:
   "public"`. The frozen file declares all three. A control the console
   adds later and forgets to disable still sends nothing, because §1's
   `fetch` refuses it and the CSP blocks any other path. The freezer
   never reads the page's screens. Stream A's panels, and any later
   one, are frozen as the page renders them from the logbook, and a
   logbook older than a panel shows what that panel says for an older
   logbook.

3. **The label** names the run. By default it is `logbook of <commit>,
   last event <at>`: the logbook's `source.commit`, and the latest `at`
   among its tickets' history, its releases and their answers, the same
   reference ADR 0082's amendment takes. The freezer has no clock, so
   equal inputs give equal bytes. `--label` replaces the label, for
   example to name the command that produced the run.

4. **The interface.**

   ```
   python3 core/freeze.py --logbook <file> --out <file> [--page console/index.html] [--label <text>]
   ```

   - On success it prints the out path and exits 0.
   - It refuses, with one line on stdout and exit 1, when any of these
     holds:
     - the logbook is missing, or is not JSON;
     - the logbook's `schema` does not start with `lights-out/logbook/`;
     - the page has no `<head>` or no `<body>`.

5. **In the evidence pack (ADR 0081).**
   - `make evidence` writes the logbook first, with its line on stderr.
     A logbook that cannot be written does not stop the pack.
   - `core/evidence.sh` then, after the pack is signed and verified,
     freezes the console from `EVIDENCE_LOGBOOK` (default
     `LOGBOOK_FILE`, then `.crew/logbook.json`) into
     `EVIDENCE_DIR/<v>.console.html`. The
     label is `evidence pack <v>, <the default label>`.
   - It signs and verifies that file as it does the pack, into
     `<v>.console.html.bundle`.
   - The GO line gains `; console snapshot signed`, or `; no console
     snapshot: <why>` when there is no logbook or the freeze refused.
     The pack's GO does not depend on the snapshot.
   - A snapshot that was written but does not sign and verify is a
     NO-GO, as a pack that does not is.
   - The pack's JSON does not change, and neither do ADR 0081's tests.
   - The snapshot is the console as it was when the pack was built, not
     when the release ran. A later ticket in the same logbook shows in
     it.

6. **On the site: the latest demo on main.**
   - The ci workflow's `engine-compat` job already runs `./go demo` at
     L0 with no API key. It writes the demo's logbook
     (`.crew/logbook-demo.json`) and uploads it in the
     `console-snapshot` artifact. `./go evidence` runs right after, so
     the same artifact now carries the evidence pack's signed
     `<v>.console.html`.
   - The site workflow runs `site/demo-logbook.py` (standard library,
     `actions: read`). It finds the newest successful `ci` run on `main`
     whose artifact still holds that logbook, and freezes it with the
     console of the commit being built into `_site/console/snapshot.html`.
     The label is prefixed with `./go demo at L0, no API key, ci run
     <id>`, so the page names the run a reader can open.
   - When no such run exists, the site build fails. The landing page
     never links a snapshot that was not built.
   - The landing page links the snapshot. A visitor opens ticket 0001's
     timeline, from the request to the canary's automatic rollback, and
     the launch screen with the rollback's reason.
   - The page is rebuilt from the current console on every site build,
     so a new panel appears there with no new run.
   - A demo's logbook is not committed. A threshold's name in its `key`
     field reads as a secret to gitleaks, and a change cannot excuse its
     own finding (ADR 0049 §4, `site/render.py`). Taking the run from
     the ci artifact keeps the repository free of it and makes the
     snapshot the latest run, not a capture someone has to remember to
     refresh.

## Tests, from this ADR

`core/tests/test_freeze.py`.

### The freeze

On a minimal page (a `<head>` with a script, a `<body>`, a script that
reads `/v1/logbook.json`) and a small logbook:

- One file is written. Its `#frozen-logbook` parses back equal to the
  logbook. Its `#frozen-console` has mode `snapshot`, auth `public` and
  `can_launch` false.
- The shim and both data elements come before the page's first
  `<script>`. The banner is the body's first element. The CSP meta is
  present.
- The page's own text is still there, byte for byte, outside the
  insertions.
- A logbook value holding `</script><script>alert(1)</script>` appears
  only escaped, and still parses back equal.
- The default label is `logbook of c0ffee1, last event
  2026-11-14T10:05:00Z`, from a logbook whose latest time is a release
  answer. `--label x` gives `x`.
- Two freezes of the same inputs are byte-equal.
- The refusals, one line and exit 1 each: a missing logbook, one that
  is not JSON, a schema `other/1`, and a page with no `<body>`.

### The committed console

`console/index.html` frozen with `fixtures/logbook/golden-1.5.json`:

- No `src=` or `href=` to `http(s)://`, and no `<link rel="stylesheet">`.
- The page's every `fetch(` route is one of the two §1 answers, or a
  POST the shim refuses. The test lists them from the page, so a new
  route stays covered.

### The evidence pack

`ci/evidence.sh`:

- With `EVIDENCE_LOGBOOK` set to the golden logbook:
  - the line ends `; console snapshot signed`;
  - `<v>.console.html` and its bundle exist, and the bundle verifies
    with `cosign verify-blob` alone;
  - the file changed by one byte does not verify.
- With no logbook, the line ends `; no console snapshot: ` and a reason.

### In a browser

`site/render.py`, in the site workflow, opens the frozen demo snapshot
in Chromium, served alone, light and dark:

- No request leaves the file, and every request is counted.
- The header says `snapshot`, and ticket 0001's timeline ends in the
  rollback.
- go-for-launch is disabled.
- Screenshots of the overview and ticket 0001 in both themes are
  uploaded with the site review.

## Alternatives rejected

- **Save the rendered DOM** (a headless browser's `outerHTML`). It
  freezes one screen, with no navigation, kanban, network replay or
  theme switch. It needs a browser to produce, and every panel's script
  state is lost.
- **Copy each panel's data into a new page.** That is a second console
  that would drift from the first. It would also hard-code the panels,
  which this ADR must not do.
- **Serve the snapshot from a hosted `console/serve.py` in public
  mode.** That is a server to run and secure. The site is static pages
  only (ADR 0048).
- **Put the snapshot inside the pack's JSON.** The pack's schema would
  grow a 200 KB string, and ADR 0081's tests would change. A sibling
  file with its own bundle is signed the same way and verified with the
  same cosign command.
- **A synthetic logbook for the site.** The golden logbook is a
  fixture. The point here is a real run.
- **Commit a demo's logbook for the site.** gitleaks reads a
  threshold's `key` as a secret, and that finding cannot be excused in
  the same change (§6). It would also go stale, because someone has to
  capture it again.
- **Run the demo in the site workflow.** That is a second plane per site
  build. The ci workflow already runs one and keeps its logbook.

## Consequences

- A visitor clicks through a real L0 run, including the rollback, from
  the landing page, with nothing to install.
- Each evidence pack carries the console as it stood, signed.
- The site's snapshot is as recent as the last full ci run on `main`,
  and its banner names that run. Its age is visible.
- The site build reads one more source, the ci workflow's artifact, and
  fails when no recent run has it.
- `./go demo` does not run the freeze, and its time does not change.

## Amendment 2026-11-17: the run that judged main's commit, on any branch

§6 asked for the newest successful `ci` run on `main` with the artifact.
Since main is fast-forwarded to a commit already judged on its land
branch, `ci` on `main` does not judge it again (ADR 0065), skips
`engine-compat`, and uploads no logbook: the site kept freezing ci run
43501367427 of 2026-11-13 while main moved on. `site/demo-logbook.py` now
walks main's last 30 commits, newest first, and takes the successful `ci`
push runs of each commit on any branch; the first with the artifact gives
the logbook. The banner still names that run, and the page is still built
only from a commit that is on main. Measured on 2026-11-17: it finds ci
run 44141319131, the land branch's run of main's head, 48678aa.
