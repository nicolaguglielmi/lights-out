# ADR 0096 — Project hygiene: a code of conduct, the governance written down, Scorecard, signed release files

- Status: proposed (M63)
- Date: 2026-11-16
- Milestone: M63 (docs/PLAN.md, "Sprint 2026-11-16 (M58–M63)")

## Context

lights-out asks people to let agents loose on their code, and a
newcomer judges that request partly by the project's public files. On
2026-11-16 those files had these gaps:

- **No code of conduct.** GitHub's community profile marks it missing.
- **No `MAINTAINERS.md` and no `GOVERNANCE.md`.** Who decides, and how
  someone else could join, is written nowhere. In practice one person
  decides everything.
- **`SECURITY.md` points to a route that does not exist.** It sends
  reporters to the Security tab's "Report a vulnerability". GitHub's
  private vulnerability reporting is off on the repository
  (`gh api repos/nicolaguglielmi/lights-out/private-vulnerability-reporting`
  answers `enabled: false`), so there is no such button. The file also
  gives no response time.
- **The supply-chain practices are not measured where people look.**
  Actions are pinned by SHA, every workflow sets `permissions:`,
  Dependabot runs, and the images are signed keyless (ADR 0057 §4,
  ADR 0075). No OpenSSF Scorecard result exists for the repository,
  because the Action does not run and the weekly public crawl does not
  include it.
- **The GitHub releases carry no files.** The images are signed in the
  registry, but the release pages have no assets and no signatures.
  Scorecard's Signed-Releases check reads release assets only.

Three fixes are repository settings, which only the owner changes:
turning private vulnerability reporting on, a ruleset on `main`, and
registering on bestpractices.dev. This ADR does not change them. It
writes the files that do not need the owner, and the owner's steps go in
the milestone's handoff.

## Decision

1. **`CODE_OF_CONDUCT.md` adopts the CNCF Community Code of Conduct
   v1.3 by reference.** The file links the text and does not copy it.
   - It replaces only the text's "Reporting" section. That section names
     the CNCF committees, which do not handle reports for a project
     outside the CNCF.
   - **No email address**, the owner's decision of 2026-11-16: reports go
     through GitHub. A reporter opens an issue titled "conduct contact
     request" with no details, and a maintainer answers there with a
     private way to talk. The escalation is GitHub's own reporting
     (Report content, then Report abuse to GitHub Support, or the abuse
     form), which does not pass through the project. It is the route for
     a report about a maintainer, too.
   - GitHub's "Reported content" setting, which sends Report content to
     the repository's admins, exists only for repositories owned by an
     organization (Settings → Access → Moderation options → Reported
     content). This repository is owned by a personal account: on
     2026-11-16 its Moderation settings list only "Interaction limits"
     and "Code review limits". The file names only what exists.
   - The first answer comes within 14 days. Provenance: a decision, the
     same bound as §4, so the project makes one promise.
   - A maintainer who is the subject of a report takes no part in
     handling it.

2. **`MAINTAINERS.md`** is a table: Name, GitHub, Organization, Area.
   Today it has one row: Nicola, @nicolaguglielmi, Independent,
   everything. Below the table it defines three roles, contributor,
   reviewer and maintainer, and how someone moves from one to the next.
   The criteria are judgments ("a review the maintainers already find
   useful"), not counts: with one maintainer and no outside contributors
   yet, there is no measurement to set a count from. Two numbers appear:
   - a maintainer inactive for six months is asked whether they stay,
     and moves to emeritus after a month with no answer. Provenance: a
     decision, sized to a project maintained in spare time;
   - a new maintainer turns on two-factor authentication before getting
     write access. This is not a number, and it is not negotiable.

3. **`GOVERNANCE.md`**, short:
   - **Today the owner decides**, stated plainly: what merges, what is
     released, which ADRs are accepted, and who joins. What does not
     depend on one person is what the gates and `./go ci` already check.
   - **With two or more maintainers:**
     - a pull request needs the approval of a maintainer who is not its
       author;
     - other decisions are made by lazy consensus, accepted when no
       maintainer objects within seven days;
     - an objection that discussion does not settle goes to a vote:
       simple majority, and a tie keeps the status quo;
     - adding or removing a maintainer or a reviewer is a majority vote
       without the person concerned.
     - Provenance of the seven days: a decision, one week, so that a
       maintainer working in spare time sees the proposal.
   - **The direction does not depend on any model vendor.** Every
     provider and harness sits behind the same port and gates (ADR 0040,
     ADR 0041), and the demo needs no key. No vendor holds a seat, a
     vote or a veto.
   - **Changing the document:** a pull request. Today the owner decides.
     With two or more maintainers it needs two thirds of them and stays
     open fourteen days. Provenance: a decision, twice the seven days of
     an ordinary decision, because this one changes how every other is
     made.
   - The document holds no plans and no roadmap. It says how decisions
     are made, not which ones will be.

4. **`SECURITY.md`** gains a response time and a fallback route:
   - **First response within 14 days.** Provenance: an external
     constraint. The OpenSSF Best Practices "passing" criterion
     `vulnerability_report_response` asks for a response within 14 days
     to reports received in the last six months.
   - **Who handles reports:** the maintainers in `MAINTAINERS.md`, today
     one person.
   - **One channel:** GitHub's private vulnerability reporting, and no
     email address (the owner's decision, 2026-11-16).
   - **Until the owner turns private reporting on**, the file says what
     to do when the button is missing. The reporter opens an issue
     titled "security contact request" with no details. The maintainer
     then opens a draft security advisory, which GitHub lets a
     maintainer create whatever the setting, and adds the reporter to
     it. The route needs no address, and the details never leave GitHub's
     private advisories. Once the setting is on, the fallback paragraph
     can stay: it does no harm.
   - The supported-versions line now names the latest release, because
     releases exist since 0.1.0.

5. **OpenSSF Scorecard: `.github/workflows/scorecard.yml`.**
   - **The Action** is `ossf/scorecard-action` v2.5.0, pinned by SHA,
     with `publish_results: true`. The results go to api.scorecard.dev,
     where the README's badge reads them, and as SARIF to code scanning
     and to a five-day artifact (the Action's example).
   - **Triggers:** a push to `main`, a weekly schedule, and
     `branch_protection_rule`. Scorecard v2.5.0 refuses a push to any
     other branch: `options.go` returns "only default branch is
     supported" unless the event is a pull request. So the file also
     runs on a pull request into `main`, where the checks run and
     nothing is published. A branch proves the workflow that way. The
     weekly schedule (Monday 04:17 UTC) is a decision: the checks read
     the repository's settings and dependencies, which change without
     a push.
   - **Scorecard's workflow restrictions** hold: no env or defaults, no
     write permission at the top, `id-token: write` in that one job,
     and only checkout, upload-artifact, upload-sarif and the Action as
     steps. The job keeps the `github.com` guard (ADR 0039). That guard
     is an `if`, which the restrictions do not forbid.
   - **`ci/package.sh` allows that `id-token: write`.** Until now only
     package.yml's `publish` and `pypi`, and a Pages deploy, could hold
     it. The exception is narrow: the file must be
     `.github/workflows/scorecard.yml`, and the job must run the
     Scorecard Action. Two planted defects prove the check can still
     fail: the job without the Action, and the same job copied into
     another workflow.

6. **Signed release files, for Signed-Releases.**
   - **The wheel and the sdist** that `dist` builds are signed by
     package.yml's `publish` job. That job already holds `id-token:
     write` and cosign at the workbench's pin. It runs `cosign sign-blob
     --bundle <file>.sigstore.json`, keyless, so the signer is the
     workflow at the tag, as for the images. Then it runs `cosign
     verify-blob` with the exact identity and GitHub's issuer, and hands
     the four files to `release` as an artifact.
   - **`release`** attaches them when it creates the draft. A draft
     that exists is still left alone (ADR 0075 §4).
   - Both steps run only when not `dry_run`, and the release only at a
     tag. A dry run prints the commands.
   - `.sigstore.json` is one of the names Scorecard reads
     (`probes/releasesAreSigned`: `.asc`, `.minisig`, `.sig`, `.sign`,
     `.sigstore`, `.sigstore.json`). A release with signatures scores 8
     out of 10. Releases with no assets are ignored, so until the first
     tag after 0.7.0 the check stays inconclusive.
   - `ci/release.sh` holds it with five planted defects:
     - no `sign-blob`;
     - a bundle named other than `.sigstore.json`;
     - no `verify-blob`;
     - signing in a dry run;
     - a release created with nothing attached.
   - **SLSA provenance (`*.intoto.jsonl`, 10 out of 10) is not in this
     ADR**, and this is the plan for it. The generator
     (`slsa-framework/slsa-github-generator`, latest v2.1.0, February
     2025) is a reusable workflow. Its README requires a reference by
     exact tag, `@vX.Y.Z`, never by SHA, because `slsa-verifier` checks
     that ref. Taking it means:
     - a written exception to "every action pinned by SHA";
     - a job with `id-token: write`, `contents: write` and
       `actions: read`, which `ci/package.sh` must allow by name;
     - a hash list of the release files handed between jobs.

     That is a change of its own, with its own ADR, if the two points
     matter more than the exception.

7. **What the owner does, not this ADR.** Three steps change repository
   settings or an outside account:
   - private vulnerability reporting on;
   - a ruleset on `main` with required checks and no force-push;
   - the project registered on bestpractices.dev, with the passing
     criteria filled.

   M63 prepares them and does none of them.

## Tests, from this ADR

- `ci/lint.sh` (actionlint) runs over `scorecard.yml` like every other
  workflow.
- `ci/package.sh`:
  - the real workflows pass, `scorecard.yml` included;
  - a job in `scorecard.yml` without the Scorecard Action that holds
    `id-token: write` is refused;
  - the Scorecard job copied into another workflow is refused.
- `ci/release.sh`:
  - the real `package.yml` passes;
  - each of §6's five planted defects is refused with its reason;
  - a `cosign verify-blob` alone does not satisfy ADR 0075's `cosign
    verify` of the images. Planting "publish does not verify" on the
    image step must still fail.
- `ci/docs-drift.sh` checks every path the new files cite.
- On GitHub: the pull request's `scorecard` run is green and uploads its
  SARIF. The first push to `main` publishes the result, and the badge
  shows a score.

## Alternatives rejected

- **Copy the CNCF Code of Conduct's text into the file.** The text's
  reporting section names committees that do not handle this project's
  reports. A copy would either keep wrong addresses or be an edited
  copy of someone else's text. A reference to the maintained text, with
  this project's reporting section, is shorter and stays current.
- **The Contributor Covenant directly.** The CNCF text is the
  Contributor Covenant 2.0 adapted, and it is maintained in more than
  twenty languages.
- **An email address**, for security or conduct reports. The owner
  chose GitHub channels only. GitHub's private vulnerability reporting
  and draft advisories keep a security report in one private place,
  with no address to publish or to watch. For conduct, a minimal public
  request for a private channel and GitHub's own reporting cover the
  same ground.
- **Scorecard on every branch push.** The Action refuses it. Running the
  Scorecard CLI in `./go ci` would need the network and a GitHub token,
  and `./go ci` runs offline at L0.
- **`publish_results: false`.** Nothing would be published, so there
  would be no badge and no public result. Those are what the check is
  for.
- **Sign the files in the `release` job.** That would be a second job
  with `id-token: write`, and a second fetch of cosign. `publish`
  already holds both.
- **GitHub artifact attestations** (`actions/attest-build-provenance`).
  They are stored in GitHub's attestation API, not as release assets,
  and Scorecard's Signed-Releases check does not read them.

## Consequences

- A newcomer finds a code of conduct, who decides and how to join, and
  a security route that works today.
- The governance says plainly that one person decides today. That is
  honest, and it is also the first thing a would-be co-maintainer
  reads.
- Scorecard's score is public and moves with every push to `main`.
  Several checks will stay low and need nothing from this ADR to
  explain them:
  - Code-Review and Contributors, with one author;
  - Branch-Protection, until the owner's ruleset;
  - CII-Best-Practices, until the owner's registration;
  - Fuzzing.
- The next tag's GitHub release carries the wheel, the sdist and their
  Sigstore bundles, verifiable with `cosign verify-blob`
  (`docs/reference.md`, "Signed keyless").
- `ci/package.sh` admits a third holder of `id-token: write`.
