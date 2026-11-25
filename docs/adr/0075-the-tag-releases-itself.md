# ADR 0075 — The tag releases itself: PyPI by trusted publishing, the two Action repositories synced, the release drafted; a dispatch is a dry run

- Status: accepted
- Date: 2026-11-11
- Amends: ADR 0057 §4 (`id-token: write` on a second job, `pypi`) and §7
  (the Action repositories are refreshed by the workflow, not by hand);
  ADR 0065 §1 (`package.yml`'s release jobs are not gated on `plan`, as
  `publish` is not)
- Amended by: ADR 0077 (a sixth job, `wiki`, after `release`: the wiki
  rendered from the repository at the tag)

## Context

After `git push origin v<version>` the `publish` job of
`.github/workflows/package.yml` builds, pushes and signs both images. Then
the owner did the rest by hand. Counted from `docs/launch/checklist.md`
for 0.3.0 and 0.3.1 (a measure, 2026-11-11), the steps were:

1. build the wheel from a clean clone and look inside it;
2. try the wheel;
3. `uv publish` with a project-scoped token read from the keychain;
4. check the upload with an empty uv cache;
5. and 6. `cosign verify` once for each image;
7. read both digests from the run's log;
8. write the release `v<version>` with the digests and the verify command;
9. and 10. copy `actions/test-proof/` and `actions/test-integrity/` to the
   root of their own repositories, commit, tag `v<version>`, push;
11. and 12. write a draft release in each of those two repositories;
13. publish the three drafts to the Marketplace.

Step 13 needs the account's passkey: the Marketplace publish has no API.
The other twelve need no judgement. Each needs a credential or a context
switch, and a forgotten step fails nothing: `ci/package.sh` checks
`actions/*` here, never the copies.

A PyPI token is a long-lived secret with upload rights to the project. It
lived in the owner's keychain because no workflow uploaded.

## Decision

1. **A `v*` tag runs the whole release in `package.yml`, in five jobs.**
   Each job holds only the permissions it uses. Nothing is uploaded until
   the version is checked and the wheel is built.

   | job | needs | runs on | permissions | what it does |
   |---|---|---|---|---|
   | `dist` | — | a `v*` tag or a dispatch | `contents: read` | Checks that the tag, the wrapper's `version=` and `pyproject.toml`'s `version` are equal. Builds the sdist and the wheel with `uv build`, at the workbench's uv pin. Checks the wheel holds `lights_out/perimeter.sh` and `lights_out/platform_policy/merge.env` and no `tests/`. Runs `lights-out-gates version` from the wheel and checks it prints the version. Uploads `dist/` as an artifact. |
   | `publish` | `dist` | a `v*` tag or a dispatch | `contents: read`, `packages: write`, `id-token: write` | Builds both images, pushes and signs them as before (ADR 0057 §4). Then `cosign verify` checks each signature by digest, against this workflow at this ref. Outputs the version and both digests. |
   | `pypi` | `dist`, `publish` | a `v*` tag only | `contents: read`, `id-token: write`; environment `pypi` | `uv publish --trusted-publishing always --check-url https://pypi.org/simple/` uploads the artifact's files. There is no token and no secret. |
   | `actions` | `publish` | a `v*` tag or a dispatch | `contents: read`; one secret | Runs `package/sync-action.sh` for `test-proof` and `test-integrity`, then drafts a release `v<version>` in each of those repositories. |
   | `release` | `publish`, `actions` | a `v*` tag or a dispatch | `contents: write` | Drafts the release `v<version>` here. `package/release-notes.sh` writes its notes. |

   `publish` keeps its `if:` unchanged, and the other jobs are reached
   through `needs`. That is why a broken `dist` publishes nothing at all.
   `dist`'s own `if:` says "not a branch push" as
   `!(github.event_name == 'push' && !startsWith(github.ref, 'refs/tags/v'))`,
   because ADR 0039's guard check (`ci/package.sh`) admits no `||` outside
   `publish`. The workflow has only two triggers, push and dispatch.
   The four new jobs are not gated on the `plan` job, and neither is
   `publish`. ADR 0065 §1 gates every other job on `plan`, but `plan`
   skips a commit that is already green on a branch, and a tag is always
   on such a commit. `ci/plan.sh` exempts these four jobs by name, in
   `package.yml` only.

2. **PyPI by trusted publishing (OIDC).** The `pypi` job asks GitHub for
   an OIDC token. PyPI trusts it for one triple: repository
   `nicolaguglielmi/lights-out`, workflow `package.yml`, environment
   `pypi`. The upload uses `uv publish`, with uv fetched at
   `workbench/Containerfile`'s version and hash through
   `workbench/fetch.sh`, the same way cosign is fetched. That adds no new
   Action and no new pin. Building in `dist` and uploading in `pypi` is
   the split uv's own GitHub guide recommends (read 2026-11-11): the job
   that runs the build tool never holds the token. The `pypi` job never
   runs on a dispatch, because a version on PyPI cannot be uploaded twice.
   Setting up the publisher on PyPI is the owner's act
   (`docs/launch/checklist.md`, "Release automation").

3. **The Action repositories by a token scoped to them alone.** The secret
   `ACTION_REPOS_TOKEN` is a fine-grained personal access token. Its only
   repositories are `nicolaguglielmi/test-proof` and
   `nicolaguglielmi/test-integrity`. Its only permission is Contents, read
   and write, which covers commits, tags and releases. The `actions` job
   is the one place in the workflow that names it, and the job reads it
   only on a `v*` tag. Git gets the token through `GIT_CONFIG_*`
   environment variables, as `actions/checkout` passes its own. The token
   is never in a URL, an argument or a file.

   `package/sync-action.sh NAME VERSION DIR [--push]` does these steps in
   order:
   1. Clones the repository.
   2. Copies `actions/NAME/action.yml`, `actions/NAME/README.md` and
      `LICENSE` to its root.
   3. Refuses unless the shim's step uses
      `nicolaguglielmi/lights-out@v<VERSION>`.
   4. Commits as the owner's noreply identity, the same author as the
      0.3.x copies.
   5. Tags `v<VERSION>`.

   The script pushes only when `--push` is given. Re-running it after a
   tag already exists is safe. If the remote tag holds the same three
   files, the script says so and exits 0. If the files differ, it exits 1,
   so a release never moves a published tag. GitHub's own `GITHUB_TOKEN`
   cannot be used here, because it reaches only the repository that runs
   the workflow.

4. **The drafts carry what was read by hand.** `package/release-notes.sh`
   writes the notes for lights-out with:
   - both images by tag and by digest;
   - one `cosign verify` command per image, by digest, with the identity
     regexp ADR 0057 §4 gives;
   - the PyPI line;
   - the two Action repositories.

   If `docs/releases/v<version>.md` exists, its prose comes first. The
   release commit can write the prose and the workflow adds the facts.
   The Action repositories' notes follow 0.3.1's text. A draft that
   already exists is left as it is, because the owner may have edited it.
   The drafts are drafts: publishing them, and ticking "Publish this
   Action to the GitHub Marketplace", stays the owner's act (passkey).

5. **A dispatch is a dry run.** `workflow_dispatch` takes the input
   `dry_run`, a boolean, default `true`.
   - With `true`, nothing leaves the runner. `dist` builds and checks the
     wheel and prints the `uv publish` command. `publish` builds no image
     and prints the build, push, sign and verify commands.
   - In both cases, `actions` clones both repositories anonymously, makes
     the commit and the tag locally, and prints the diff, the push and the
     draft's notes. `release` prints the notes and the `gh release create`
     command.
   - Each job writes what it would do to the step summary.
   - With `false`, `publish` does what a dispatch did before this ADR: it
     pushes `sha-<commit>` images and signs them. PyPI, the Action
     repositories and the release still need a tag, so they only show what
     they would do.

## Alternatives rejected

- **A PyPI API token as a repository secret.** It is the same secret that
  lived in the keychain, now readable by every workflow on every branch.
  It lasts until someone revokes it. Trusted publishing issues a token
  that lasts minutes and works for one workflow file.
- **`pypa/gh-action-pypi-publish`.** It is the PyPA's own Action and
  generates PEP 740 attestations by default. But it is a container Action:
  another third-party pin in the job that holds the OIDC token. uv is
  already pinned and hashed in the workbench. Attestations can come later,
  through `astral-sh/attest-action` (uv's guide) or this Action, in an ADR
  of their own.
- **A GitHub App instead of a personal access token.** It is better
  scoped in theory: installed on two repositories, with short-lived
  tokens. But it needs an App, its private key as a secret, and a
  token-minting step or Action. For one owner and two repositories, a
  fine-grained token limited to those two does the same with one secret.
- **Git submodules or a subtree split instead of copies.** The
  Marketplace reads `action.yml` at a repository's root at a tag, so a
  copy is what it needs. A subtree split would rewrite history into those
  repositories, and a submodule is not read at all.
- **Separate `release.yml` workflow triggered by the tag.** The trusted
  publisher names one workflow file. A second file would need the image
  digests across workflows (artifacts or the registry API) and would run
  even when `publish` failed.
- **Uploading to PyPI from `publish`.** It has `id-token: write` already.
  But PyPI's publisher would then trust the job that also runs QEMU,
  buildx and the whole image build. One job per credential is the rule
  uv's guide states.
- **A dispatch that also uploads and tags.** A version cannot be re-uploaded
  to PyPI, and a moved Action tag breaks every `uses:` that pinned it. A
  rehearsal must not be able to burn the version.

## Consequences

- The release is `git tag v<version> && git push origin v<version>`,
  then three Marketplace clicks. The checklist's twelve hand steps become
  checks of the run. A failure stops at the job that failed. Re-running
  the failed jobs is safe: `sync-action.sh` and the drafts are idempotent.
  `uv publish --check-url` skips a file PyPI already has, so a re-run
  of the `pypi` job is harmless too.
- Every release uploads to PyPI, because the wheel's version must equal
  the tag. A release where "the light tier is unchanged" (0.3.1) now also
  uploads, which is what `uvx lights-out-gates@<version>` expects anyway.
- `ci/package.sh` now allows `id-token: write` on `package.yml`'s `pypi`
  job as well as `publish`. `ci/release.sh` checks the rest of this ADR.
  It checks the jobs, their `needs`, permissions, secrets and dry-run
  guards, and it runs both scripts against local repositories with one
  planted defect each.
- The owner sets up three things once: the trusted publisher on PyPI, the
  `pypi` environment limited to `v*` tags, and the `ACTION_REPOS_TOKEN`
  secret, which expires on the date the owner chooses. Until the
  publisher exists, the first tag's `pypi` job fails. Until the secret
  exists, `actions` fails on a tag and says which secret is missing.
  Nothing else breaks.
- The Gemini CLI extension's repository (`lights-out-gemini`, ADR 0069)
  is not created yet. Once it exists, it is the same kind of copy, and
  `sync-action.sh` could take it.

## Golden-rule check

Nothing here runs on `./go demo`. Every job is GitHub-only (ADR 0039),
and the L0 path needs no account, no key and no network beyond what it
already uses. `ci/release.sh` runs in the workbench with git and Python
only. It needs no network: the remotes it pushes to are local bare
repositories.
