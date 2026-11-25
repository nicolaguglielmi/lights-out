# ADR 0027 — L2 at M8 is the release path only: one flat Terraform root with local state, Cloud Deploy driving a custom-automated canary at 5 % and 25 % with its own verify job *off* because `verify/slo.sh` decides at every level, cosign with a KMS key as the port's check and Binary Authorization as the platform's second lock, per-role service accounts instead of per-role secrets, and the tower's L2 adapters deferred to the milestone that runs it there

- Status: accepted (reconciled at 0.9.0: L2/L3 GCP adapters shipped under ADR 0128, while Terraform provisioning and live GCP project apply remain unexecuted; amends plan §7 M8 ["Cloud Deploy con le fasi canary e il verify job" — the verify job is ours, not Cloud Deploy's; "Secret Manager con credenziali per ruolo" — identities, not keys] and §3's `Registry` row; ADR 0022 §3 [at L2 the port's check is cosign against a KMS key; Binary Authorization is the platform's second lock, not the implementation of the rule]; ADR 0019 §3 [the `pubsub` and `firestore` verbs move to the milestone that runs the tower on GCP]; ADR 0024 §2 [`cloud-deploy` passes ADR 0026's suite]). Amended 2026-09-20 by owner ruling, twice: §5 — Binary Authorization's own refusal becomes an executable case (`gates/unsigned-refused.sh`), not a one-off manual proof; §2 — a `google_billing_budget` exists from the first `terraform apply`, not from M10's nightly.
- Date: 2026-09-20
- Milestone: M8

## Context

Plan §7 M8 lists Terraform for the project, Artifact Registry, Cloud Run,
Cloud Deploy with canary phases and the verify job, Cloud Monitoring,
Binary Authorization, Workload Identity Federation from GitHub and Secret
Manager, and is done when a new project goes from zero to the first
automatic rollback in under an hour with `gates/` and `verify/` identical
to L0. Plan §10 item 5 flags Cloud Deploy as unconfirmed since
plan-writing time.

Verified on 2026-09-20. Cloud Deploy canary is supported for Cloud Run
**services** and worker pools, not jobs; `customCanaryDeployment.phaseConfigs`
takes named phases with percentages, an optional `verify: true` per phase,
and an implicit `stable` phase at 100 %; phases advance with
`gcloud deploy rollouts advance`, or by an automation resource; **canary
phases are skipped on the first deployment to a target**; a release needs a
`skaffold.yaml` even for Cloud Run. Binary Authorization's *sigstore
signature check* — the thing that would have consumed an L0-shaped cosign
signature unchanged — is **continuous validation only, Artifact Registry
and GKE**; deploy-time enforcement for Cloud Run is the older model:
attestors and attestations over Artifact Analysis notes, signed with a
PKIX/KMS key. `google-github-actions/auth` is at v3.

## Decision

1. **Scope: M8 builds L2's release path, not L2's ticket path.** The
   done-when is `up → deploy → 5 % → verify → rollback` plus the hash test,
   and ADR 0026's suite at L2. In full: `artifact-registry`,
   `cloud-deploy`, `managed-prometheus`, and `flagd` on Cloud Run. Every
   other adapter `profiles/l2.env` names gets a real `ready.sh` (so `make
   up`'s loop is honest and ADR 0012 §6's "a missing file is an error"
   still catches a forgotten adapter) and nothing else; `pubsub`,
   `firestore`, `github` and `github-issues` verbs land with the milestone
   that runs the tower on GCP — M10's nightly is the first thing that needs
   them. `./go e2e PROFILE=l2` fails naming the missing verb, and the
   README says so. Not a mock: a readiness probe against a real service is
   real, and nothing claims the verb exists.
2. **One flat Terraform root, local state, and a `destroy` target.**
   `infra/` gets `versions.tf` (pinned provider), `variables.tf`,
   `outputs.tf` and one file per area — `project.tf` (APIs and the billing
   budget), `registry.tf` (Artifact Registry, the KMS keyring and key, the
   Binary Authorization attestor), `run.tf`, `deploy.tf`, `monitoring.tf`,
   `sql.tf`, `iam.tf` (service accounts, Workload Identity Federation,
   Secret Manager). No modules, no workspaces, no remote backend: one
   disposable project (plan §11) run by one person. State stays under
   `infra/`, gitignored, and it contains the database password.
   `./go up PROFILE=l2` runs `terraform fmt -check`, `validate`, `apply`,
   then the first release (point 4) and the readiness loop;
   `./go down PROFILE=l2` is `terraform apply -var sql_running=false`,
   which stops the one resource that bills while idle (ADR 0022 §4) and
   keeps the state honest instead of drifting; `./go destroy PROFILE=l2` is
   `terraform destroy`. `infra/README.md` gains the sentence that matters:
   lose the state and the way back is deleting the project, which is why
   the project is disposable.

   *Amended 2026-09-20, owner ruling:* the budget alert is not something
   M10's nightly adds later — it exists from the first `terraform apply`.
   `project.tf` enables `billingbudgets.googleapis.com` and declares one
   `google_billing_budget` scoped to this project through
   `budget_filter.projects`, with `var.billing_account` (no default:
   `apply` fails naming the variable rather than building a project with
   nobody watching the bill; the value comes from `.env` as
   `TF_VAR_billing_account`, like the project id, and `.env.example` gets a
   fake one), `amount.specified_amount` of `var.budget_amount` — default
   **50 USD per month** — and threshold rules at **50 %** and **100 %**.
   All three numbers are a **decision**, not a measurement and not an
   external constraint: M8 is the first milestone that spends anything and
   no L2 run has been measured yet. They are the operator's to change in
   `terraform.tfvars`, nothing in the repo reads them back, and no
   acceptance criterion depends on them — do not treat them as
   load-bearing. A budget *alerts*, it does not cap: mail goes to the
   billing account's admins (no notification-channel resource, one
   resource total), and what stops the spend is still
   `./go down PROFILE=l2` and a disposable project. Ordering is explicit,
   because a budget created after the bill starts is theatre: `sql.tf`'s
   instance — the one resource that bills while idle — declares
   `depends_on` the budget, so an operator without
   `roles/billing.costsManager` on the billing account fails *before* Cloud
   SQL exists, not after.
3. **Cloud Deploy manages revisions and traffic; `core/launchpad.sh` still
   decides.** The pipeline is `customCanaryDeployment` with phases
   `canary-5` at 5 % and `canary-25` at 25 % and the implicit `stable`
   phase — the percentages are a constraint, not a choice: they must equal
   `core/launchpad.sh`'s stage list (ADR 0013 §4), and `shift.sh <pct>`
   NO-GOs naming the pipeline file when asked for a percentage that has no
   phase. **Cloud Deploy's own `verify` stanza stays off.** One PromQL file
   decides at every level (plan §3, plan §11's divergence risk, M8's hash
   test); enabling Cloud Deploy's verify job as well would give a release
   two verdicts and the demo would lie the first time they disagreed. This
   is the amendment to plan §7 M8's wording that must not be missed: the
   verify job exists, and it is ours. Verb mapping, all inside the adapter:
   `deploy.sh <version> <image>` = `gcloud deploy releases create` with
   `--images` pinned to the verified digest and `APP_VERSION=<version>` in
   the rendered service, which lands at `canary-5`; `shift.sh <pct>` =
   `gcloud deploy rollouts advance --phase-id`; `shift.sh 100` = advance to
   `stable`; `rollback.sh` = `gcloud deploy targets rollback`, idempotent
   by re-reading the current rollout first; `status.sh` = the Cloud Run
   service's traffic split and the two revisions' `APP_VERSION`, printed as
   ADR 0026 §2's three keys. `skaffold.yaml` and the Cloud Run service
   manifest live in `adapters/cloud-deploy/`, with the rest of the vendor's
   config (plan §6).
4. **`./go up PROFILE=l2` deploys once before anything else.** Cloud Deploy
   skips canary phases on the first deployment to a target, so a fresh
   project's first release would go straight to 100 % and the suite's first
   case would prove nothing. `up` creates the service with the stable
   image and only then reports ready. Found by reading the docs, not by
   debugging a talk.
5. **Signing at L2: the same cosign verb, a different key; Binary
   Authorization is a second lock.** `adapters/artifact-registry/
   publish.sh` builds with Cloud Build, signs with
   `cosign sign --key gcpkms://…`, and creates the Binary Authorization
   attestation with the same key version; `verify.sh` is
   `cosign verify --key gcpkms://…`, the same command the launchpad runs at
   L0 with a file key. Binary Authorization enforces at admission on the
   Cloud Run service, so an image that reached the project unsigned is
   refused twice, by two mechanisms, with two messages — and the one the
   demo shows, `NO-GO: launchpad — unsigned image`, is identical at every
   level.

   *Amended 2026-09-20, owner ruling:* this ADR used to say that Binary
   Authorization's own refusal was "proven once by hand at M8 close and
   recorded in HANDOFF, not wired into the suite". Overturned: a guarantee
   as load-bearing as "nothing reaches production without a signature" is
   not supported by a manual check somebody does once. It becomes an
   executable case — `gates/unsigned-refused.sh`, run as
   `./go unsigned-refused PROFILE=l2`, part of M8's L2 acceptance beside
   `./go delivery-conformance PROFILE=l2`. Its own gate, not a sixth case
   of ADR 0026's suite: that suite is the *port's* contract, level-agnostic
   and driven only through `core/` (ADR 0024 §2), while this case must
   bypass `core/` on purpose and would be skipped forever by a third
   Delivery adapter. Two guards, both instant one-line NO-GOs so
   `ci/contract.sh`'s cold run is unaffected: `CONFORMANCE=1` (it drives
   the real plane — ADR 0026 §4's reason and wording) and
   `REGISTRY = artifact-registry`, the profile line that says attestations
   are made here; at L0 the gate answers `NO-GO: unsigned-refused — Binary
   Authorization exists only at L2 (./go unsigned-refused PROFILE=l2)` and
   stops. What it does, in order:
   1. *Positive control.* `verify.sh sha-$(git rev-parse HEAD)` must accept
      the image `up` published and print its digest. A `verify.sh` that
      refuses everything would make every step below meaningless.
   2. *The probe.* One image whose only difference from that digest is a
      label: a generated `FROM <digest>` + `LABEL`, built and pushed by
      `gcloud builds submit --tag <ar-repo>:probe-unsigned`, straight into
      Artifact Registry, never through `publish.sh` — therefore neither
      cosign-signed nor attested. A *new digest* is the whole point: both a
      cosign signature and a BinAuthz attestation travel with the digest,
      so re-tagging the stable image would be admitted and would prove
      nothing. A trap on EXIT, INT and TERM deletes the tag and the digest;
      the tag is fixed, so the cleanup is idempotent.
   3. *The port's lock.* `make release CANARY_VERSION=<status.sh's stable>
      CANARY_TAG=probe-unsigned`: expect exit 2 (`make`'s own code for a
      failed recipe, not `core/launchpad.sh`'s own 1 — verified 2026-09-20
      building ADR 0026's case 5), stdout exactly
      `NO-GO: launchpad — unsigned image`, `status.sh` unchanged in all
      three keys. This is ADR 0026 case 5 with a real unsigned image in
      place of an absent tag, which is the half the level-agnostic case
      cannot reach.
   4. *The platform's lock.* The same digest put in front of Cloud Run by
      the one path that never touches `core/` and never calls `verify.sh`:
      `gcloud run deploy <service> --image <probe digest> --no-traffic`
      (verified 2026-09-20: `--no-traffic` creates the revision and leaves
      the existing split alone, so even a failure of this test cannot serve
      the image). It must fail **and say why**: an admission denial naming
      the policy — "not authorized by policy", "Denied by attestation
      authority" — and the implementer pins the string actually seen, in a
      comment next to the match. A deploy that fails for any other reason
      is `NO-GO: unsigned-refused — the deploy failed, but not on Binary
      Authorization: <first stderr line>`: a false red that names the real
      cause, never a green. A deploy that *succeeds* deletes the revision
      it should not have been able to create, then NO-GOs.
   5. `status.sh` unchanged one last time, then `GO: unsigned-refused`.

   The file sits in `gates/`, so the hash test treats it like any other:
   one file, identical at both levels, that only *runs* at L2. That is not
   a capability living only at L2 (ADR 0022 §2) — the capability, refusing
   an unsigned image, is the launchpad's at every level; BinAuthz is the
   second lock, and this gate is what keeps it from being decorative.
   Projected 2–3 minutes at L2, most of it Cloud Build; measured at M8
   close.
6. **Secret Manager holds one secret; per-role means per-service-account.**
   The app's database password is a real secret and Cloud Run reads it from
   Secret Manager. The six roles get one service account each with the
   minimum roles; at L2 the model is reached by identity, not by an API
   key, so a per-role *secret* would be an empty box with a name. Plan §7
   M8's "Secret Manager con credenziali per ruolo" is amended to say that.
7. **Workload Identity Federation is created at M8 and first used at M10.**
   `iam.tf` creates the pool, the GitHub provider with a repository-scoped
   attribute condition, and the binding; `terraform output` prints the
   provider resource name and `infra/README.md` carries the four-line
   workflow snippet (`permissions: id-token: write`,
   `google-github-actions/auth@v3`, the provider, the service account).
   The first consumer is the nightly on a disposable project. Declared in
   the README as infrastructure without a consumer until then; the
   alternative — a GitHub job that authenticates to prove it — needs a GCP
   project bound to this repo, which no cloner has.
8. **Flags at L2: flagd on Cloud Run, its file in a GCS object.** There is
   no bind mount on Cloud Run, and baking the flag file into an image would
   turn the kill switch (plan §8 step 9, "resolved in seconds, no deploy")
   into a deploy. flagd polls the object's HTTPS URL; `adapters/flagd/
   set.sh` writes the file at L0 and the object at L2, which is the seam
   ADR 0026 §3 opened. The poll interval is what "in seconds" means at L2
   and the README states the measured value.

## Alternatives rejected

- **Let Cloud Deploy's verify job run `verify/slo.sh` in Cloud Build.** The
  same file would run, but the *controller* would differ per level and
  `core/launchpad.sh` would become a wrapper at L2 — the divergence plan
  §11 lists as the risk that turns the repo into demo-ware.
- **Cloud Deploy automations for auto-advance.** They would move the 5 % →
  25 % decision from the launchpad into the vendor, for the same reason.
- **Binary Authorization's sigstore signature check as the L2
  implementation of ADR 0022 §3.** Verified 2026-09-20: continuous
  validation only, GKE and Artifact Registry. Using it would mean the L2
  check runs *after* the traffic, which is not a gate.
- **Attestations only, dropping cosign at L2.** Then L0 and L2 verify
  different artefacts with different commands and "one rule, two adapters"
  becomes a slide, not a fact.
- **Proving Binary Authorization's refusal by hand, once, at M8 close**
  (this ADR's own §5 until the 2026-09-20 ruling). A one-off manual check
  is skipped the third time and cannot be run by the nightly; it does not
  support the sentence it is there to support.
- **A sixth case in `gates/delivery-conformance.sh` guarded by
  `DELIVERY = cloud-deploy`.** A vendor name used as a guard inside the one
  suite that exists to be level-agnostic, for a case that cannot be driven
  through `core/` and that a third Delivery adapter would skip forever.
- **Deploying a public image (`…/cloudrun/container/hello`) instead of
  building a probe.** No build, but it proves nothing about *our* artefact,
  it cannot also exercise `verify.sh`, and an admission-whitelist entry for
  a Google-published image would turn the gate green for the wrong reason.
- **Making the budget optional** (`count = var.billing_account == "" ? 0 :
  1`). A budget nobody notices is missing is the failure mode the ruling is
  about; a required variable fails loudly on the first apply instead.
- **A remote GCS backend for Terraform state.** A bucket to create before
  `terraform init`, for a single-operator disposable project whose ultimate
  cleanup is deleting the project.
- **Terraform modules, or one root per environment.** One environment
  exists; a module with one caller is indirection.
- **A `ci/terraform.sh` gate.** It would need terraform in the base
  workbench image, which ADR 0028 takes out; `fmt -check` and `validate`
  run inside `up PROFILE=l2`, where the files are about to be applied.
- **GKE at M8.** Plan §3 puts the GKE option at L3, and Cloud Run is the
  target whose traffic split the canary strategy was verified against.
- **Building the L2 tower adapters now** (`pubsub`, `firestore`, `github`):
  four adapters with no acceptance criterion in this milestone, in the
  milestone that is already the largest in the plan.

## Consequences

- M8 closes with GCP infrastructure that nobody exercises daily: WIF and
  the per-role service accounts each have exactly one proof, run by hand.
  The nightly (M10) is what turns them into tested code, and until then a
  vendor API change breaks them silently. Binary Authorization left that
  list on 2026-09-20: `gates/unsigned-refused.sh` is a command, so the
  nightly runs it and a vendor change is a red gate rather than a silence.
- The canary percentages now live in two places — `core/launchpad.sh`'s
  stage loop and the adapter's pipeline YAML. `shift.sh`'s loud NO-GO is
  the only thing that catches a drift. This is the consequence that will
  hurt.
- `gates/unsigned-refused.sh` costs a Cloud Build run and one refused
  deploy per invocation, and it asserts on a vendor's error text: the day
  Google rewords the denial, a working lock reads as a broken one. The
  false verdict is red, not green, and the fix is one string.
- Creating the budget needs a permission the rest of `infra/` does not:
  `google_billing_budget` is a billing-account operation
  (`roles/billing.costsManager`), not a project one, so an operator who is
  only owner of the project cannot `terraform apply` at all. Deliberate —
  the alternative is a project that bills with nobody watching — and
  `infra/README.md` states the role next to the Cloud SQL cost line.
- Local Terraform state holding a database password is one `rm -rf` away
  from a Cloud SQL instance nobody can find; `infra/README.md` and
  `./go down PROFILE=l2` are the mitigations, and the disposable project is
  the backstop.
- `profiles/l2.env` names four adapters whose verbs do not exist; the
  README's L2 section has to say which commands work at L2 at M8, and the
  list is short.
- Every L2 run costs money while Cloud SQL is up. ADR 0026's suite is the
  longest-running L2 command and the one most likely to be abandoned
  half-way; its trap restores flags but not billing.

## Golden-rule check

Nothing in this ADR touches the path a cloner takes: L2 is reached only by
`PROFILE=l2`, which needs an account, and every command in `./go demo`
still runs at L0 with no key. The one thing that crosses levels is the
cosign verb and the PromQL file, which is the point. `gates/unsigned-
refused.sh` is a file the cloner has and never runs: at L0 it is one line
and an exit 1 that names the level. The budget reads `TF_VAR_billing_
account` from `.env`, where `.env.example` carries a fake id and no cloner
needs either. `infra/README.md` opens with "everything below is optional"
and the Cloud SQL cost line (ADR 0022 §5). Passes.
