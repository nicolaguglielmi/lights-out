# Launch checklist

What is left before the repository goes public and on launch day. It is
split into what the owner does (the owner's accounts, keys and GitHub
settings) and what is done in the repository. Each line gives the command
or the GitHub setting.

Sources: `docs/PLAN.md` §15 (phase 0, what is measured, risks) and §16;
`docs/plans/assessment-and-must-have-plan.md` (Horizon 1, "Measured before
launch"); `.claude/context/HANDOFF.md`, the M21 and M23 sections; the
README. A line taken from another file names that file.

Publishing is the owner's act: making the repository public, the tag,
the image, the issues and the announcement (plan §14, §15).

## The owner does: before launch

### Decide the numbers first

- [ ] **Set the target numbers before launch.** Plan §15, "Cosa si
  misura": stars, clones and unique visitors (GitHub → Insights →
  Traffic), issues opened by outsiders, outside contributors, talk
  invitations, inbound consulting requests. A target chosen afterwards
  measures nothing. The same goes for the signals that would start the
  search for a co-founder.

### Merge what is pending

- [ ] **Merge the open branches into `main`**, this one included, through
  a `merge/<name>` branch once Actions is green on it
  (`docs/plans/third-arc-execution.md`). The two parallel sessions M23's
  finding 2 named before M24, egress for the harnesses and the tower
  restoring `.lights-out/runners/` from the base, are both on `main`
  (checked 2026-11-10: `ci/attach.sh` case f GO, and NO-GO once the
  restore is removed).
- [ ] **`./go demo` green on Linux and on macOS** from a clean clone, before
  every announcement (plan §15, "Rischi").
  ```sh
  git clone https://github.com/nicolaguglielmi/lights-out.git
  cd lights-out
  ./go demo
  ```
  The last line is `GO: demo, milestone M9, <n>s to rollback (budget
  900s, plan §2)`. A first run over the budget now says `a first run: over
  the 900s budget, reported, not judged` (ADR 0013, amendment 2026-11-04).
  Run it again to get the judged figure.

### The recordings that spend keys

- [ ] **Record the Claude and OpenAI harnesses** (README, "Coding
  harnesses"; HANDOFF M19). Each writes
  `fixtures/0001/dev-worker/<harness>/`:
  ```sh
  ./go record TICKET=0001 PROFILE=l1 AGENT_HARNESS=claude-code MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5
  ```
  ```sh
  ./go record TICKET=0001 PROFILE=l1 AGENT_HARNESS=codex MODEL_PROVIDER=openai MODEL=gpt-6.1-sol
  ```
  Then read `tokens`, `turns` and `model` in each `run.json`. The first
  run may stop on a budget measured on Gemini.
- [ ] **Record an ADK role with Claude and with OpenAI** (plan §16, M18's
  "Fatto quando"; implemented, never run):
  ```sh
  ./go record-evals PROFILE=l1 MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5
  ```
  and the same with `MODEL_PROVIDER=openai MODEL=gpt-6.1-sol`.
- [ ] **Write the cost per provider in the README**, from those
  `run.json` files. The README says: "The cost per provider will be stated
  here once those recordings exist". Plan §16's "Rischi" asks for it before
  launch.
- [ ] **Re-record fixtures 0001 and 0005 under the shipped prompt** (two
  L1 runs with the owner's key, assessment Horizon 1 item 4). The
  assessment dated this on 2026-11-03; check that it is still needed.
  ```sh
  ./go record TICKET=0001 PROFILE=l1
  ```
  ```sh
  ./go record TICKET=0005 PROFILE=l1
  ```

### GitHub as code host (M21)

- [ ] **The throwaway repository** (HANDOFF M21, "What the owner must
  do"): an empty GitHub repository with `workloads/minimal/`'s contents at
  its root, committed and pushed. Merge commits allowed (GitHub's default).
- [ ] **The token**: github.com → Settings → Developer settings →
  Fine-grained tokens. Only that repository. Contents, Pull requests and
  Commit statuses, read and write.
- [ ] **The one L1 end-to-end on GitHub.** In `.env`:
  `GITHUB_REPO=<owner>/<name>`, `GITHUB_TOKEN=…`, `GEMINI_API_KEY=…`. Clone
  the throwaway, then:
  ```sh
  ./go attach <clone>
  ```
  ```sh
  ./go e2e CODE_HOST=github PROFILE=l1
  ```
  Expected: the PR opens on GitHub with the `gates` status, merges after
  `GO: gates`, the merge is built, signed and released on the local
  plane, the canary (0.2.0-outage) rolls back, and `GO: e2e` follows.
  This is the first execution of the attached release path anywhere
  (M21 finding 3). Record the result in plan §16 M21. Afterwards, pull the
  clone and run `./go attach` again before any other ticket.
- [ ] **The nightly.** This repository → Settings → Secrets and
  variables → Actions: the variable `LIGHTS_OUT_GITHUB_REPO` and the
  secret `LIGHTS_OUT_GITHUB_TOKEN`. It runs from the default branch, so
  after the merge to `main`. Run it once by hand: Actions → the
  `nightly-github` workflow → Run workflow (`workflow_dispatch`). Each run
  leaves a merged PR and a `conformance/<id>` branch in the throwaway
  repository.

### The package (M23)

- [ ] **Check the version** in the `version=` line of `lights-out`
  (`0.1.0`). Change it before the tag if you want another.
- [ ] **Push the tag from the merge on `main`** (HANDOFF M23, "What the
  owner must do to publish"):
  ```sh
  git tag v0.1.0 && git push origin v0.1.0
  ```
  The `publish` job of `package.yml` checks the tag equals `v$version`,
  builds amd64 and arm64 through QEMU and pushes
  `ghcr.io/nicolaguglielmi/lights-out:0.1.0`. The multi-platform build has
  **never run**: expect it to be slow (the workbench under QEMU for arm64),
  and look there first if it fails.
- [ ] **Make the package public**: GitHub → the package → Package
  settings → Change visibility → Public. It is linked to the repository
  by the `org.opencontainers.image.source` label.
- [ ] **The release `v0.1.0` with notes** (plan §15, phase 0): GitHub →
  Releases → Draft a new release → tag `v0.1.0`.
- [x] **Prove the plan's own criterion for M23** once the image is public
  (done 2026-11-09 with 0.3.0: the probe's pull request, 27 s, and the
  wrapper from the tag on an arm64 Mac that had never pulled the image,
  `GO: gates` in 46 s):
  a PR on another repository with
  `uses: nicolaguglielmi/lights-out@v0.3.0` (the root Action; up to
  v0.2.0 the path was `nicolaguglielmi/lights-out/action@<tag>`), and
  `lights-out gates` on a clean machine that pulls the published image.
  Since ADR 0057 the 300 s include the first pull.
- [x] **Update the README's "Not published yet" paragraph** once the image
  and the package are public. Done 2026-11-05 with `v0.1.0`.

### The gates image and the three Actions (M31)

Nothing here has run yet. Publishing is your act (ADR 0057).

- [ ] **From the next release on: the version moves in the release
  commit**, `version=` in `lights-out`, `version` in `pyproject.toml` and
  the landing page together, right before the tag. `ci/docs-drift.sh`
  refuses a page whose release is not the wrapper's (ADR 0054, amendment
  2026-11-09).
- [x] **Check `version=0.3.0`** in `lights-out` (the wrapper). The
  `publish` job refuses a tag that is not `v` plus that version. Checked
  2026-11-09.
- [x] **Tag `v0.3.0` from the merge on `main`.** Done 2026-11-09 from
  b931688; the job passed at its first run (run 42588480235). The `publish` job of
  `.github/workflows/package.yml` builds both images for amd64 and arm64,
  pushes them to ghcr.io and signs both keyless. It has never run, so look
  there first if anything fails.
- [x] **Make the new package `lights-out-gates` public** (it answered an
  anonymous pull right after the publish, 2026-11-09): GitHub → the
  package → Package settings → Change visibility → Public.
- [x] **Verify both signatures** from a machine with cosign (both verified
  2026-11-09 with the workbench's cosign 3.1.3). Run it once
  for `lights-out-gates:0.3.0` and once for `lights-out:0.3.0`:
  ```sh
  cosign verify ghcr.io/nicolaguglielmi/lights-out-gates:0.3.0 \
    --certificate-identity-regexp '^https://github.com/nicolaguglielmi/lights-out/.github/workflows/package.yml@refs/tags/v' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  ```
- [x] **Publish the root Action's Marketplace release.** Published
  2026-11-09. A draft release
  `v0.3.0` with the notes exists since 2026-11-09: GitHub → Releases → the
  draft → Edit → tick "Publish this Action to the GitHub Marketplace",
  pick a primary category, accept the GitHub Marketplace Developer
  Agreement, publish. Two-factor authentication must be on for the account.
- [x] **For each standalone Action, a public repository of its own**
  (both published to the Marketplace 2026-11-09):
  `nicolaguglielmi/test-proof` and `nicolaguglielmi/test-integrity`. Both
  exist since 2026-11-09, with `action.yml`, the README and the license at
  the root, tagged `v0.3.0`, each with a draft release, and proven on the
  probe's pull request (11 s and 23 s, both GO, run 42591256328). What is
  left is yours: open each draft and publish it to the Marketplace the
  same way. At the next version, copy `actions/test-proof/` (or
  `actions/test-integrity/`) to the root again and tag. The copies can lag
  a version without any check failing: `ci/package.sh` checks the sources
  here, never the copies. From the release after 0.4.0 the tag does it
  (section "Release automation").
- [x] **Measure the first pull.** Done 2026-11-09, each Action in a job of
  its own: 27 s, 14 s and 25 s, all GO (probe run 42589663783), written in
  the README's step 1, which now links the probe: keep it public, or
  change the link. In the public probe repository
  `nicolaguglielmi/lights-out-probe` (made public on 2026-11-08 because the
  account's Actions billing refused a private run), set the workflow's and
  the two shims' `uses:` to `@v0.3.0`, drop the step that builds the image
  and the `image:` inputs, and push a change on a pull request. The three
  times then include the first pull: write them in the README's step 1,
  replacing the 19 s, 2 s and 17 s measured with the image built in the
  job. Delete the probe afterwards if you do not want it public.

### Release 0.3.1 (2026-11-10)

The first run that drafts the contract (ADR 0064) and `test-proof`'s
HEAD-relative fix. The light tier is unchanged: no new upload to PyPI.

- [x] **The release commit** (995ba3f): `version=` in `lights-out`,
  `version` in `pyproject.toml` and the landing page at 0.3.1, every usage
  pinned to `@v0.3.1`; six workflows green (podman after one rerun of the
  harness-conformance timeout).
- [x] **Tag `v0.3.1`**: `package.yml` run 42790042827 published and signed
  both images; both signatures verified with the workbench's cosign.
- [x] **The two Actions' repositories** refreshed from `actions/` and
  tagged `v0.3.1`.
- [x] **Publish the three draft releases** `v0.3.1` with "Publish this
  Action to the GitHub Marketplace" ticked: `lights-out`, `test-proof`,
  `test-integrity`. Published 2026-11-10, the owner confirming with a passkey;
  the three Marketplace pages show 0.3.1.

### Release 0.4.0 (2026-11-11)

The agent network in the console and `./go network` (ADR 0071), per-role
instructions and MCP servers for the dev-worker (ADR 0073, M42), the
agent's toolbelt (ADR 0069, M39), the Trello-style kanban (ADR 0068),
`self` judged from its last green ancestor (ADR 0074), the podman port
fix, M24's fixes. Security scanning (ADR 0072) ships in 0.5.0.

- [x] **The release commit** (1626e63, landed on `main` as the merge
  0213078): `version=` in `lights-out`, `version` in `pyproject.toml`, the
  three plugin manifests and the landing page at 0.4.0, every usage pinned
  to `@v0.4.0`. `self` on the merge ran 3 h 10 min (run 43104690404): the
  first `self` judged by ADR 0074's judge, with the mutants in 4 shards.
  The two before it were cancelled at GitHub's 360-minute limit.
- [x] **Tag `v0.4.0`** on 0213078: `package.yml` run 43131164910 published
  and signed both images; both signatures verified with the workbench's
  cosign.
- [x] **The two Actions' repositories** refreshed from `actions/` and
  tagged `v0.4.0`.
- [ ] **The three releases `v0.4.0` are published, not drafts**:
  `lights-out`, `test-proof`, `test-integrity`. On the Marketplace: edit
  each one, tick "Publish this Action to the GitHub Marketplace" and save
  (owner, passkey).
- [ ] **PyPI**: `lights-out-gates` 0.4.0, which adds `lights-out-gates mcp`
  and `spec` (owner, the token in the keychain).

### Release 0.5.0 (2026-11-12)

Security scanning (ADR 0072, M43): SAST judged on the diff, an SPDX SBOM
attested per released image, passive DAST at staging, off by default. The
first release cut by its own tag (ADR 0075), with the wiki rendered at the
tag (ADR 0077), and the mutants that run only the tests that reach them
(ADR 0076). Named 0.5.0, not 0.4.1: it adds features, and nothing in it is
a fix of 0.4.0 alone.

- [x] **The release commit** (67a63d1, landed on `main` as the merge
  cf2955c): `version=` in `lights-out`, `version` in `pyproject.toml`,
  the three plugin manifests and the landing page at 0.5.0, every usage
  pinned to `@v0.5.0`, the prose in `docs/releases/v0.5.0.md`.
- [x] **Rehearsal**: `package.yml` dispatched on the merge with `dry_run`
  (run 43303067425), every job green, `pypi` skipped.
- [x] **The three one-time settings** of "Release automation" and the
  wiki's first page (owner, 2026-11-12).
- [x] **Tag `v0.5.0`** on cf2955c: `package.yml` run 43309065223, all
  nine jobs green; `lights-out-gates` 0.5.0 on PyPI by trusted publishing,
  both Action repositories tagged with a draft each, the wiki rendered.
- [x] **The three drafts `v0.5.0` published to the Marketplace** (owner,
  passkey, 2026-11-13): `lights-out`, `test-integrity` and `test-proof`
  each show 0.5.0 as Latest, and their three Marketplace pages list v0.5.0.

### Release 0.6.0 (2026-11-13)

The sprint of 2026-11-12 (M45–M50): the why of a NO-GO (ADR 0078), the
human gate on the card (ADR 0079), the live canary (ADR 0080), the
evidence pack per release (ADR 0081), the delivery measures (ADR 0082),
cost and time per ticket with a budget (ADR 0083), and the console's
Overview panels for the last two. Named 0.6.0: it adds features.

- [x] **The release commit**: the wrapper, `pyproject.toml`, the three
  plugin manifests and the landing page at 0.6.0, every usage pinned to
  `@v0.6.0`, the prose in `docs/releases/v0.6.0.md`.
- [x] **A dry run** of `package.yml` (`workflow_dispatch`, `dry_run`):
  run 43414295511, every job green, `pypi` skipped as a dry run does.
- [x] **Tag `v0.6.0`** on the release merge 397e08d: `package.yml` run
  43414807364, all ten jobs green; `lights-out-gates` 0.6.0 on PyPI by
  trusted publishing, both Action repositories tagged with a draft each,
  the wiki rendered.
- [x] **The three drafts `v0.6.0` published to the Marketplace** (owner,
  passkey, 2026-11-13): `lights-out`, `test-integrity` and `test-proof`
  each show 0.6.0 as Latest, and their three Marketplace pages list v0.6.0.

### Release 0.7.0 (2026-11-14)

Since 0.6.0: the gates' logic in Python (ADR 0084, M51), adapters outside
the repository with GitLab and Jenkins (ADR 0058, M32), M24's fixes and
its Gemini leg, the flow strip, the role matrix and the burn (ADRs
0085-0087, M52-M54), the provider scoreboard and the frozen console
(ADRs 0088, 0089, M55, M56), and the intake interview with a turn cap on
every ADK role (ADR 0090, M57). Named 0.7.0: it adds features.

- [x] **The release commit**: the wrapper, `pyproject.toml`, the three
  plugin manifests and the landing page at 0.7.0, every usage pinned to
  `@v0.7.0`, the prose in `docs/releases/v0.7.0.md`.
- [x] **A dry run** of `package.yml` (`workflow_dispatch`, `dry_run`):
  run 43651713236, every job green.
- [x] **Tag `v0.7.0`** on main (6f134db, the release merge and the
  jenkins job's fix), every job green: run 43652010467, PyPI 0.7.0, the
  wiki rendered, three drafts.
- [x] **The three drafts `v0.7.0` published to the Marketplace** (owner,
  passkey, 2026-11-15): `lights-out`, `test-integrity` and `test-proof`
  each show 0.7.0 as Latest.

### Release 0.8.0 (2026-11-16)

The sprint of 2026-11-16: the first run without friction (ADR 0093, M60),
GLM by Z.ai and a budget per provider (ADR 0092, M59), closed decisions
(ADR 0091, M58), Flagger and the GitLab CI and CircleCI recipes (ADR
0095, M62), and the second Python slice (ADR 0094, M61). Named 0.8.0: it
adds features.

- [x] **The release commit**: the wrapper, `pyproject.toml`, the three
  plugin manifests and the landing page at 0.8.0, every usage pinned to
  `@v0.8.0` (the Jenkins, GitLab CI and CircleCI recipes too), the prose
  in `docs/releases/v0.8.0.md`.
- [x] **A dry run** of `package.yml` (`workflow_dispatch`, `dry_run`):
  run 43909756283, every job green.
- [x] **Tag `v0.8.0`** on main (807f2a4), every job green: run
  43910381207, PyPI 0.8.0, the wiki rendered, three drafts.
- [x] **The three drafts `v0.8.0` published to the Marketplace** (owner,
  passkey, 2026-11-16): `lights-out`, `test-integrity` and `test-proof`
  each show 0.8.0 as Latest.

### Release 0.8.1 (2026-11-19)

M64's dev container and the seven levels (ADR 0097), the mutation
runner's stop on a coverage-hook failure (ADR 0076 amendment), DAST's
second scan of the stable (ADR 0072 amendment), `./go try` in `stranger`,
the frozen console following main (ADR 0089 amendment), the plain git
hooks, and the gates image waiting for a `.git` file a podman machine
shows late (added by the owner 2026-11-20). Named 0.8.1:
the shipped artifacts gain fixes, no command or flag.

- [x] **The release commit**: the wrapper, `pyproject.toml`, the three
  plugin manifests and the landing page at 0.8.1, every usage pinned to
  `@v0.8.1` (the Jenkins, GitLab CI and CircleCI recipes too), the prose
  in `docs/releases/v0.8.1.md`.
- [x] **A dry run** of `package.yml` (`workflow_dispatch`, `dry_run`) on
  2e2583d: run 44717167310, every job green.
- [x] **Tag `v0.8.1`** on main (2e2583d), every job green: run
  44721481964, PyPI 0.8.1, the wiki rendered, three drafts.
- [ ] **The three drafts `v0.8.1` published to the Marketplace** (owner,
  passkey).

### Release 0.9.0 (prepared 2026-11-25)

`M65`–`M97` on top of 0.8.1: verification from the proxy, one install
(ADR 0099), the plane half drafted by init, more stacks, the unified CLI,
the ADR lifecycle, signed provenance, epics and spec split, the
self-healing loop, the modular console with OIDC roles, managed GCP
adapters, the jury across providers (ADR 0130), a conformance suite for
every port (ADR 0133), CodeHost attestations (ADR 0134), thresholds
(ADR 0135), the `ci/` ratchet (ADR 0136), the reviewer's catch rate
(ADR 0137), the portfolio and the contract frozen at schema `v1.0`
(ADR 0138), the ADR reconciliation (ADR 0139). Planned as `1.0.0`, cut as
`0.9.0` on 2026-11-25 by the owner: the live runs in `ROADMAP.md`, Next,
are not done.

- [x] **The release commit**: wrapper `lights-out` (`version=0.9.0`),
  `pyproject.toml`, the three plugin manifests, Actions, CI surfaces,
  `site/index.html`, active docs at `0.9.0`, and prose in
  `docs/releases/v0.9.0.md`.
- [ ] **On `main` through green workflows** (package, self, stranger,
  podman, ci, site), then **a dry run** of `package.yml`
  (`workflow_dispatch`, `dry_run`), then **tag `v0.9.0`** (owner's act) to
  publish both signed images, `lights-out-gates` `0.9.0` on PyPI, synced
  Actions, wiki, and Marketplace drafts.

### The Python package on PyPI (M35)

The light tier, `lights-out-gates --light`, is a Python package built from
the root `pyproject.toml` (ADR 0061). Publishing it is the owner's act.

- [x] **Publish soon after the merge.** Published 2026-11-09 as
  `lights-out-gates` 0.3.0, from e0b73f6. PyPI refused `lights-out` on
  2026-11-09 ("too similar to an existing project": `lightsout`), so the
  distribution is `lights-out-gates` (ADR 0061, amendment 2026-11-09),
  free that day (`https://pypi.org/pypi/lights-out-gates/json` answered
  404), not reserved: until the first upload, `uvx lights-out-gates` in a
  hook recipe runs whatever package holds the name.
- [x] **Set the version** in `pyproject.toml` to the release: `0.3.0`
  since 2026-11-09, the wrapper's. A release that also ships the image sets the wrapper's
  `version=` in `lights-out` to the same: `package.yml` checks the tag
  against the wrapper only.
- [ ] **Build from a clean clone of the merge on `main`** and look inside:
  ```sh
  uv build
  python3 -m zipfile -l dist/lights_out-*.whl
  ```
  The wheel holds `lights_out/*.py`, `lights_out/perimeter.sh` and
  `lights_out/platform_policy/merge.env`, and no `tests/`.
  A dry run on 2026-11-09, from a clean clone at 0.3.0, held exactly
  that, and the wheel answered `GO: gates-light` on this repository; the
  wheel you upload is the one you build.
- [ ] **Try the wheel** in a repository with a committed `.lights-out/`:
  ```sh
  uvx --from ./dist/lights_out_gates-0.3.0-py3-none-any.whl lights-out-gates --light
  ```
- [x] **Upload** (done 2026-11-09), from a PyPI account with two-factor authentication and a
  token scoped to the project, the token in the environment, never in a
  file (TestPyPI first, if you want a dry run:
  `--publish-url https://test.pypi.org/legacy/`). `uv publish` asks for the
  token, or reads it from uv's own token variable:
  ```sh
  uv publish
  ```
  No workflow published it then. From the release after 0.4.0 the tag
  uploads it by trusted publishing (section "Release automation"), and
  the project-scoped token can be revoked once that has worked once.
- [x] **After the upload** (done 2026-11-09: `GO: gates-light` from PyPI
  with an empty uv cache), on a clean machine: `uvx lights-out-gates
  --light` in a repository with a contract. Then drop "Not on PyPI yet"
  from `docs/use-on-your-repo.md` (the hook recipes and the status table),
  `docs/reference.md` and the README.

### The agent's toolbelt (M39)

The Claude Code plugin and `lights-out-gates mcp` (ADR 0069). Nothing is
listed anywhere: every listing below is the owner's act. What each place
asks was read on 2026-11-10 (ADR 0069, "Verified before writing"); read it
again the day you list.

- [ ] **Release the package with the plugin.** The published 0.3.1 has no
  `spec`, `map` or `mcp` command, and the plugin runs `lights-out-gates` at
  its own version. In the release commit, move `version` in
  `pyproject.toml`, in `plugins/lights-out/.claude-plugin/plugin.json`,
  `plugins/lights-out-codex/.codex-plugin/plugin.json` and
  `plugins/lights-out-gemini/gemini-extension.json` (`ci/plugin.sh` checks
  they are equal) and the wrapper's `version=` together; upload to PyPI right after the tag (section "The Python
  package on PyPI"). Until the upload, a plugin install from `main` that
  falls back to `uvx` answers `NO-GO: lights-out — lights-out-gates
  <version> not found`.
- [ ] **Your own marketplace: this repository.** After the merge,
  `.claude-plugin/marketplace.json` on `main` is the listing; there is no
  form. Check it once on a clean machine:
  ```bash
  claude plugin marketplace add nicolaguglielmi/lights-out
  claude plugin install lights-out@lights-out
  ```
  then `/lights-out:gates` in a repository with a contract. The relative
  source has no ref: a new install gets `main` (ADR 0069, Consequences).
  The 14 days the launch plan wants before awesome-claude-code count from
  this day.
- [ ] **Anthropic's directory** (claude.ai, Cowork, and Claude Code as
  `lights-out@synced`): a paid claude.ai plan; submit from the developer
  portal, `claude.ai/directory/manage`, after the pre-submission checklist
  on claude.com. First run `claude plugin validate --strict
  plugins/lights-out`: on 2026-11-10 it passed with one directory warning,
  `ICON_MISSING`. The portal takes the icon only on the first save: add a
  square PNG, 512 to 2048 px, under 2 MB, as `icon.png` beside the
  plugin's manifest (`plugins/lights-out/.claude-plugin/`) before
  submitting. The hook
  and the MCP server are Claude Code components; check the portal's
  component table for what loads on claude.ai.
- [ ] **The official marketplace, `claude-plugins-official`**: it takes no
  submissions through the portal, only through an Anthropic partner
  contact (Claude Code's publish page, 2026-11-10). The roadmap's "its
  form" does not exist; skip it unless you have that contact.
- [ ] **The MCP registry** (in preview): after the PyPI upload of the
  release, add `mcp-name: io.github.nicolaguglielmi/lights-out` (an HTML
  comment is enough) to `README.md`, the package's README, and release
  again so PyPI shows it; write `server.json` with `mcp-publisher init`
  (name `io.github.nicolaguglielmi/lights-out`, a description of at most
  100 characters, one `pypi` package `lights-out-gates` at the release's
  version, transport `stdio`, the argument `mcp`), then
  `mcp-publisher login github`, `mcp-publisher validate`, `mcp-publisher
  publish`. Read the registry's PyPI guide that day: these steps come from
  its quickstart, which shows npm.
- [ ] **Codex's directory**: the plugin bundles no hook, so it is
  eligible ("Plugins with hooks aren't eligible for the public plugin
  directory", Codex's plugin build page, 2026-11-10). After the merge,
  check `codex plugin marketplace add nicolaguglielmi/lights-out` and
  `codex plugin add lights-out@lights-out` on a clean machine, then submit
  through the plugin submission portal that page links; read its review
  requirements that day. The release moves `version` in
  `plugins/lights-out-codex/.codex-plugin/plugin.json` with the others
  (`ci/plugin.sh` checks it).
- [ ] **Gemini CLI's gallery** (geminicli.com/extensions/browse): it crawls
  public repositories with the topic `gemini-cli-extension` daily, and
  wants `gemini-extension.json` at the repository's root (the releasing
  page, updated 2026-05-08). Create a public repository named
  `lights-out-gemini` (the extension's name is its directory's), copy
  `plugins/lights-out-gemini/` into its root at each release, tag it with
  the release, add the topic. No check here sees that copy: refresh it at
  every release, as the two standalone Actions' repositories. Then
  `gemini extensions install https://github.com/nicolaguglielmi/lights-out-gemini`
  on a clean machine.

### Release automation (ADR 0075)

From the release after 0.4.0, a `v*` tag runs the whole release in
`.github/workflows/package.yml`. The jobs are `dist` (the wheel built,
looked inside and run), `publish` (both images pushed, signed and
verified), `pypi` (trusted publishing, no token), `actions` (both Action
repositories synced, tagged and given a draft release) and `release` (this
repository's draft, with both digests and the cosign commands). Three
settings come first, once. Until they exist, the first tag's `pypi` job,
or its `actions` job, fails and says why.

- [ ] **The trusted publisher on PyPI.** pypi.org → Your projects →
  `lights-out-gates` → Manage → Publishing → add a new publisher on the
  GitHub Actions tab, with these fields:
  - **Owner**: `nicolaguglielmi`
  - **Repository name**: `lights-out`
  - **Workflow name**: `package.yml`
  - **Environment name**: `pypi`

  PyPI then accepts an upload only from that job, through GitHub's OIDC
  token. Once a release has uploaded this way, revoke the project-scoped
  API token in the keychain (PyPI → Account settings → API tokens).
- [ ] **The environment `pypi`.** This repository → Settings →
  Environments → New environment → `pypi`. Under "Deployment branches and
  tags", pick "Selected branches and tags" and add a tag rule `v*`. Add no
  required reviewer: the tag is already your gate. The job runs only on a
  `v*` tag. The rule makes sure no other ref can borrow PyPI's trust.
- [ ] **The token for the two Action repositories.** github.com →
  Settings → Developer settings → Personal access tokens → Fine-grained
  tokens → Generate new token:
  - **Resource owner**: `nicolaguglielmi`
  - **Repository access**: "Only select repositories",
    `nicolaguglielmi/test-proof` and `nicolaguglielmi/test-integrity`, and
    nothing else
  - **Repository permissions**: Contents, read and write (commits, tags,
    releases). Metadata, read-only, is added by GitHub. Nothing else.
  - **Expiration**: your choice. Write the date down: a tag after it fails
    in `actions`.

  Then this repository → Settings → Secrets and variables → Actions → New
  repository secret, named `ACTION_REPOS_TOKEN`, holding that token. Do
  not commit it anywhere and do not put it in `.env`. Only the `actions`
  job reads it, and only on a `v*` tag.

Each release, then:

- [ ] **Rehearse on the release commit** before you tag it: Actions →
  `package` → Run workflow → the branch, `dry_run` ticked (the default).
  Every job writes what it would do in the run's summary: the wheel built
  and run, the image commands, both Action repositories committed and
  tagged on the runner with their diffs, the notes of the three drafts.
  Nothing is pushed, uploaded or tagged. If you want the prose of the
  release written, commit it in the release commit as
  `docs/releases/v<version>.md`. It comes first in the draft.
- [ ] **Tag** from the merge on `main`, with the version moved in the
  release commit (`lights-out`, `pyproject.toml`, the plugin manifests,
  the landing page):
  ```sh
  git tag v<version> && git push origin v<version>
  ```
  `dist` refuses the tag unless it equals the wrapper's `version=` and
  `pyproject.toml`'s `version`.
- [ ] **Publish the three drafts to the Marketplace**, the one step left
  by hand (a passkey): `lights-out`, `test-proof` and `test-integrity` →
  Releases → the draft `v<version>` → Edit → tick "Publish this Action to
  the GitHub Marketplace" → Publish. Read the notes first. They hold both
  digests and the verify commands, which `publish` has already run.
- [ ] **If a job failed**, fix the cause and use "Re-run failed jobs".
  Every job is safe to re-run:
  - `pypi` skips files PyPI already has (`--check-url`).
  - `actions` stops with success when the tag already holds the same
    files, and fails, without moving anything, when it holds other files.
  - An existing draft is left as it is.
  - `wiki` pushes nothing when the pages are unchanged.

### The wiki (ADR 0077)

From the next tag, the release's last job, `wiki`, renders the wiki from
the repository at the tag (`package/wiki.py`: the README, the tutorial,
the guide, the reference, production, the architecture, one row per ADR,
the releases) and pushes it with the workflow's own `GITHUB_TOKEN`. There
is no secret to add. Nobody edits the wiki: every release replaces every
page, and each page links the file it comes from.

- [ ] **Save a first page in the Wiki tab**, once, before the next tag.
  GitHub creates the wiki's git repository only then, and no API does it:
  this repository → Wiki → "Create the first page" → leave the title
  `Home`, any text → Save page. The tag replaces it. Until then, the tag's
  `wiki` job fails and says so; the rest of the release is already done.
- [ ] **Keep "Restrict editing to collaborators only" ticked**: this
  repository → Settings → General → Features → Wikis. It is GitHub's
  default for a public repository.
- [ ] **After the first tag, read the `wiki` job.** GitHub does not
  document whether `GITHUB_TOKEN` may push to a wiki; widely used wiki
  Actions do it with `contents: write`, which the job holds. If the job
  fails with a 403 from `lights-out.wiki.git`, the job needs a token, and
  that is an amendment to ADR 0077, not a setting.

### The README's front page

- [ ] **Record the rollback** (plan §15, phase 0; assessment Horizon 1
  item 3): a terminal recording under 60 seconds of the canary at 5%,
  verify NO-GO, and the return to 100/0 on its own. It goes at the top of
  the README. `./go demo-rollback` is the one-minute path (README, "Shorter
  looks").
- [ ] **Measure the time to a first GO on your own repository** without
  the plane. Target: under 10 minutes (assessment, "Measured before
  launch"). The second measure there, two tickets in three reaching a
  mergeable PR with no human touching the code, is M24's exit criterion
  (plan §14), not a launch-day number.

### GitHub settings

- [ ] **Description and topics**: the repository page → About (the gear).
  Plan §15's topics: `ai-agents`, `coding-agents`, `guardrails`,
  `canary`, `progressive-delivery`, `platform-engineering`, `devsecops`,
  `gemini`, `adk`. The assessment found the topics already set on
  2026-11-03; check them against the list.
- [ ] **Social preview image**: Settings → General → Social preview.
- [ ] **Discussions on**: Settings → General → Features → Discussions.
  Plan §15 says Discussions are enough: no Discord or Slack that 10 hours
  a week cannot staff.
- [ ] **Private vulnerability reporting on**: Settings → the security
  section → Private vulnerability reporting → Enable. `SECURITY.md` and the issue form's
  contact link send reporters to the Security tab's "Report a
  vulnerability" button, which exists only with this on. (From
  `SECURITY.md`, not from the sources above.)
- [ ] **Create the labels, then open the good first issues** from
  [good-first-issues.md](good-first-issues.md): 6–8 issues labelled
  `good first issue` (plan §15, phase 0). That file lists the labels to
  create first.

### The soft launch

- [ ] **Soft launch to 10–20 trusted people** (colleagues, Italian
  communities), on Linux and macOS, with one question: "did `./go demo`
  reach the end on your machine?" (plan §15, phase 0; assessment Horizon
  1 item 5). Fix every friction found before anything public.

## The owner does: on launch day

- [x] **Flip the repository to public** (done 2026-11-04): Settings → General → Danger
  Zone → Change repository visibility → Public. Only then does the
  wrapper's raw-URL download work for strangers (HANDOFF M23).
- [ ] **`./go demo` from a clean clone of the public URL**, once more, on
  the day (plan §15, "Rischi": a quickstart broken on launch day).
- [ ] **One message everywhere** (plan §15, "Il messaggio"): autonomous
  agents that carry a change to production on a codebase nobody
  trusts — numeric gates, a declared perimeter, automatic rollback, one
  human before 100%. The hook is the automatic rollback of a change an
  agent wrote. Say *one* human, *one* gate; never "human in the loop".
- [ ] **No Show HN yet.** Plan §15 puts Show HN, newsletters and the long
  article in phase 2, after the experiment: "the first judgement is the
  one that stays". Launch day for phase 0 is the public repository, the
  soft launch's fixes and the weekly diary (phase 1).
- [ ] **Answer outside issues and PRs within 48 hours** (plan §15, "Il
  ritmo settimanale").

### Product Hunt, the week after Show HN

A second wave, not the launch. Product Hunt's readers are makers and
product people; fewer of them run a DevOps toolchain, and a dev tool there
converts to stars far below Hacker News (the adoption roadmap, section 3.3:
Hacker News exposure is worth about +121 stars in 24 h). A first impression
there happens once, so it waits for the prerequisites.

- [ ] **Prerequisites**: `v0.3.0` tagged and both images signed, the
  package on PyPI, the root Action on the Marketplace, the README's
  recording real, M24's numbers published, Show HN done (plan §15, phase 2).
- [ ] **The "coming soon" page**, any time before: it costs nothing and
  collects followers who are told on launch day.
- [ ] **Launch on a Tuesday or Wednesday at 00:01 Pacific**, the week
  after Show HN. The tagline is the one message in under 60 characters
  (draft: "Autonomous agents to production, behind gates, one human GO").
- [ ] **The gallery from the console** (M33): the kanban, a ticket's
  timeline down to the rollback, the overview; the demo's recording from
  the README. The first comment tells the one human gate, the 15-minute
  demo with no account and no card, and gives `uvx lights-out-gates
  --light` as the ten-second try.
- [ ] **Stay in the comments all day** and answer within the hour: launch
  day costs a day of the owner's time.
- [ ] **Measure** stars and unique visitors over the 48 h after, beside
  Show HN's, in the adoption roadmap's section 8.

## Done in the repository

| Item | Where |
|---|---|
| `CONTRIBUTING.md` with the DCO, `ROADMAP.md` in English, `SECURITY.md` | repository root |
| Issue forms (bug, attach, gate bypass) and a pull request template | `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` |
| ADR 0031 committed (assessment Horizon 1 item 2) | `docs/adr/0031-worker-brief-report-and-durable-tower-documents.md` |
| `.DS_Store` ignored (assessment Horizon 1 item 2) | `.gitignore` |
| ROADMAP follows plan §14 and §16: M18 and M20–M23 done, M24 next | `ROADMAP.md` |
| A local first run of `./go demo` reports its time over the 900 s budget instead of failing; CI and later runs are judged | `go`, `Makefile` (`demo`), ADR 0013 amendment 2026-11-04, ticket 0010 item 6 |
| The `launch` job's "envoy-canary not ready after 180s": the backend now restarts when flagd is not ready at import | `app/compose.yaml`, `workloads/minimal/.lights-out/compose.yaml`, ticket 0010 item 7 |
| Ticket 0010, the pre-launch audit's hardening items, closed | `tickets/0010.md` |
| Eight good-first-issue drafts from real findings | [good-first-issues.md](good-first-issues.md) |
| The README states what is real, recorded, hand-written and not yet published | `README.md`, "What is real and what is recorded" |
| The image and the Action built and tested on every push; `publish` only on a `v*` tag or a dispatch | `.github/workflows/package.yml` |
| The release on a `v*` tag: the wheel checked, the images signed and verified, PyPI by trusted publishing, the two Action repositories synced and tagged, three draft releases; a dispatch is a dry run (ADR 0075) | `.github/workflows/package.yml`, `package/sync-action.sh`, `package/release-notes.sh`, `ci/release.sh` |
| The wiki rendered from the repository at the tag and pushed by the release; a dispatch renders and lists the pages (ADR 0077) | `.github/workflows/package.yml`, `package/wiki.py`, `package/sync-wiki.sh`, `ci/wiki.sh` |
| The nightly against GitHub, skipping with a notice while its variable is unset | `.github/workflows/nightly-github.yml` |
