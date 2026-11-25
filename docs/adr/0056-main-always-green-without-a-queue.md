# ADR 0056 — Main always green without a queue: no re-judge on disjoint perimeters, main checked after merge, bisect and revert

- Status: accepted
- Date: 2026-11-07
- Milestone: M30
- Amends ADR 0019 §1 (the transition table: `merged` requests `mainline`,
  `release` starts from `main-green`), ADR 0038 §2, §4 (a rework the
  tower sends back from main starts from main's head) and ADR 0053 §5
  (a perimeter is held through `merged`). Consistent with
  ADR 0033 (main is judged by the last green main's judges), ADR 0034 (a
  refusal is a recorded failed attempt) and ADR 0043 (the code host is
  touched only through the CodeHost port's verbs).

## Context

`core/steps/merge.sh` merges the head CI judged on whatever main is at
merge time. With several tickets in flight main moves between a PR's
judgment and its merge, the merge commit is a tree no gate has seen, and
nothing checks main afterwards.

The usual answer is a merge queue: re-judge each PR on the current main,
one at a time. The owner decided against it on 2026-11-07: with tickets
in flight multiplying, a global lock is a funnel. Conflicts are prevented
upstream (ADR 0053, M27: open tickets never hold overlapping perimeters)
and cured downstream, by finding the merge that broke main and reverting
it: the canary's principle applied to main.

With ADR 0053's holds, two tickets the tower runs never touch the same
file. Main can still move under a judged PR's perimeter, by a merge the
tower did not make (a person's, another tool's: the norm on an attached
repository) or by a revert. And two disjoint diffs can still break main
together: one renames what the other starts calling.

## Decision

1. **Merge without re-judging when the perimeter is untouched.** Before
   the adapter's `merge.sh`, `core/steps/merge.sh` runs `python3
   core/mainline.py rejudge`. It fetches the code host's default branch
   and lists the first-parent commits after `pr.base_sha` (the base the
   PR was judged on), each with the paths it changed against its first
   parent (`--no-renames`): what each commit *touched*, so a change later
   reverted still counts. The perimeter is the ticket's `## Perimeter`
   from the Tracker, never from the PR, read by ADR 0053's parser and
   matched path by path as `core/perimeter.sh` does (`fnmatch.fnmatchcase`, `*` crosses `/`).
   - Nothing touched the perimeter: the adapter merges onto the moved
     main and no gate runs again; the main check (§3) judges the result.
     Main can still move between this read and the merge: the main check
     covers that too.
   - A commit touched it: no merge. The step prints `ci-failed` and
     `mainline: {verdict: "NO-GO", judge: "base", head_sha, line,
     details}`, line `NO-GO: base — main moved under the perimeter since
     <base>: <path> by <commit>`, one `details` entry per overlap. The
     ticket goes to rework (§6).
   - `pr.base_sha` is not on main (main was rewritten): the step fails
     and nothing is merged.
   - The head CI judged is already on main: a retry after the code host
     merged (ADR 0019 §9). Its own merge is not main moving under it; the
     adapter's idempotent merge answers.

2. **The table.**

   ```
    precondition()                  next()
   +  mainline) 'merged'          -  merged)     'release'
   -  release)  'merged'          +  merged)     'mainline'
   +  release)  'main-green'      +  main-green) 'release'
   ```

   `mainline` may also print `ci-failed`, which requests `rework`. The
   board's copy of the table (ADR 0035) follows.

3. **Main is checked after each merge, in groups of at most four.** The
   `mainline` step (`core/steps/mainline.sh`, a thin entry point for
   `python3 core/mainline.py check`) keeps one document beside the
   tickets: collection `mainline`, id `main`, `{state: "green" | "red",
   green, head, line, reverted: [...], at}`.
   - **The last green main, G**, is the document's `green` while it is on
     main; otherwise (no document, or main rewritten, as the L0 replays
     do) the base the ticket's PR was judged on. That base was never
     judged itself: a red base makes the first merge after it the
     culprit. ADR 0053 §6 checks the base before the first ticket on an
     attached repository; the bundled app's base is this checkout.
   - **Groups.** The first-parent commits after G, oldest first, are
     taken at most `MAINLINE_GROUP_MAX` at a time and the newest of the
     group is judged against G; GO moves G to it, until G is main's head.
     `MAINLINE_GROUP_MAX=4` in `policy/mainline.env`, read from the
     tower's checkout. Provenance: the owner's decision of four
     dev-worker attempts a day (`roles/dev-worker/role.env`), sized
     against the Gemini free tier's 250 requests a day at `MAX_TURNS=60`,
     the source of `REWORK_MAX_ATTEMPTS` (ADR 0038 §3): a group is at most
     one day of that throughput, and bisecting it costs at most
     ⌈log₂ 4⌉ = 2 gate runs. It does not bound merges from outside the
     tower or on other providers; it is a decision to revisit with them.
   - **What judges main.** `make gates GATES="sast test"` in a worktree at
     the commit, the judges restored from G as `core/steps/ci.sh` restores
     a PR's base's (ADR 0033 §1). PLAN M30 says "the workload's gates";
     this narrows it on purpose to the gates whose subject is the whole
     tree. The diff gates (risk, secrets, deps, mutation, ADR 0053's)
     already judged each PR's own diff, a merge of disjoint diffs does not
     change their subject, and the ticket-bound ones have no ticket on
     main.
   - **The same tree is the same verdict**, under the same judges:
     verdicts are memoized by (tree, G). G's own tree is GO; so is the
     tree of the head the ticket's CI passed when G is that PR's base. A
     merge onto an unmoved main has exactly that tree, so `./go demo`'s
     single ticket pays a fetch, not a gate run.
   - **What ships is the green main.** The ticket's document records
     `mainline.green`, the judged commit its merge is in, and
     `core/steps/release.sh` releases that commit. A merge inside a group
     is never judged alone, so its own tree is never released.

4. **Bisect, revert through the code host, rework the culprit.** A
   group's newest commit NO-GO writes the document `red` with the line,
   then bisects the group between G (green) and that commit (red): the
   first red commit is the culprit, in at most ⌈log₂ n⌉ more runs. It is
   reverted with the CodeHost port's existing verbs: a branch
   `revert/<sha>` from main's head with `git revert` (`-m 1` for a merge),
   `push.sh`, `open-pr.sh` with a title and the verdict as body,
   `merge.sh` at that head. A revert, never a force push: main keeps both
   commits, and an outside commit made meanwhile is not lost. Main's new
   head is judged; GO writes the document `green` and appends `{merge,
   revert, line, details, at}` to `reverted`, `line` and `details` being
   the culprit's own verdict.
   - **The ticket of the step.** Its merge in `reverted`: `ci-failed`,
     `mainline: {verdict: "NO-GO", judge: "main", head_sha, line,
     details}`, rework. Otherwise `main-green`. The culprit's ticket
     learns it from its own `mainline` step, queued after its merge; a
     culprit merged outside the tower is reverted and recorded with
     nobody to send it to. A ticket whose merge is not on main fails the
     step.
   - **One culprit per check.** The revert does not apply, the culprit is
     a revert or already reverted, or main is still NO-GO after the
     revert: the document stays `red` with the reason and the step fails
     (ADR 0034).

5. **A red main stops assign, rework and release, and is lifted by a
   fix.** Before `assign`, `rework` or `release`, `core/tower.py` reads
   `mainline/main`; `red` refuses the step before it runs, the state
   stays, and the failed attempt is recorded with the error `main is
   NO-GO since <G>: <line>`, so the reason is in the ticket's document as
   in main's. `rework` is refused too: an attempt started from a red main
   would be charged a failure it inherited (ADR 0053 §6's rule). While
   the document is red a `mainline` step judges main's head as it is and
   never bisects again: GO turns it green, NO-GO fails again. So a person
   fixes main, then `./go resume` requests the stranded steps (ADR 0034).
   The shape of `release-freeze` (ADR 0013 §6): a refusal before any
   effect, recorded, lifted by state.

6. **Rework on the new base.** `core/steps/rework.sh` reads `.mainline`
   before `.ci`: its `judge` (`base` or `main`) goes into the `.rework`
   entry, and the next attempt prints `mainline: null` beside `ci: null`.
   For these judges `core/crew.sh` starts attempt n + 1 from main's head
   (`REWORK_FRESH`), not from the rejected head: that head is under a
   revert or judged on a base that is gone. The worker gets the ticket,
   the brief and the verdict (ADR 0038 §4). A rework never resets main:
   at L0 `adapters/gitea/bootstrap.sh` force-pushes this checkout to main
   only for attempt 1 (`CODE_HOST_KEEP_MAIN` otherwise). The cap
   (ADR 0038 §3) counts these attempts: each spends a worker's quota like
   any other. At L0 an attempt 2 replays `fixtures/<ticket>/<role>/
   attempt-2/` or fails naming it (ADR 0038 §8). No integrator role.

7. **The proof.**
   - `core/tests/test_mainline.py` (`unittest`, from this ADR): the
     perimeter match, the overlap over touched paths, the group limit,
     the bisection's culprit and its number of runs.
   - `ci/mainline.sh` in `./go ci`, no engine: the real merge and
     `mainline` steps on a temp git repository behind a stub CodeHost,
     with a stub workload whose `gates` target is a script. Re-judge:
     base unmoved, merged; moved outside the perimeter, merged with no
     gate run; moved inside it, `ci-failed` and not merged; touched and
     since reverted, still `ci-failed`; base rewritten, the step fails.
     Main check: a green group; a red group bisected, its culprit
     reverted on the host, main green; a revert that does not apply
     leaves main red.
   - `core/tests/test_tower.py`: a red `mainline/main` refuses `assign`,
     `rework` and `release` with the reason, and no other step.
   - `./go demo-mainline`, L0, no key: tickets 0020–0022, hand-written
     fixtures (README, "What is real"). 0020 and 0021 hold disjoint
     perimeters; 0020 renames a function 0021 starts calling, and each
     passes its gates alone. 0022's perimeter is `app/backend/meetups/
     db.py`. After the three are judged, a change to `db.py` lands on main
     through the code host with no ticket: a person's merge. 0020 and
     0021 merge without re-judging; 0022 goes back to rework naming
     `db.py` and that commit; the main check finds main NO-GO, bisects to
     0021's merge, reverts it, and main is GO. The demo applies the bus's
     events itself, with `core/tower.sh step` as `adapters/inproc/run.sh`
     does, for two reasons it prints: the person's merge must land
     between judging and merging, and it stops where other demos take
     over (the release is `./go e2e`'s, an attempt 2 `./go
     demo-rework`'s), acknowledging those requested steps unapplied.

## Alternatives rejected

- **A merge queue with a global lock.** The owner's decision of
  2026-11-07: every PR waits for every gate run ahead of it, a wait that
  grows with the tickets in flight. Here a disjoint PR merges at once and
  main pays one gate run per group.
- **Speculative batches** (judge PR combinations in parallel, merge the
  largest green prefix). N runs at once is N times the engine at L0, on a
  laptop, paid mostly for nothing when perimeters are disjoint. The
  bisection pays only when main is red.
- **A version control of our own** (a patch algebra that merges without
  conflicts). Out of scope: the code host is a port (ADR 0043) and git is
  the contract every workload already has.
- **An integrator role** that rebases and fixes conflicting PRs. A
  seventh role; `ci/roles.sh` holds six. The conflict goes back to the
  `dev-worker` (ADR 0038).
- **Re-judge every PR whose base moved.** Correct, but every concurrent
  PR pays a second full gate run, mutation included, for an interaction
  disjoint perimeters make rare and the main check catches.
- **Block merges while main is unchecked.** The queue again.
- **Restore main by force-pushing the last green commit.** It rewrites
  history others fetched, loses outside commits made meanwhile, and a
  protected branch refuses it. A revert is a commit: audited, reversible.
- **Every gate on main.** Mutation repeats each PR's run on the sum of
  their lines; `test-weakening` reads a ticket's declared removals, and
  main has no ticket.
- **Each ticket's step judges only its own merge commit** (no groups).
  The release problem it solves is solved by releasing the green main
  (§3), and it bisects nothing: every red merge would be its own culprit
  only when no outside commit landed before it.

## Consequences

- Every merged ticket gains a `mainline` history entry before release;
  `demo-rework`'s trail gains it; `./go audit` reads `main-green` as
  merged and a reverted merge as why a merged PR is back at `ci-failed`.
- The replays (`e2e`, `demo-rework`, `demo-review`, `demo-mainline`)
  delete `mainline/main` with the ticket's document: a red main left by
  an earlier run on a live plane cannot refuse `./go demo`'s `assign`.
- At L0, attempt 1's bootstrap still resets main to this checkout. A
  ticket assigned after a merge in the same run (ADR 0053 §5's resume)
  resets main under the merged ones; their `mainline` step then fails on
  a merge that is not on main. Noted, not changed here.
- ADR 0053's holds ended at `merged`; under this ADR a merged ticket can
  return to `ci-failed` by a revert, and a ticket assigned meanwhile on
  its files would put two workers on them. `merged` joins
  `core/assign.py`'s holding states: a hold lasts until `main-green`. The
  ticket parsing is ADR 0053's own (`core/assign.py`'s `perimeter`).
- Two culprits in one group need a person. At L2 two `mainline` steps at
  once would race on the document; the inproc orchestrator applies one
  event at a time.

## Golden-rule check

`./go demo` runs `e2e` on ticket 0001, merged onto an unmoved main: its
tree is the one CI passed, so the `mainline` step is GO after one fetch,
and the release ships the same commit as before. `./go demo-mainline` is
a separate target, not in the demo's fixed list (ADR 0003).
