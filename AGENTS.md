# Instructions for coding agents

These rules hold for every tool that works on this repository: Claude Code,
Antigravity, Codex, Gemini CLI or any other. `CLAUDE.md` holds the full
working rules (in Italian); this file repeats the ones every tool must follow.

## Nothing private in the repository

The repository is public. Never commit, and never write into a tracked file:

- personal email addresses, account names other than the public GitHub
  login, cloud project ids or billing details;
- absolute local paths (`/Users/...`, `/home/...`, `C:\Users\...`);
- session ids, request ids, or anything copied from a provider's error
  that identifies an account;
- the names of other projects, containers or services on the machine;
- handoffs, session notes and private roadmaps.

Handoffs go to `.claude/context/handoffs/<branch>.md` (the branch name with
`/` as `-`) and the coordinator's state to `.claude/context/HANDOFF.md`, whichever
tool you are: both are gitignored and stay local (docs/adr/0065 §5). Private
plans go under `.crew/`, which is gitignored too. What a reader of the
repository needs from a handoff goes into an ADR, the release notes or
`ROADMAP.md`, written without the data above.

Write reports about live runs with placeholders (`<redacted>`, `~/`, `.env`)
instead of the real values.

Before every push, check the commits that are not on `origin/main` yet:
`gitleaks git --log-opts="origin/main..HEAD"` and a search of the diff for the
data listed above. If anything turns up, fix the commits before they leave
the machine.

## How work reaches `main`

One milestone per branch. A branch reaches `main` only through a `land/*`
branch on which every workflow is green (package, self, stranger, podman,
ci, site), whichever tool wrote it. Never accumulate commits on a local
`main` and push them together: on 2026-11-25, 113 commits made that way
held a weakened test suite, 22 static-analysis findings and a demo over
its time budget, and none of it had been through a workflow.

Heavy checks (`./go ci`, `./go demo`, image builds) run on GitHub's
runners, not on the contributor's machine, unless the owner says otherwise.

## Secrets

No secret in the repository, not even in examples: only `.env.example`, with
fake values. Real keys live in the gitignored `.env`; never print or copy it.

## Commits

Commits are authored by the owner's GitHub noreply address. No
`Co-authored-by` or other attribution trailers naming a tool or a model.
