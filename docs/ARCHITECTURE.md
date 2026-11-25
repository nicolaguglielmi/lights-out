# Architecture

## Ports and Adapters

The core knows no vendor. Every integration sits behind a port with at least two implementations.

| Port | Adapter | Note |
|---|---|---|
| `Workload` | `app`, any repository attached with `./go attach <path>` (`workloads/minimal` is one), or the gates half alone, drafted by `./go init` | What the platform operates on; chosen by `WORKLOAD=<dir>` (default the attached clone, else `app`), not by profile; an attached repository keeps its contract in `.lights-out/`, with a `worker.md` and an optional `policy.env` (ADR 0037); defines compose services, environment keys, /healthz and /metrics on :8000, flags, smoke/load/test scripts, fixtures; unnamed in core/, gates/, verify/, adapters/ (verified by ci/app-free.sh); proven by ./go demo-seam (ADR 0030) |
| `AgentHarness` | `replay`, `gemini-cli`, `claude-code`, `codex`, `antigravity-sdk`, `adk` | `replay`: copies fixture files from `fixtures/<ticket>/<role>/`; `gemini-cli`: runs a Gemini CLI container session with role prompt and budgets, records transcript and events; `claude-code` and `codex`: the same contract with Claude Code 2.1.311 (provider `anthropic`) or Codex 0.166.0 (provider `openai`), one provider per harness, the guard in the CLI's `PreToolUse` hook and the container as the boundary (ADR 0041); coding roles produce `OUT/patch`, `pr.json`, `run.json`; review roles produce `OUT/final.md` (first line APPROVED or CHANGES-REQUESTED, rest reason), `input_sha256` (sha256 of the document), `run.json`; `adk`: no workspace; `INPUT` file → `OUT/calls.json`, `final.md`, `run.json`; a role selects it with `HARNESS=adk` in its role.env (ADR 0020 §1); review roles use `HARNESS=gemini-cli` (ADR 0021 §3) |
| `CodeHost` | `gitea`, `github` | `gitea`: bootstrap (admin user, repo, Actions off), clone-url, push, open-pr, status, merge, pr; `github` (M21, ADR 0043): the same eight verbs (bootstrap, clone-url, push, open-pr, status, merge, pr, await), plus ready.sh, env.sh and credential.sh, on GitHub's REST API. Serves an attached repository only. Bootstrap creates and pushes nothing: it checks that the clone's HEAD is the default branch head and refuses otherwise. open-pr bases on the default branch; status descriptions are cut to 140 characters; merge sends `merge_method=merge` with `sha=<judged head>`; pr reports `merge_sha` null until merged |
| `Tracker` | `file`, `github-issues`, `linear` | `file`: reads `tickets/<id>.md` (`get.sh`) and writes a new one (`put.sh <title> <origin>`, body on stdin, idempotent on the origin; M22); others read from external service |
| `EventBus` | `spool`, `pubsub-emulator`, `pubsub` | Verbs: `publish`, `pull`, `ack`; `spool` (L0/L1 default): a queue in one SQLite file, `.crew/spool.sqlite`, leases of 600 s; `pubsub-emulator` opt-in, behind the compose profile `emulators` ([ADR 0060](adr/0060-the-lean-plane.md)); `pubsub` (`adapters/pubsub/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)): Cloud Pub/Sub REST v1 with stdin-only Bearer auth |
| `StateStore` | `sqlite`, `firestore-emulator`, `firestore` | Verbs: `get`, `put`, `delete`; `sqlite` (L0/L1 default): one file, `.crew/state.sqlite`, that outlives `./go down`; `firestore-emulator` opt-in, behind the compose profile `emulators` ([ADR 0060](adr/0060-the-lean-plane.md)); `firestore` (`adapters/firestore/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)): Cloud Firestore REST v1 typed documents |
| `Orchestrator` | `inproc`, `cloud-workflows` | Verbs: `inproc/run.sh`; `cloud-workflows` (`adapters/cloud-workflows/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)): Cloud Workflows Executions REST v1 draining `EVENT_BUS` |
| `Delivery` | `envoy-canary`, `cloud-deploy`, `argo-rollouts`, `flagger` | Stages, promote, rollback; `cloud-deploy` (`adapters/cloud-deploy/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)) delegates to `plugins/cloud/gcp-cloud-deploy/` |
| `Metrics` | `promql`, `prometheus`, `managed-prometheus` | Same PromQL query; `promql` asks `METRICS_URL`, any Prometheus-compatible endpoint; `prometheus` is its case at `http://$PLANE_HOST:9090` (M22); `managed-prometheus` (`adapters/managed-prometheus/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)) queries Managed Prometheus REST v1 with stdin Bearer auth |
| `Flags` | `flagd`, `unleash` | OpenFeature SDK unchanged |
| `Registry` | `zot`, `oci-registry`, `artifact-registry` | Sign and verify; `artifact-registry` (`adapters/artifact-registry/`, [ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)) resolves OIDC/ADC tokens for `adapters/oci-registry/` |

**Rule: an adapter cannot introduce concepts into the core.** If Cloud Deploy has a concept that Envoy does not, either model it abstractly or keep it inside the adapter.

*Amended 2026-09-20 (ADR 0024, 0025, 0026) — decided, built in M8.* `ci/vendor-free.sh` proves the rule for the `Delivery` column by grepping `core/` for vendor names, comments included; its word list grows to `flagd|unleash`, now that the freeze check reads `adapters/$FLAGS/evaluate.sh` instead of a hardcoded flagd URL in `core/launchpad.sh`. The port contracts M8 changes:

| Port | Verbs after M8 | Note |
|---|---|---|
| `Delivery` | `deploy.sh <version> <image>`, `shift.sh <pct>`, `rollback.sh`, `status.sh` | four verbs, and an optional fifth, `canary-url.sh`, which prints one URL that reaches only the canary (`envoy-canary`: `http://envoy:8081`, a canary-only listener; `argo-rollouts` has none) and is read only by `verify/dast.sh` (ADR 0072 §3). `ready.sh` is **not** one of them: every adapter in `PLANE_ADAPTERS` owes `make up` a readiness probe whatever its port (ADR 0012 §6), and the conformance suite never calls it. `status.sh` prints one JSON object with exactly three keys — `stable` (string), `canary` (string or `null`), `weight` (integer 0–100) — and nothing else (ADR 0026 §2) |
| `Registry` | `publish.sh <tag>`, `verify.sh <tag-or-ref>`, optional `sbom.sh <repo>@<digest>` | `publish.sh` builds the current checkout, pushes it under `sha-<commit>`, writes one SPDX 2.3 SBOM with osv-scanner, attests it with cosign and reads it back, then signs it, printing the immutable `repo@sha256:…`; any failure before the signature fails publish, so no signed image lacks an SBOM (ADR 0072 §2); `verify.sh` verifies the signature and prints the digest it verified, which is the digest that gets deployed (ADR 0025 §1); `sbom.sh` prints the attestation's digest, which the release record, the tower document and `./go audit` carry as `sbom` (null when the adapter has no such verb) |
| `Flags` | `evaluate.sh <flag> [version]`, `set.sh <flag> <variant>` | the launchpad's freeze check and the `freeze`/`unfreeze`/`kill-switch` targets go through the port, so the same conformance case runs at L0 and L2 (ADR 0026 §3) |
| `CodeHost` | `await.sh <sha> <context>` | waits for a commit status the tower did not produce — the image build job — and prints its state (ADR 0025 §5). On GitHub it reads commit statuses only, never check runs, and no core step calls it there today (ADR 0043 §3) |

*Added 2026-11-04 (ADR 0043, M21).* A code host may ship `adapters/<name>/credential.sh`, a git credential helper. When it exists the Makefile appends it to the workbench's env-scoped git config as entry 1 (`credential.helper`), after the Containerfile's `safe.directory` at entry 0. Core's own clone and fetches and the adapter's `push.sh` then authenticate the same way, and core gains no verb and no vendor word. The GitHub token is handed to curl on stdin (`-H @-`) and to git by that helper. It is never in argv or a `.git/config`, and the worker container gets neither the variables nor the token. Gitea ships no helper, so L0 is unchanged.

The attached release path (ADR 0043 §4) works at any code host. For an attached repository `core/steps/release.sh` fetches the default branch, checks that the merge is on it, builds the merge commit in a throwaway worktree with `make publish` (the repository's own Containerfile) and signs it. The unchanged launchpad verifies the signature. The bundled app still waits for the Gitea `image` job. `ci/codehost.sh` proves the adapters cold, with recorders for curl and git and no fake GitHub; `./go codehost-conformance` proves the verbs live, on Gitea on every push and on GitHub nightly.

Every `Delivery` adapter must pass `gates/delivery-conformance.sh` to count as one: five cases, driven only through `core/launchpad.sh` and `core/promote.sh` (ADR 0024 §2, ADR 0026).

## Levels and Profiles

Four integration levels, each a profile (`profiles/l0.env` … `profiles/l3.env`). The Makefile selects one with `PROFILE ?= l0` and exports it. Adapter choices come from environment variables named after the ports above, except for the `Workload` port: it is chosen by `WORKLOAD=<dir>` (default `app`), not by the profile — it is the same workload at every level. See [ADR 0001](adr/0001-profiles-and-single-makefile.md) and [ADR 0030](adr/0030-workload-port.md).

*Amended 2026-09-20 (ADR 0022, 0028).* L0 and L1 are the whole product: every capability, no account, no key. L2 and L3 replace containers with managed services and add nothing the local plane lacks. ADR 0028's split, gcloud and terraform in a cloud image of their own that only L2 and L3 would build, is not in the tree: both are in the one workbench image (`workbench/Containerfile`), which every level builds.

## Invariants

1. **One PromQL file:** `verify/slo.promql` is the only query that decides if the canary passes. It runs identically on Prometheus local (L0/L1) and on Managed Prometheus (L2/L3). This is the line that prevents L0 and L2 from diverging. At L2 this means Cloud Deploy's own `verify` stanza stays off: `core/launchpad.sh` decides at every level (ADR 0027 §3).

2. **One CI, run the same way everywhere:** `.github/workflows/ci.yml`'s `ci` job runs `./go ci`, and so does a laptop; the workflows run on GitHub Actions. Each check runs once: `ci` keeps every check's stdout and exit code under `.crew/ci/` for the length of the run, and `contract`, the last check, judges those records for the one-line output contract instead of running the checks again; `./go ci-contract` alone runs every gate cold ([ADR 0062](adr/0062-contract-judges-the-run.md)). Since [ADR 0060](adr/0060-the-lean-plane.md) no runner executes them inside the plane (the L0 Gitea runner, whose last job was `image`, [ADR 0039](adr/0039-the-gitea-runner-runs-only-the-image-job.md), is gone). See [ADR 0002](adr/0002-self-ci-is-make-ci.md).

3. **Nothing reaches staging unsigned:** the `Registry` port signs what it publishes and `core/launchpad.sh` verifies the signature before the first traffic, at every level — a local key pair at L0/L1, a KMS key at L2, the same cosign command. An unsigned image is `NO-GO: launchpad — unsigned image`. See [ADR 0022](adr/0022-local-first-cloud-optional.md) and [ADR 0025](adr/0025-release-artefact-build-sign-verify.md).

## Toolchain and Entrypoint

`./go <cmd>` is a POSIX sh script (the only host entrypoint). It:

1. Picks an engine (`CONTAINER_ENGINE`, else podman, else docker)
2. Builds or reuses the `workbench` image from `workbench/Containerfile`
3. Runs `make <cmd>` inside it with the repo mounted at the same absolute path
4. Passes the socket for targets that need it (`up`, `down`, `test`, `demo`, `release`, `demo-rollback`, `demo-seam`, `demo-seam-plain`, `demo-hold`, `demo-launch`, `e2e`, `shell`, `gates`, `demo-gates`, `mutation-baseline`, `assign`, `demo-crew`, `record`, `triage`, `demo-triage`, `promote`, `record-evals`)
5. Runs without the socket for targets that do not (`ci`, `engine-compat`)

For `engine-compat` and `demo`, `./go` runs `ci/engine-compat.sh` on the host first.

See [ADR 0004](adr/0004-containerised-toolchain.md).

## Human gate

`core/launchpad.sh` writes `RELEASE_RECORD` (`.crew/release.json`) with the release outcome and metrics at each stage. `core/promote.sh` is go-for-launch: it reads the record, checks that the canary is held (not rolled back), runs the release role (at L1+) or renders the brief from the record (at L0), prints it, and asks for a human answer: `GO_FOR_LAUNCH=GO|NO-GO` if set, else one line from the terminal, else NO-GO. `GO` shifts the canary to 100% of traffic; `NO-GO` rolls it back. `demo-launch` proves this flow mechanically and runs on every push in the `launch` CI job. See [ADR 0020](adr/0020-adk-agents-and-go-for-launch-at-m7.md) §4.

## Demo Stages

`./go demo` runs a fixed list of stages. A stage that exists runs for real. A stage that does not yet exist prints one line and does not fail:

```
[demo] rollback: NOT YET (M3)
```

| Stage | Milestone | What |
|---|---|---|
| engine-compat | M2 | Host-side, the selected engine: versions, `up`, `smoke` |
| ci | M0 | Repo self-checks |
| up | M2 | Start the L0 plane in compose, per-adapter readiness |
| smoke | M2 | Create a meetup through Envoy |
| verify | M1 | Check metric shape at M1; canary vs stable evaluation at M3 |
| deploy | M3 | Staging → 5% → 25%, held for human gate |
| rollback | M3 | Automatic on verify NO-GO, or manual trigger |
| e2e | M6 | Full path through the tower: ticket → PR → CI → merge → canary → verify NO-GO → rollback, audited |

`demo-launch` is not a stage of `./go demo` (ADR 0020 §4); it runs in the `launch` CI job to prove go-for-launch on every push. Neither is `delivery-conformance` (ADR 0026 §4).

See [ADR 0003](adr/0003-make-demo-before-m3.md).

## PR gates

Eight gates run on a git range in order from cheap to expensive: `risk` (rules-not-points classifier), `secrets` (gitleaks), `test-weakening` (no deleted test, lost assertion or added skip in the diff), `deps` (osv-scanner), `sast` (bandit), `test` (pytest), `mutation` (cosmic-ray on changed lines against a measured baseline), `test-proof` (the tests a change adds or edits fail on the base). Each gate exits with a single line: `GO: <name>`, optionally followed by ` — <detail>` (where a toolchain gate ran, ADR 0042 §3), or `NO-GO: <name> — <reason>`. The gates read the active workload's configuration (`WORKLOAD_MUTATION_SCOPE`, `WORKLOAD_LOCKFILES`, `WORKLOAD_KILL_SWITCH`, `MUTATION_BASELINE`) from its `workload.env` instead of hardcoding paths; the gates and the core/ platform code are guaranteed never to name a specific workload by `ci/app-free.sh` (ADR 0030). `sast`, `test` and `mutation` run the workload's declared command (`SAST_CMD`, `TEST_CMD`, `MUTATION_CMD`) where its `TOOLCHAIN_IMAGE` says, and say so on their GO line (ADR 0042).

The `risk` gate is unique: it always GO when it classifies (the verdict is the classification itself — `low` = auto-merge, `medium` or `high` = review). A NO-GO from `risk` means "could not classify" (bad input, missing policy file), not "found something bad". The classifier reads five signals from the diff: migrations, auth, payments, platform (gates/ci/verify/policy/core and the toolchain), and diff size. Policy is data, not code: `policy/merge.env` holds globs, thresholds, and the auto-merge decision, readable by both sh and Python without a parser.

Eleven committed fixture PRs prove both verdicts on real gate failures: `honest-fix` and `fake-test` resolve the same bug (ticket 0001) with adequate and inadequate tests; `migration`, `sast-finding`, `vulnerable-dep`, and `failing-test` each trigger a different gate; `test-passes-on-base`, `assertion-removed` and `test-edited` prove `test-proof` and `test-weakening` failing and passing (ADR 0053); `sast-inherited` and `sast-added-on-red-base` run on a base already red at `sast` and prove the gate judges the diff (ADR 0072). Fixtures are git-format-patch patches applied in detached worktrees, with expected JSON outcomes recorded in `expect` files; `./go demo-gates` runs them all and asserts success.

See [ADR 0015](adr/0015-pr-gates-at-m4.md) and [ADR 0016](adr/0016-risk-model-and-merge-policy.md).

## Crew flow

The ticket→PR pipeline at M5: `core/crew.sh` clones the repo, sources the role's budgets, runs the `AgentHarness` adapter on the ticket, commits the result with the crew identity, pushes to `CodeHost`, and opens a PR. The harness returns a patch, PR title and body, transcript, and metadata (model, tokens, duration). At L0, `replay` copies the fixture into the output directory and applies the committed patch; at L1, `gemini-cli` runs the role live and records the session. Both produce identical artifacts (except the harness name and model field). The PR branch passes through the same gate pipeline as a hand-written PR: `make gates BASE=<initial> HEAD=<harness-commit>`. `demo-crew` proves the flow on ticket 0001 and asserts both the PR and the gates are GO.

## Control plane

At M6, the tower is a state machine over a JSON document per ticket in the state store, driven by step events on the event bus. States are `opened` → `pr-opened` → `ci-passed` | `ci-failed` | `review` → `merged` → `rolled-back` | `held`; since M15 `ci-failed` requests `rework`, which goes back to `pr-opened` with the verdict for the worker or, at the cap, ends at `needs-human` (ADR 0038); since M30 `merged` requests `mainline`, which checks main and leads to `main-green` (then the release) or back to `ci-failed` when the merge was reverted (ADR 0056). Step events carry `{"ticket","step"}`. Idempotency: an event whose precondition does not match the current state is dropped and acked; effects are re-runnable. The merge seam is the CodeHost adapter: `merge.sh <pr> <head sha>` merges only at the head CI judged (`ci.head_sha`), and a head that moved fails the step (ADR 0019, amended 2026-11-02); at L0, `ci` is the gates run by the tower and posted as a commit status, not a runner job. The artefact document holds `pr`, `task`, `spec`, `ci`, `merge`, `release`, and `history` fields. `./go audit` rebuilds the chain and compares the state store against the code host (two NO-GO kinds: missing link or divergence).

## L0 plane

Eight services in `compose.yaml`:

| Service | Host port | What |
|---|---|---|
| Envoy | 8080 | The app's only entrance; shifts traffic between stable and canary via weighted clusters and admin API |
| Envoy admin | 9901 | Runtime config: `GET /runtime` reads weights, `POST /runtime_modify` shifts traffic |
| backend | implicit | Stable revision at port 8000 inside compose |
| backend-canary | implicit | Canary revision (started on-demand by deploy); at port 8000 inside compose |
| Gitea | 3000 | Rootless, sqlite; Actions off, no runner: the release step builds the merge itself ([ADR 0060](adr/0060-the-lean-plane.md)) |
| Prometheus | 9090 | Scrapes Envoy admin `/stats/prometheus` every 5 s (`envoy_cluster_name` `stable` and `canary`), plus k8s targets when active |
| Zot | 5050 | OCI registry, empty until a merged PR is built into an image (M5 at the earliest); 5000 is macOS AirPlay |
| flagd | 8016 | OFREP; serves the active workload's `flags/flags.json` (`$WORKLOAD/flags/flags.json`, bind-mounted dynamically), the one bind mount |
| Pub/Sub emulator | 8085 | Opt-in (`EVENT_BUS=pubsub-emulator`, compose profile `emulators`): event bus for the tower's step events (topic `tower`, subscription `tower-inproc`) |
| Firestore emulator | 8086 | Opt-in (`STATE_STORE=firestore-emulator`, compose profile `emulators`): one JSON document per ticket (`tickets/<id>`) |

At L0 and L1 the tower's documents and its step events are files, not services: `.crew/state.sqlite` (`sqlite`) and `.crew/spool.sqlite` (`spool`), which no harness container mounts ([ADR 0060](adr/0060-the-lean-plane.md)).

Vendor config lives in `adapters/<name>/`, copied into images at build; one bind mount (the active workload's `flags/`) into flagd. Readiness: `adapters/<name>/ready.sh`, run by `make up` for the adapters the profile selects.

The canary revision is the same image as stable, with the workload's kill-switch flag (for the app, `waitlist`) targeted at its canary version (for the app, `0.2.0-waitlist`); the Workload port generalizes this (ADR 0030). The controller `core/launchpad.sh` shifts traffic through Envoy's admin API and evaluates `verify/slo.sh` at each stage. With `DAST=on` (off by default at every level), `core/launchpad.sh` first runs `verify/dast.sh` at staging, after `deploy.sh` and before the shift to 5%: ZAP's passive baseline scan of the stable and of the canary (through `canary-url.sh`), NO-GO only for an alert the canary has and the stable lacks (ADR 0072 §3). `DAST=on` with a Delivery adapter that has no `canary-url.sh` is refused before staging. Automatic rollback on a verify NO-GO, and on a stage that cannot be judged because a Metrics query failed: that one prints `cannot judge`, never a verdict ([ADR 0066](adr/0066-a-canary-that-cannot-be-judged.md)).

See [ADR 0012](adr/0012-l0-plane-layout-runner-envoy-readiness.md), [ADR 0013](adr/0013-launchpad-canary-verify-rollback-at-l0.md), [ADR 0014](adr/0014-prove-the-go-branch-with-demo-hold.md), [ADR 0066](adr/0066-a-canary-that-cannot-be-judged.md).

## Status

M88 — Managed L2/L3 cloud adapters and nightly OIDC conformance ([ADR 0128](adr/0128-managed-l2-l3-cloud-adapters-and-live-conformance.md)): `adapters/pubsub/`, `adapters/firestore/`, `adapters/artifact-registry/`, `adapters/managed-prometheus/`, `adapters/cloud-deploy/`, and `adapters/cloud-workflows/` implement the GCP L2/L3 profile adapters with stdin-only Bearer auth (`curl -H @-`); `core/gates/readiness.py` recognizes `METRICS=managed-prometheus`; `.github/workflows/cloud-conformance.yml` adds a fork-safe keyless OIDC nightly conformance workflow across GCP L2/L3, `oci-registry`, `aws`, and `azure` targets (opt-in via `CLOUD_CONFORMANCE_ENABLED`).

M80 — Autonomous spec-split, parallel fleet, and deterministic M24 harness ([ADR 0120](adr/0120-auto-spec-split-parallel-fleet-and-m24-harness.md)): `core/spec_split.py` and `core/spec_split.sh` (`./go spec-split`) partition `## Perimeter` into disjoint `impl` and `tests` `## Network` sections (opt-in via `INTAKE_NETWORK_SPLIT=on` in `core/intake.py` and `NETWORK_AUTO_SPLIT=on` in `adapters/network/run.sh`, preserving L0 single-worker fallback); `core/runner.py` (`./go watch --workers` / `RUNNER_WORKERS=`, `1..8`) executes disjoint tickets in parallel while serializing same-ticket steps and fleet `merge`/`mainline`/`release` transitions under `ReleaseTrainLock` (`core/releases.py`); `core/m24_runner.py` and `core/m24_runner.sh` (`./go m24-check`) evaluate `docs/scoreboard/m24-tenacity.json` and ingest real run logbooks deterministically without fabricating unrun provider rows.

Before M24 — The harness network is closed (ADR 0046): every CLI harness sits on the internal `crew` network only; its one way out is the `egress` proxy (`core/egress.py`, workbench image), which opens exact `host:port` pairs — the adapter's `EGRESS` (the stub's host under conformance) plus the workload's optional `crew-egress.txt` — written by `core/egress.sh` before each run; app's worker has its own `crew-postgres` on `crew`; `./go harness-conformance` gains case 9, a probe inside the harness that reads the key canary and tries eight ways out, and checks every request reached the stub from `egress`; the provider's own host remains a channel.

M30 — Main always green without a queue (ADR 0056): `merged` now requests a `mainline` step and `release` starts from `main-green`. Before the adapter merges, `core/mainline.py rejudge` lists the commits that landed on main after the PR's base; one that touched the ticket's perimeter sends the ticket to rework, otherwise the PR merges with no second gate run. The `mainline` step judges main with `sast` and `test` in groups of at most four merges (`policy/mainline.env`), bisects a red group, reverts the culprit through the CodeHost verbs and sends its ticket to rework. A red main stops `assign`, `rework` and `release` until a fix lands. `./go demo-mainline` proves it at L0 on hand-written fixtures.

M22 — Ops without the plane (ADR 0044): `./go incident` builds an incident document from the release record, `./go postmortem` runs ops-triage on it through a fourth tool, `file_postmortem`, checks every timeline anchor against the document and files each action item as a ticket through `adapters/file/put.sh`. The Metrics port gains `promql`, so `./go verify METRICS_URL=…` judges any Prometheus-compatible endpoint. None of the new targets takes the socket. `./go demo-postmortem` is NO-GO until the owner runs `./go record-postmortem PROFILE=l1`.

M27 — Tests that prove something, and tickets that wait (ADR 0053): `WORKLOAD_TEST_PATHS`, the twelfth key, names the tests. `test-weakening` refuses a diff that deletes a test file, loses assertions or tests, or adds skips, per language, unless the ticket's `## Removes tests` declares it. `test-proof` runs the tests a change adds or edits on the base, where they must fail. The tower's `assign` step refuses a ticket whose `## Perimeter` overlaps an open ticket's (one `perimeters/held` document in the StateStore), and on an attached repository runs `test` on the base first (`sast` left this check in ADR 0072, it judges the diff), caching a green verdict per commit; a refused ticket waits at `opened` for `./go resume`. Proven by `./go demo-gates` (three fixtures) and `ci/assign.sh`, without an engine.

M20 — Gates anywhere (ADR 0042): `workload.env` has eleven keys. The three toolchain gates (`test`, `sast`, `mutation`) run the workload's declared command where `TOOLCHAIN_IMAGE` says (`workbench`, `compose:<service>` or an image) and name it on their line. `./go init [<path>]` drafts the gates half of `.lights-out/` in the clone, with a measured `MUTATION_BASELINE`, and `./go gates` needs no engine socket on the workbench form. `./go demo-init` proves attach, init and gates on `workloads/minimal` and `workloads/minimal-js` without the plane. The 300 s budget is proven for small suites only.

M18 — The model is a choice (ADR 0040): `MODEL_PROVIDER` and `MODEL` in `profiles/` or `.env`, roles keep `THINKING` and budgets; the thinking map lives in `adapters/<harness>/thinking.env` with a source per line; the ADK roles select Claude and OpenAI through ADK's `LiteLlm` with `reasoning_effort` and no planner, Gemini through ADK's own model and planner; `RecordingLlm`/`ReplayLlm` wrap either and the recordings carry `provider`; `run.json` of both harnesses reports `provider` and the served `model`, null at L0 with the fixture's under `recorded`; `ci/model-free.sh` keeps model ids and key names out of `core/`, `gates/`, `verify/`, `roles/`; `litellm==1.106.6` pinned in the workbench (+190 MB image); the non-Gemini recording path (`./go record-evals PROFILE=l1 MODEL_PROVIDER=anthropic MODEL=claude-sonnet-5-5`) is implemented, not yet run.

M19 — `claude-code` and `codex` harnesses (ADR 0041): two adapters in `gemini-cli`'s shape, each pinned by `package-lock.json`, one provider each; the same `guard.sh` copied byte-identical into the CLI's pre-tool hook (gemini `BeforeTool`, Claude and Codex `PreToolUse`), the container as the boundary, codex's own sandbox off because it cannot start in rootless podman, the network open; the worker mounts its run directory, not `.crew`; budgets read from each vendor's signal; `MODEL_BASE_URL` as the one seam; `./go harness-conformance` runs every CLI harness against a scripted model (`gates/harness-conformance/stub.py`, nine cases, no key: plumbing, not a model's ability), with `harness-shape` and the extended `hooks` in `./go ci`; recording with another harness is `./go record … AGENT_HARNESS=claude-code|codex` into `fixtures/<ticket>/<role>/<harness>/`, implemented, not yet run, and replay does not read it.

M15 — The rework loop (ADR 0038): a gates NO-GO or a CHANGES-REQUESTED review no longer ends the ticket. `ci-failed` requests a `rework` step that runs dev-worker again from its own PR head with the ticket and the verdict (NO-GO line and the gate's details, or the reviewer's reason) as input; the new head meets the same judges. `policy/rework.env` caps a ticket at four attempts, then `needs-human`. Every history entry carries its attempt; `./go demo-rework` proves it at L0 on hand-written fixtures.

M12 — The Workload port (ADR 0030): the platform no longer operates on a hardcoded app but on any workload — a directory that owns a `workload.env` with seven keys, three compose services, health and metrics endpoints, one flag file, smoke/load/test scripts, and gate fixtures. `WORKLOAD=<path>` (default `app`) selects it; the app is one workload, `workloads/minimal/` is a second minimal workload that proves the seam. Every gate and the core platform read the active workload's configuration instead of hardcoding paths, verified by `ci/app-free.sh`. `./go demo-seam` is the acceptance test: runs the platform on `workloads/minimal` and asserts the release record's failing stage was specifically an `error_rate` NO-GO (proving both workloads' metrics were compared). `ci/workloads.sh` validates every workload's contract; `./go ci` swaps `compose` for `workloads` and gains `app-free`. Targets `demo-hold`, `demo-launch`, `demo-rollback`, `demo-gates`, and `delivery-conformance` remain unchanged; they now read the active workload's variables instead of the app's hardcoded ones.

M9 — Review: test-reviewer and architect roles with prompts on the existing gemini-cli harness (no ADK, these are reading roles); a `review` step in `core/tower.sh` (precondition `review`, next `ci-passed` or `ci-failed`), inserted where the risk classifier already routes medium/high PRs; review roles produce a second artefact contract with `final.md` (verdict and reason) and `input_sha256` (input document hash); ticket 0003's perimeter rule declares which files a ticket may change, checked in `core/perimeter.sh` before PR opens; two dev-worker `record` runs fix the token budget (ADR 0020 §9 measured 2.6M, down from provisional 12M) and validate ticket 0004's rewritten prompt; three new ci checks (`perimeter`, `review`, `tower` updated); medium-risk fixtures recorded at L1 and replayed at L0.

M7 — ADK agents and go-for-launch: product, ops-triage, release on ADK 2.9.1 with prompt.md and recorded model responses at L0 (`fixtures/adk/`), replay at L0 and live at L1+; adk eval suite with the ambiguous question and two misleading alerts; three new ci checks (`prompts`, `evals`, `promote`); `core/promote.sh` is go-for-launch reading `RELEASE_RECORD`, composing the brief (live at L1+, rendered from record at L0), and asking the human: GO_FOR_LAUNCH=GO|NO-GO; `./go promote` and `./go demo-launch` on every push; at L0 the promotion is Envoy weight 100 on the canary cluster, the canary does not become the stable image (M8).

M6 — Control plane: tower state machine in `core/tower.sh` with four step scripts in `core/steps/`, event bus (Pub/Sub emulator) and state store (Firestore emulator) adapters, `inproc` orchestrator, audit log that rebuilds deploy→PR→ticket→spec and cross-checks the code host, `./go e2e` runs the full path ticket→PR→CI→merge→canary→verify NO-GO→rollback with automated gates posted as commit status.
