# ADR 0054 — The project map: a SQLite file rebuilt from one commit at every merge, a docs-drift check, a docs ticket after a merge, and the map's slice in the worker's brief behind a switch

- Status: accepted
- Date: 2026-11-07
- Milestone: M28

## Context

Agents learn a repository with grep and the workload's `worker.md`. Nothing
tells them which ADRs govern a path, which tests cover it, which tickets
touched it before, or which pages describe it. Meanwhile documentation
drifts at every merge, and the one check against it, `ci/readme.sh`, looks
at the `./go` and `make` words of `README.md` only.

Measured on 105dea2 (this repository, 2026-11-07): 548 tracked files that
are not tests, pages, ADRs or tickets; 18 tests; 51 ADRs; 45 pages; 13
tickets; 612 Python and JS symbols. Building everything below from that
commit took 0.5 s on a laptop and wrote a 416 KiB file. The citation rules
of §3, run over the pages, found three things that name nothing:
`docs/ARCHITECTURE.md` described ADR 0028's cloud image
(`workbench/cloud/Containerfile`, `WORKBENCH_FLAVOUR`), which is not in the
tree, and linked ADR 0020 through a path relative to the wrong directory.

## Decision

1. **The map is one SQLite file** (`sqlite3`, standard library), never a
   service: no port, no daemon, no new dependency. `core/map.py`, Python
   under `core/` (ADR 0052). Schema `1`, four tables:
   - `meta(key, value)`: `schema`, `sha`, `tickets`, `exclude`.
   - `node(id, kind, name, title)`, unique on `(kind, name)`. Kinds:
     - `adr`: a Markdown file in a directory named `adr` or `adrs` whose
       name starts with four digits;
     - `ticket`: `<tickets>/<digits>.md`, named by its id, titled by its
       first `# ` line; `<tickets>` is `tickets` for this checkout and
       `.lights-out/tickets` for an attached repository (ADR 0037 §2);
     - `page`: any other Markdown file not under a `fixtures` directory;
     - `test`: `test_*.py`, `*_test.py`, `*.test.js`, `*.spec.js` (and
       `.mjs`, `.cjs`);
     - `file`: every other tracked path;
     - `symbol`: `<path>::<name>`, a top-level function or class, or a
       class's method as `Class.method`;
     - `feature`: a ticket's `feature:<name>` label on its `- Labels:`
       line. Features are declared by tickets; the map never guesses one;
     - `command` and `key`: what pages and ADRs cite (§3).
   - `edge(src, dst, kind)`:
     - `defines`: file or test → symbol. Python through `ast`; JS through a
       line parser: `function`, `class`, top-level `const`/`let`/`var`,
       `export`, `exports.<name> =`;
     - `imports`: file → file; `tests`: test → a file it imports that is
       not a test. Python modules resolve from the importer's directory
       up to the root; JS resolves relative specifiers (`./`, `../`) with
       `.js`, `.mjs`, `.cjs`, `/index.js`;
     - `perimeter`: ticket → each tracked path its `## Perimeter` globs
       match (ADR 0021 §5, `fnmatch`: `*` crosses `/`);
     - `history`: ticket → each path still tracked that a commit whose
       message names `ticket/<id>` or `ticket <id>` changed (merges
       against their first parent: what a code host's merge commit says);
     - `declares`: feature → ticket; `cites`: page or ADR → command or key.
   - `cite(src, kind, target, line, ok)`: every citation of a page or an
     ADR, with whether it resolves.

2. **Rebuilt, never updated; stamped with its commit.** `build` reads one
   commit through git's objects (`ls-tree`, `cat-file --batch`), never a
   working tree, and writes a new file that replaces the old one: the same
   commit gives the same rows. `meta.sha` is that commit. Every reader
   opens the map through one function that is given the base it works on
   and refuses, naming both shas and `./go map`, a map built at another
   commit, a missing file, or another schema. A stale map is never read
   without knowing it.
   - `./go map` builds the workload repository's `HEAD` (this checkout, or
     the attached clone) into `MAP_FILE`, `.crew/map.sqlite` by default,
     and prints one line: `GO: map — <sha>, <kind>=<count>… → <file>`, or
     `NO-GO: map — <reason>`. No engine socket, no plane.

3. **Citations, and the drift check.** In a page or an ADR, outside a
   fenced block a citation is: a Markdown link target that is not a URL
   or an anchor, resolved from the page's directory; a backticked token
   with a `/`, no space and none of `< > $ { } = @ | ; … ...` or `://`,
   resolved from the root when its first segment is a top-level
   directory, or from the page's directory when its first segment is
   there. Inside backticks or a fenced block (a shell comment, ` # …`,
   stripped) a citation is also `./go <word>` or `make <word>`, and a
   `NAME=` assignment.
   - A path resolves when it exists, or when it ends a tracked path:
     `.lights-out/worker.md` names a workload's contract file. A glob
     resolves when it matches a tracked path.
   - A command resolves when `<word>` is a target `make help` lists, as
     `ci/readme.sh` read it.
   - A `NAME=` resolves when `NAME` appears in a tracked file that is not
     Markdown: a profile key, a workload key, a make variable, a variable
     a script reads. A key the code no longer spells is drift; one the
     code only names in a comment is not caught, a limit of the rule.
   - **`ci/docs-drift.sh`** replaces `ci/readme.sh` in `./go ci`: one
     line, `GO: docs-drift` or `NO-GO: docs-drift — <n> citation(s) name
     nothing`, each finding (`<page>:<line>: <kind> <target> names
     nothing`) on stderr. It reads the working tree, so it judges what is
     about to be committed; a path the repository's `.gitignore` ignores is
     a path a run writes (`.crew/`, `.env`), not a claim. A contributor's
     global excludes play no part, so every clone gets the same answer. It proves it can fail first, on a
     built tree with a missing path, an unknown target and an unknown key.
   - Its scope is every page except `MAP_EXCLUDE`, set in the `Makefile`:
     `docs/PLAN.md` and `docs/plans/*` (plans describe what does not exist
     yet), `.claude/*` (construction tooling and dated session notes).
     ADRs are cited in the map but not judged: an ADR is a record of a
     decision at its date, changed only by an amendment. Tickets are not
     pages: a ticket names the files it will create. One name outside the
     repository is known by `--known`: `CONTAINER_CONNECTION`, podman's
     own variable (ADR 0051).

4. **After a merge, the merge step rebuilds the map and files a docs
   ticket.** `core/steps/merge.sh`, after the code host merged, runs
   `core/map.py after-merge`: a bare clone of the code host
   (`clone-url.sh`, the credential helper of ADR 0043 §2), `build` at the
   merge sha into `MAP_FILE`, and the paths the merge changed against its
   first parent. The pages and ADRs that cite one of those paths, that the
   merge did not change itself and that `MAP_EXCLUDE` does not name, get
   one ticket through the Tracker's `put` verb (ADR 0044 §6), origin `docs
   merge <sha>`, so a re-run files nothing twice. Its `## Perimeter` lists
   those documents only: the code is merged and judged, the ticket is for
   the `dev-worker` through `./go assign`, like any ticket. A citation
   covers a path when it is that path, or a directory of two segments or
   more above it: `core/` alone is cited by 33 pages and ADRs (measured on
   105dea2), so a top-level directory names the layout, not what governs
   a file. The document records `map: {sha, file, docs_ticket}`; no
   ticket is `null`.
   - A failed rebuild fails the step: the state stays `ci-passed` and
     `./go resume` runs it again, where the code host's merge adapter does
     not merge twice (ADR 0019 §9 d). A merge that skipped its docs ticket
     silently would lose the only route the drift has back to a worker.
   - `MAP_FILE` empty turns the rebuild off. `ci/tower.sh` empties it: its
     stub code host has no repository to clone.
   - The replays that merge (`e2e`, `demo-rework`, `demo-review`) run
     against a copy of the tickets under `.crew/replay-tickets/`, as
     `demo-postmortem` does (ADR 0044 §7): a docs ticket written into
     `tickets/` would leave an untracked file that takes the owner's next
     ticket number.

5. **The slice goes into the worker's input document, behind
   `MAP_BRIEF`.** With `MAP_BRIEF=on`, `core/crew.sh` asks the map, at the
   base it just cloned, for the paths the ticket's perimeter matches, and
   puts the answer in `input.md` after the workload's brief and before a
   rework verdict (ADR 0031 §2, ADR 0038 §4): the files (a glob that
   matches nothing yet is listed as such), the ADRs and the pages that
   cite them, the tests that cover them, the tickets that touched them
   (`perimeter`, `history`), and their symbols. Every list is sorted;
   empty ones say `none`. A missing or stale map is a NO-GO of `assign`
   that names both shas and `./go map`; any value but `on` or `off` is a
   NO-GO naming the key. Every harness reads `input.md`, so the slice
   needs nothing per harness.
   - **`MAP_BRIEF=off` in all four profiles.** At L0 the worker replays a
     recorded PR: replay keys on the ticket and the role, not on
     `input.md`, so the slice would not break a replay, but it would
     change nothing a replay does, and the first `assign` of a demo has no
     map at its base. At L1 and above, whether the slice helps is what M24
     measures, half the tickets with it and half without, comparing turns,
     tokens and rework attempts in `run.json`. The default changes on
     M24's numbers, not before. A run turns it on with `./go assign
     TICKET=<id> MAP_BRIEF=on`, or `.env`.

6. **What does not change.** Six roles (`ci/roles.sh`): the docs ticket is
   the `dev-worker`'s. No MCP server. No model anywhere in the map: same
   commit, same map. `core/map.py` makes the second process call of
   `core/` besides `tower.py`'s `sh()`, because `sh()` runs scripts and
   the map runs `git`; it is excused for bandit on its one line with that
   reason, and the Tracker's `put` verb goes through `sh()`.
   `core/crew.sh` stays within its debt entry of ADR 0052 §6: the input
   document is assembled in fewer lines than before.

7. **Tests from this ADR**: `core/tests/test_map.py` (unit, in
   `ci/core-tests.sh` and the self gate's mutation), `ci/map.sh` (the CLI
   end to end: this repository, `workloads/minimal` as a repository of
   its own, a stale map refused, a docs ticket filed once), and
   `ci/docs-drift.sh` itself.

## Alternatives rejected

- **A graph database as a service.** A daemon and a port in the plane, a
  client library in the workbench, and one more thing that must be up
  before `./go demo`, against the golden rule. The questions are joins over
  a few thousand rows; SQLite answers them from a file, in the standard
  library.
- **An MCP server now.** A long-running process per run and a protocol
  each harness speaks differently, to answer a question M24 has not asked
  yet: whether agents need to query the map mid-task at all. The brief
  works in every harness and replays at L0. A read-only server comes after
  M24, and only if its numbers ask for it.
- **Incremental updates.** A map that patches itself can drift from the
  commit it claims to describe, which is the failure it exists to catch.
  A rebuild of this repository costs 0.5 s (measured above); incremental
  updates would buy nothing and lose "same commit, same map".
- **A model to find features and links.** Not deterministic, not
  replayable, a cost at every merge. Features are declared by tickets.
- **A seventh role that keeps documentation.** Six roles is a scope
  constraint; the `dev-worker` takes a docs ticket like any other.
- **Rewriting pages automatically.** No rewrite without a gate: the docs
  ticket goes through `assign`, the gates and review like any change.

## Consequences

- A merge that touches cited paths opens a ticket every time. On this
  repository that is most merges under `app/`, `core/` or `adapters/`:
  the drift becomes visible work instead of silent debt.
- `ci/readme.sh` is gone; `ci/docs-drift.sh` checks the same words in
  every page, and more.
- The map knows nothing a commit does not hold: a ticket only in a
  Tracker, not committed, has no node.

## Amendment 2026-11-09: the landing page is a page too

The landing page, `site/index.html`, is HTML, so the map's citations,
which read Markdown, never saw it. On 2026-11-09, the day 0.3.0 was
published, it still named v0.2.0 in its header, told visitors to download
the 0.2.0 wrapper and image, and linked a release `v0.2.0` that was never
published: nothing failed. `ci/docs-drift.sh` now reads it too, with three
rules of its own, after the map's:

- **The release it names is the wrapper's.** Every `v<major>.<minor>.<patch>`
  on the page, and the tag of every `ghcr.io/nicolaguglielmi/<image>:<version>`,
  equals the `version=` line of `lights-out`, the one source of the version
  `package.yml` checks a tag against (ADR 0045 §7). The wrapper's version
  therefore moves in the release commit, with the page, right before the
  tag (`docs/launch/checklist.md`).
- **Every link into this repository at `main` names a path that exists**:
  `https://github.com/nicolaguglielmi/lights-out/blob/main/<path>` and
  `…/tree/main/<path>`, the fragment and query left out.
- **Every `./go <target>` names a target**, as in a Markdown page.

The rules live in the self-check, not in `core/map.py`: they know this
repository's page, wrapper and image names, which the product must not
(`ci/app-free.sh`). Like the map's rule, the check first proves it can
fail on a planted page. What it cannot read is whether the prose still
says what the product does: that stays a step of the milestone skill
(`.claude/skills/milestone/SKILL.md`), and the console's stills are
rendered again by `site.yml` on every push that changes the console.

Rejected: comparing the page with the latest `v*` tag, because a clone
without tags (a fork, an archive) would fail for want of history, not of
alignment; and a rule in `core/map.py`, for the reason above.
