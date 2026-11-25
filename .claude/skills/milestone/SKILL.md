---
name: milestone
description: Run one milestone of the lights-out implementation plan end to end, from reading the acceptance criteria to leaving the repo green. Use when the user says /milestone followed by a milestone id such as M0, M3 or M7.
allowed-tools: Read, Write, Edit, Bash, Grep, Glob, Task, WebSearch, WebFetch
---

# Milestone

Run milestone **$ARGUMENTS** of `docs/PLAN.md`. One milestone per session. Do not start the next one.

## 1. Frame it

Read the milestone in section 7 of `docs/PLAN.md`, plus sections 2, 3 and 4 for the rules it has to respect.

Restate, in under ten lines:

- the deliverables
- the acceptance criterion, verbatim, in the form of the command that proves it
- what is explicitly out of scope for this milestone

Stop if the acceptance criterion is not mechanically checkable. Say why and propose a version that is.

## 2. Plan, do not write

Stay read-only until the plan is approved. Produce a file-by-file plan: what gets created, what gets modified, what gets deleted.

Delegate to the `architect` subagent anything that adds a package, an abstraction, an adapter, or a dependency. Wait for the ADR before planning around it.

Then stop and wait for approval. Do not touch a file before it comes.

## Models and effort per task

Plan §5 fixes the construction models; `docs/plans/third-arc-execution.md`
assigns them per milestone. Sonnet is the floor: no subagent runs on a
smaller model. When you delegate, pass the effort below; a session that
runs on Sonnet works at xhigh itself except while it waits on `./go`.

| Task | Subagent | Effort |
|---|---|---|
| ADR, structural decision | `architect` (opus) | high |
| Gates, ci checks, tests from the spec | `gate-writer` (sonnet) | xhigh |
| README, docs, commit messages | `doc-scribe` (sonnet) | medium |
| A cross-cutting problem the session has missed twice | a one-shot task on fable | xhigh |

## 3. Build

- Delegate tests, gates and the verify query to the `gate-writer` subagent.
- Delegate README, docs and commit messages to the `doc-scribe` subagent.
- Keep the main session for the integration work.

Smallest change that satisfies the criterion. If you find yourself building for a future milestone, stop and remove it.

## 4. Prove it

Run the acceptance command on a clean container. Paste the real output. If it fails, fix and run it again; never describe a result you have not seen.

Then run `make demo` for the whole repo. A milestone that breaks the demo is not done, whatever else works.

If the milestone changes what a visitor of the landing page sees or runs (a command, a measured number, a screen of the console), `site/index.html` says it in the same branch, and the README, the reference and the tutorial say the same. `ci/docs-drift.sh` checks the page's version, links and `./go` targets, never its prose: reading the page against what changed is part of done. The console's stills are rendered again by the `site` workflow on every push that changes the console; look at them in the `site-review` artifact before calling the milestone closed. Do not move the wrapper's `version=`: that happens in the release commit, with the page, right before the tag.

## 5. Hand off

Close the session with:

- the acceptance command and its output
- what changed, in five lines
- anything you found that contradicts `docs/PLAN.md`, which is more valuable than the code
- the first thing the next milestone should look at

Write this to `.claude/context/handoffs/<branch>.md`, the branch name with `/` as `-` (docs/adr/0065 §5): never to `.claude/context/HANDOFF.md`, which only the session that lands the branches writes. Both files are gitignored and never committed (docs/adr/0065 §5, amended 2026-11-25): leave them out of the commit, and remind the user to clear the context before starting the next milestone.
