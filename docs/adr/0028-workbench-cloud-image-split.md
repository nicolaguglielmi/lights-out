# ADR 0028 — gcloud and terraform leave the base workbench for `workbench/cloud/Containerfile`, built `FROM` it; the profile says which flavour it needs and `./go` reads that one line

- Status: superseded by ADR 0057 (reconciled at 0.9.0: `workbench/cloud/` and `WORKBENCH_FLAVOUR` were never built; `workbench/Containerfile` consolidated image stages under ADR 0057; originally: amends ADR 0004 §1 [the tool list of the base image loses gcloud and terraform] and its first consequence [the 1 GB image that "serves nobody before M8" is answered here]; consistent with ADR 0012 §3 [the runner image is already built `FROM` the workbench image]); amended by ADR 0057 [never built until M31: gcloud and terraform leave the base now, the cloud image and `WORKBENCH_FLAVOUR` are written with L2]
- Date: 2026-09-20
- Milestone: M8

## Context

ADR 0004 §1 put every tool in one image and its consequences accepted the
cost in one sentence: "the image is 1 GB for a repo that is empty at M0;
gcloud and terraform serve nobody before M8". M7 close measured it:
workbench 1.42 GB, of which gcloud 368 MB and terraform 106 MB — 474 MB
(provenance: measurement, plan §7 M7 closing note, 2026-09-16) that no
script has used for nine milestones. The owner's ruling in that same note
was to split them out in M8, "la milestone dove i due strumenti servono per
la prima volta", with the ADR that amends 0004. M8 also adds cosign to the
base image (ADR 0025 §2), so the base grows on the same day it shrinks.

## Decision

1. **Two images, one lineage.** `workbench/Containerfile` keeps everything
   every level needs, minus gcloud and terraform, plus cosign.
   `workbench/cloud/Containerfile` is `FROM ${WORKBENCH_IMAGE}` and adds
   gcloud and terraform with the same per-arch pinned `ARG` sha256 pattern
   and the same `fetch.sh`. This is exactly how `adapters/gitea/runner/
   Containerfile` is already built (ADR 0012 §3), so there is no new shape
   to learn.
2. **The profile decides, `./go` reads it.** `profiles/l2.env` and
   `profiles/l3.env` set `WORKBENCH_FLAVOUR=cloud`; `l0.env` and `l1.env`
   say nothing. `./go` extracts that one line from
   `profiles/$PROFILE.env` with `sed` and, when it is `cloud`, builds (or
   reuses) the base image first and then the cloud image on top, tagging
   each by the cksum of its own inputs as it already does. Two `sed`-lines
   in `./go`; no level name is hardcoded in it and no new concept enters
   the Makefile, which keeps reading the profile as before.
3. **cosign stays in the base.** Signing and verification happen at every
   level, first at L0 (ADR 0022 §3), and the Gitea runner image — built
   `FROM` the base — is what signs at L0 (ADR 0025 §4).

## Alternatives rejected

- **Leave it as it is.** 474 MB of download on every clone for two tools an
  L0 user never runs, against a 6-minute build budget (ADR 0004 §3) that
  M7 was already stretching.
- **Pull gcloud and terraform at first use, into a volume.** An install
  step that runs outside a Containerfile has no pinned sha256 and no
  reproducible layer; ADR 0004 §1's per-arch hash rule is the thing that
  makes the toolchain trustworthy.
- **A third image for the runner.** It is already built from the base and
  needs nothing from the cloud layer.
- **Selecting the flavour with an environment variable on `./go`** (e.g.
  `WORKBENCH=cloud`): a second place to be wrong about, and it would let
  `PROFILE=l2` run without gcloud until the first command failed deep
  inside Terraform.
- **`gcloud` from the official `google-cloud-cli` image as the cloud base**
  instead of layering on workbench: ADR 0004 already rejected it (1 GB+
  before anything is added), and it would fork the toolchain in two.

## Consequences

- An L2 user builds two images and waits longer on the first
  `PROFILE=l2` run than they do today; an L0 user — the golden rule's
  reader — downloads 474 MB less and gets cosign instead.
- Two Containerfiles now carry pinned per-arch hashes to bump by hand, and
  the cloud one must be bumped in the same commit as the base when the base
  digest moves, or the layer rebuilds against a different distro. ADR
  0004's "seven tools, fourteen hashes, bumped by hand" becomes two files.
  This is the consequence that will hurt.
- Any future `ci` check that needs terraform or gcloud can only run at
  `PROFILE=l2`; ADR 0027 §2 puts `fmt -check` and `validate` inside
  `up PROFILE=l2` for that reason.
- ADR 0004's 6-minute build budget is re-measured at M8 close for both
  images and recorded in HANDOFF.

## Golden-rule check

This is the only decision in M8 that makes the cloner's path *shorter*:
the first `./go demo` downloads 474 MB less, minus whatever cosign adds
back. Nothing about `PROFILE=l0` changes otherwise. Passes.

## Amendment 2026-11-08 (ADR 0057)

**Context.** The split decided here in M8 was never built: M28's drift
check found `workbench/Containerfile` still fetching gcloud and terraform,
no `workbench/cloud/Containerfile`, and no `WORKBENCH_FLAVOUR` in any
profile or in `./go`. On 2026-11-08 nothing in the repository calls either
tool (git grep), and every published lights-out image (ADR 0045) carries
both.

**Decision.** gcloud and terraform leave the base now, with `unzip` (which
served terraform only), their `ARG`s and the `CLOUDSDK_*` environment,
without the cloud image. §1's cloud image and §2's `WORKBENCH_FLAVOUR` are
written the day L2 is, not before: an image no command uses has nothing to
prove it builds. The base becomes two stages of one file (ADR 0057 §2).

**Measure.** The full image, unpacked on amd64: 1.77 GB before (ADR
0045's measure), 1,246 MB after (1,246,550,245 bytes, `docker image
inspect`, package.yml run 42519069964 on 0a0d073), 524 MB less; 65 MB of
that is node's C headers, dropped in the same milestone (ADR 0057 §2). The
workbench under podman on arm64: 1.81 GB before, 1.30 GB after (local,
2026-11-08).

**Consequences.** Until L2 is written, `PROFILE=l2` has no gcloud or
terraform anywhere; `./go doctor --level l2` says so (ADR 0057 §8). This
ADR's hurting consequence (two Containerfiles of hashes) is deferred, not
gone; ADR 0057 open point 3 asks whether the cloud flavour becomes a third
stage instead.
