# ADR 0077 — The wiki is rendered from the repository at the tag, never edited by hand

- Status: accepted
- Date: 2026-11-12
- Amends: ADR 0075 §1 (a sixth release job, `wiki`, after `release`);
  ADR 0065 §1 (`wiki` is exempt from the `plan` gate like the other
  release jobs)

## Context

The repository has had `has_wiki=true` since it went public, and the wiki
is empty: `git ls-remote https://github.com/nicolaguglielmi/lights-out.wiki.git`
answered "Repository not found" on 2026-11-12. The Wiki tab is one of the
first places a visitor clicks, and an empty one reads as an abandoned
project.

The documentation already exists in the repository: the README, the
documents under `docs/`, the ADRs, and the release prose the release commit
may write in `docs/releases/`. Each of them changes with the code and is
checked by `docs-drift`. A second copy kept by hand would start drifting at
its first edit, and nothing here checks a wiki.

What GitHub documents, read on 2026-11-12:

- A wiki is a git repository, `<repository>.wiki.git`. It can be cloned
  and pushed to, and only its default branch is shown. The first page must
  be created in the web UI before the repository exists
  ([Adding or editing wiki pages](https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages)).
- A page's title is its file name, and its extension chooses the renderer
  (`.md` is Markdown). `_Sidebar.md` and `_Footer.md` fill the sidebar and
  the footer of every page
  ([Creating a footer or sidebar for your wiki](https://docs.github.com/en/communities/documenting-your-project-with-wikis/creating-a-footer-or-sidebar-for-your-wiki)).
- Only collaborators can edit a public repository's wiki by default; the
  setting is "Restrict editing to collaborators only"
  ([Changing access permissions for wikis](https://docs.github.com/en/communities/documenting-your-project-with-wikis/changing-access-permissions-for-wikis)).

What GitHub does not document: whether the workflow's own `GITHUB_TOKEN`
can push to `<repository>.wiki.git`. Neither the token's page nor the
`permissions` reference mentions wikis. The evidence is third-party: the
README of `Andrew-Chen-Wang/github-wiki-action`, a widely used Action that
pushes with `${{ github.token }}` and says the job needs `contents: write`,
and pull requests where a 403 from the wiki remote was fixed by that
permission. That is the assumption this ADR makes, and the first tag after
it tests it (Consequences).

## Decision

1. **The wiki is an output of the release.** A `v*` tag renders it from
   the repository at the tag and pushes it. Nobody edits it, and every
   page says so and links its source. A hand edit lasts until the next
   release, which replaces every file.

2. **The pages, each from files already in the repository:**

   | page | source |
   |---|---|
   | `Home` | `README.md` |
   | `Tutorial` | `docs/tutorial.md` |
   | `Use-it-on-your-own-codebase` | `docs/use-on-your-repo.md` |
   | `Reference` | `docs/reference.md` |
   | `Production` | `docs/production.md` |
   | `Architecture` | `docs/ARCHITECTURE.md` |
   | `Decisions` | `docs/adr/NNNN-*.md`: one row per ADR, its number, the title from its first heading, the first word of its status, and its link |
   | `Releases` | the wrapper's `version=` and every `docs/releases/v*.md`, newest first |
   | `_Sidebar` | the list of the pages above |
   | `_Footer` | the version the wiki was rendered at, and that it is generated |

   `docs/PLAN.md`, `docs/plans/` and `docs/launch/` stay out: they are the
   owner's working documents, not documentation for a user.

3. **A page is its source, with its links made absolute.** The first line
   of each page from a document names the document and its link at the
   tag. Then comes the document unchanged, except for its link targets:
   - a target that is already absolute (`http:`, `https:`, `mailto:`) or an
     anchor on the same page is kept;
   - a relative target that resolves to one of the documents above becomes
     that page's wiki URL, anchor kept; `docs/adr/` becomes `Decisions`;
   - any other relative target becomes its file on GitHub at the tag:
     `raw/` for an image, `blob/` for a file, `tree/` for a directory;
   - a relative target to a path that does not exist stops the rendering.
     The wiki never publishes a broken link.

   Wiki links are absolute (`https://github.com/<repository>/wiki/<page>`)
   because a relative link on `Home`, served at `/wiki`, resolves one level
   too high. Fenced code blocks are copied as they are.

   `package/wiki.py OUT` renders into a directory, with the standard
   library only. `package/sync-wiki.sh PAGES DIR [--push]` clones the wiki
   into `DIR`, replaces every file in it with the rendered ones in `PAGES`,
   commits as the owner's noreply identity (as `sync-action.sh` does, ADR 0075 §3) and
   pushes with `--push` only. Unchanged pages push nothing. A wiki whose
   repository does not exist yet is a failure that names the owner's first
   step.

4. **One job, `wiki`, after `release`.** It needs `release`, so a release
   that failed leaves the wiki as it was. It holds exactly
   `contents: write`, and pushes with the workflow's `GITHUB_TOKEN`: no
   secret, and ADR 0075's one secret stays the only one. The push step
   runs on a `v*` tag only. On a dispatch it is a dry run like the other
   release jobs (ADR 0075 §5): the pages are rendered and listed in the
   step summary, and nothing is cloned or pushed, because the wiki may not
   exist yet.

5. **`ci/wiki.sh` proves it**, in `./go ci`, engine-free and network-free,
   from this ADR and not from the scripts: the rendering of the
   repository has exactly the pages of §2; each page's text, links
   blanked, is its source's; no target is relative; every wiki link names
   a rendered page and every GitHub link a path at the tag; `Decisions`
   has one row per ADR; two renderings are identical; a fixture tree gives
   the links §3 names; the sync is checked against local bare
   repositories; the job is shaped as §4 says. Each check runs once on a
   planted defect to show it can fail.

## Alternatives rejected

- **A wiki kept by hand.** It is a second copy of the README and `docs/`
  that nothing checks. It drifts at the first edit, and the repository's
  rule is that the docs move with the code (`docs-drift`).
- **GitHub Pages only.** The landing page in `site/` already exists and
  links the docs on GitHub. But the Wiki tab stays empty, which is what a
  visitor sees, and Pages is a page, not a set of searchable documents
  with a sidebar.
- **No wiki: turn `has_wiki` off.** It hides the empty tab, and costs
  nothing. But it also removes a place where the documentation is read
  without leaving the repository's page, with a sidebar and GitHub's wiki
  search, at the cost of one job.
- **A third-party wiki Action.** It would be another pin in a job that
  holds `contents: write`. Clone, copy, commit and push are four git
  commands, and the repository already does the same for the Action
  repositories (ADR 0075 §3).
- **Rendering on every push to `main`.** The wiki would then describe
  commits that are not released. A user reading it would find commands
  their installed version does not have. At the tag, the wiki and the
  release say the same thing.
- **A fine-grained token as a secret.** GitHub's `GITHUB_TOKEN` reaches
  the repository that runs the workflow, and the wiki belongs to it. A
  token would be a second long-lived secret to rotate. If the first tag
  shows that `GITHUB_TOKEN` cannot push to the wiki, the job fails with
  the remote's answer, and that is the moment for a token and an
  amendment.

## Consequences

- The release is still one `git push origin v<version>`. The wiki is
  replaced a few minutes later, after the release draft.
- The owner does two things once: saves a first page in the Wiki tab,
  which creates the wiki's repository (no API does it), and leaves
  "Restrict editing to collaborators only" ticked. Until the first page
  exists, the `wiki` job fails on a tag and says so. Nothing else breaks.
- If GitHub refuses `GITHUB_TOKEN` on the wiki, the first tag's `wiki`
  job fails with a 403. The release itself is already drafted by then.
- `ci/plan.sh` exempts `wiki` with the other release jobs. `ci/package.sh`
  needs nothing new: the job has the github.com guard and no `||`.
- Every document link that used to point into the repository now points
  at the tag on GitHub, so a wiki page always matches the code of the
  release it was rendered from.

## Golden-rule check

Nothing here runs on `./go demo`. The job is GitHub-only (ADR 0039).
`ci/wiki.sh` needs git and Python's standard library, and no network: the
wiki it pushes to is a local bare repository.
