# ADR 0065 — One proof per commit: a commit already green on a branch is not judged again on `main`, a docs-only push skips the demo, and each branch writes its own handoff

- Status: accepted (amends ADR 0002 [what the workflows run on a push]; the milestone skill's hand-off step)
- Date: 2026-11-09
- Milestone: none; the coordinator's landing process (`.claude/context/HANDOFF.md`)

## Context

Since 2026-11-08 milestones are built in parallel sessions and landed by
one coordinating session: each branch is merged into the integration
branch, pushed, judged by the six GitHub Actions workflows, and `main` is
fast-forwarded to the same commit once they are green. On 2026-11-09 the
owner asked why every landing takes so long. Measured that day:

- One round of the six workflows takes as long as `ci`, its slowest: 22
  and 26 minutes on the landings 1ee0569 and 03da20b (runs 42595901173,
  42594426084), although its longest job, `mainline`, takes 12. The rest
  is waiting for runners.
- Every landed commit was judged twice: once on the integration branch,
  then again, unchanged, when `main` was fast-forwarded to it. The second
  round queues beside the next commit's first.
- No workflow filters by path. A one-word fix in `ROADMAP.md` (8e968aa)
  ran the 15-minute demo twice.
- Every merge of a parallel branch conflicted on
  `.claude/context/HANDOFF.md`, the one file every session rewrites.

## Decision

1. **`plan`, the first job of `ci`, `stranger`, `self`, `podman` and
   `package`.** It runs `.github/plan.sh`, which prints two outputs, and
   every other job of the workflow but `publish` needs it and runs only
   when its outputs say so. It holds `actions: read` and `contents: read`,
   nothing else.
2. **A commit already green is not judged again on `main`.** On a push to
   `main`, when a run of the same workflow on the same commit, pushed to
   another branch, concluded `success`, `run` is `false`, every other job
   is skipped, and the summary names the run that judged it. A push to
   any other branch, a tag, a pull request and a manual dispatch always
   run. `site` is left alone: it deploys from `main`, so it must run there.
3. **A docs-only push skips the demo.** When every path the push changed
   (GitHub's compare of `before` and the commit, at most 300 files, the
   API's page) is documentation, `full` is `false`: in `ci` only the `ci`
   job runs, which is `./go ci` with `docs-drift` in it; in `self` the
   gates run; `stranger`, `podman` and `package` skip. Documentation is:
   `*.md` at the root, `docs/**` but `docs/assets/**`, `site/**`,
   `.claude/**`, and `README.md` under `actions/*/` and `console/`. Not
   documentation, though written in Markdown: `tickets/`, `roles/`,
   `workloads/`, `.lights-out/` and `fixtures/`, which the demo and the
   gates read. A new branch (`before` all zeros), a compare the API
   refuses or a list cut at 300 files count as not docs-only.
4. **The classifier is proved by `ci/plan.sh`**, a self-check: a table of
   paths labelled docs or not, and every job of the five workflows but
   `plan` and `publish` gated on `plan`'s outputs, so a job added later
   cannot escape it.
5. **One handoff per branch.** A session writes its handoff to
   `.claude/context/handoffs/<branch>.md` (the branch name with `/` as
   `-`), never to `.claude/context/HANDOFF.md`, which only the
   coordinator writes: the state of the integration branch and a line per
   branch landed, pointing to its file.

## Alternatives rejected

- **Path filters (`on.push.paths-ignore`).** A filtered push leaves no run
  at all, so nothing says the commit was looked at, and `self` and
  `docs-drift` must run on docs too.
- **Not running on `main` (`branches-ignore: [main]`).** A commit pushed
  to `main` directly, by the owner or the tag's release commit, would
  never be judged. The plan job judges it unless the same commit already
  passed elsewhere.
- **A merge queue.** It needs pull requests and branch protection, which
  the landing process does not use; the owner fast-forwards `main`.
- **Fewer jobs in `ci`.** Each is the acceptance of a milestone; dropping
  one is a decision for that milestone's ADR, not for the landing process.

## Consequences

- A landing costs one round of the workflows, not two; a docs-only push
  costs the `ci` and `self` jobs, a few minutes, instead of the demo.
  Measured on 2026-11-09: the commit that brought `plan` (5c51fdd) took 13
  minutes for its six workflows on the integration branch, with nothing
  else queued, against 22 and 26 for the two landings before it; the
  fast-forward of `main` to it took 31 s, every job but `plan` skipped and
  each run's summary naming the branch's run (ci run 42610705844). The
  docs-only lane's time is the commit after it, in the handoff.
- On `main`, a skipped run is green and says which run on which branch
  judged the commit: the proof is that run, with the same commit id.
- A docs-only push that breaks the demo through a page the demo reads
  would pass. Rule 3's list keeps the pages the demo reads out of
  documentation; `ci/plan.sh` holds the list.
- Parallel branches no longer conflict on the handoff; the coordinator
  writes one line per landing.
