# ADR 0095 — Flagger under the launchpad's gates, and two more CIs as surfaces: `adapters/flagger/` lets Flagger's own analysis roll a canary back while the launchpad opens each step and the human answer opens 100%; GitLab CI and CircleCI post the gates' line under their own context

- Status: accepted (amends ADR 0051 §4 [the k3s cluster script takes the controller from the adapter that asks for it] and §5 [a workload's `k8s/` may hold one subdirectory per Delivery adapter]; ADR 0058 §6 [the recipes for GitLab CI and CircleCI are written, each under its own context] and §9 [Flagger is built]; plan §4 [`flagger` joins the Delivery row])
- Date: 2026-11-16
- Milestone: M62

## Context

"Your CI/CD instead of ours" is the owner's direction for this sprint, and the
first product feedback named the Compose and Envoy lock-in. Two delivery
adapters exist: `envoy-canary` (L0) and `argo-rollouts` (ADR 0051), both driven
stage by stage by the launchpad, with `verify/slo.sh` as the only judge. ADR
0051 rejected Flagger because Flagger judges and moves the canary itself. ADR
0058 §9 recorded Flagger v1.45.0 and left it. ADR 0058 §6 named GitLab CI and
CircleCI as surfaces and wrote neither.

What a team on Flagger asks of lights-out is not a second judge: it is to keep
Flagger's automatic rollback, the thing they bought Flagger for, and add the
one human gate before 100%.

Checked on 2026-11-16:

- **Flagger.** v1.45.0 (2026-09-01) is still the latest release
  (`api.github.com/repos/fluxcd/flagger/releases`). Docs:
  <https://docs.flagger.app/usage/webhooks> and
  <https://docs.flagger.app/tutorials/traefik-progressive-delivery>, read
  2026-11-16. The behaviour below is read from the source at the tag,
  `pkg/controller/scheduler.go`, because the docs do not state it:
  - Each analysis tick, while the canary is `Progressing` or
    `WaitingPromotion`, runs the `rollback` webhooks, then the metric checks.
    A failed check counts; at `threshold` failed checks Flagger routes all
    traffic to the primary, scales the canary to zero and marks it `Failed`.
  - Before every weight increase it calls the `confirm-traffic-increase`
    webhooks. A non-200 answer holds the weight; the metric checks still run.
  - With `stepWeights`, the weights are exactly those steps. When the canary
    weight reaches the last step, the next tick calls `confirm-promotion`. A
    non-200 holds the canary at that weight in phase `WaitingPromotion`. A 200
    copies the canary's template into the primary, then routes 100% to the
    primary and scales the canary to zero: after a promotion the canary weight
    is 0 and the primary carries the new version.
  - A canary whose template hash equals the last applied one does not start
    (`pkg/canary/spec.go`, `hasSpecChanged`), and a `Failed` canary records
    its template's hash every tick: the same template deployed again after a
    rollback is ignored.
  - The Traefik router writes `traefik.io/v1alpha1` `TraefikService`s, the
    API version of the Traefik v3 that k3s ships.
- **The loadtester.** `ghcr.io/fluxcd/flagger-loadtester:0.39.0`, the tag in
  `kustomize/tester/deployment.yaml` at v1.45.0, serves the gate API
  (`pkg/loadtester/server.go`): `/gate/check` answers 200 only while the gate
  is open, `/gate/open` and `/gate/close` take `{"name","namespace"}`;
  `/rollback/check|open|close` the same for a separate rollback flag;
  `/gate/halt` always answers 403. The gate is one per canary
  (`<name>.<namespace>`): two webhooks on `/gate/check` share it.
- **CircleCI.** The CLI is v1.15.0 (2026-11-10,
  `api.github.com/repos/CircleCI-Public/circleci-cli/releases`). Its docs say
  local jobs cannot use the machine executor, because a machine job needs a
  VM of its own
  (<https://circleci.com/docs/guides/execution-managed/run-a-job-in-a-container/>,
  read 2026-11-16 through the search index; the page answered 404 to a
  direct fetch).
- **gitlab-ci-local.** 4.77.1 (2026-11-04). Its service containers do not
  mount the job's build volume (`src/job.ts`, `startService`): a job that
  drives a `docker:dind` service cannot bind its own checkout into a child
  container, which is what the wrapper does.
- **GitLab Runner.** v19.5.3, the newest tag of `gitlab-org/gitlab-runner`'s
  mirror on 2026-11-16.

## Decision

1. **`DELIVERY=flagger` is a folder and a profile line.** `adapters/flagger/`
   holds the four verbs, `ready.sh`, `env.sh` and `install.sh`. `core/`
   does not change; `ci/vendor-free.sh` gains `flagger`. The adapter reads
   `KUBECONFIG`, `FLAGGER_NAMESPACE` (default `lights-out`), `FLAGGER_CANARY`
   (default `backend`), `FLAGGER_HEALTH_PORT` (default 8000) and
   `FLAGGER_REGISTRY_HOST` (default `localhost:5050`), the five of ADR 0051
   §1 under this adapter's prefix.

2. **Three webhooks, and who answers each.** The Canary's analysis has
   `stepWeights: [5, 25, 100]`, the launchpad's weights (ADR 0013 §4), and
   three webhooks on the loadtester:

   | Webhook | URL | Opened by |
   |---|---|---|
   | `confirm-traffic-increase` | `/gate/check` | `shift.sh`, for exactly one step |
   | `rollback` | `/rollback/check` | `rollback.sh` |
   | `confirm-promotion` | `/gate/halt` | nobody in lights-out: a person who changes the Canary |

   - **The launchpad still decides every stage.** The gate is closed at rest.
     `shift.sh <pct>` opens it, waits until Flagger reports `<pct>`, and
     closes it. Flagger cannot move on its own, so `verify/slo.sh` judges each
     stage before the next one opens, as on every other adapter.
   - **Flagger's own analysis still rolls back.** The metric check runs on
     every tick whatever the gate says, and at `threshold` failed checks
     Flagger rolls the canary back without being asked. That is the automatic
     rollback a Flagger team keeps. It is a second judge in one direction only:
     it can stop a canary the launchpad would have let through, never pass one
     the launchpad stopped. ADR 0051 rejected a second judge because a stage
     could pass one and fail the other; here a stage that fails either one
     fails, and the launchpad's next verb (a verify on no canary traffic, or
     its rollback) reads the same plane.
   - **The human gate before 100% holds.** Only `core/promote.sh` calls
     `shift.sh 100`, and only on `GO_FOR_LAUNCH=GO` (ADR 0020 §4). Flagger
     cannot pass 25% without the stage gate, so 100% of traffic waits for the
     human answer.
   - **Flagger's promotion waits for a person.** In Flagger, "promotion" is
     the canary's template copied into the primary, after which the canary
     weight is 0. The Delivery port's 100% is the canary taking every request
     (ADR 0026 §2, ADR 0051 §3), and the conformance suite asserts weight 100
     after GO. So `confirm-promotion` cannot be the 25→100 step without
     breaking the port. It points at `/gate/halt`: at 100% Flagger sits in
     `WaitingPromotion` until a person makes the Canary's promotion URL
     `/gate/approve`, the step ADR 0051 §3 leaves to the M8 decision for
     Argo. This is the brief's "promotion waits for the human answer" mapped
     onto Flagger's two meanings of promotion, recorded here because the brief
     asked for `confirm-promotion` to be the human gate itself, which the
     source above shows cannot hold together with the suite.

3. **How the verbs map onto Flagger.**
   - **`deploy.sh <version> <image>`** closes the gate, then patches the
     Deployment: the first container's image, the annotation
     `lights-out.dev/version`, and `lights-out.dev/deploy`, a per-deploy
     stamp (`date +%s`), so the same version deployed again after a rollback
     is a new template (the `hasSpecChanged` fact above). It waits for phase
     `Progressing` at weight 0 and a canary pod answering `/healthz` with
     `<version>`, read through the API server's pod proxy as ADR 0051 §3 does.
   - **`shift.sh <pct>`** refuses a weight no step declares and a weight
     below the current one, then opens, waits, closes as §2 says. A canary
     Flagger rolls back while it waits is one stderr line and exit 1 naming
     the failed checks.
   - **`rollback.sh`** does nothing unless a canary is in flight (phase
     `Progressing` or `WaitingPromotion`). Otherwise it closes the gate, opens
     the rollback flag, waits for phase `Failed` at weight 0, and closes the
     flag. Idempotent, as the launchpad's opening rollback needs (ADR 0026 §6
     case 4).
   - **`status.sh`** prints the three keys of ADR 0026 §2: `stable` from a
     pod of the primary, `canary` from a pod of the canary Deployment while
     the phase is `Progressing`, `WaitingPromotion`, `Promoting` or
     `Finalising` (else `null`), and `weight` from `status.canaryWeight`,
     except in `WaitingPromotion`: Flagger zeroes that field on the way in
     (`pkg/canary/status.go`, `setStatusPhase`) while the routes stay at the
     last step, the only weight it enters that phase from, so there the
     weight is the last `stepWeights` value (found 2026-11-16, run
     43840181787).
   - **`ready.sh`** waits for the Canary to be past `Initializing` and for
     `$APP_URL/healthz` to answer 10 times in a row (ADR 0051's rule).

4. **The analysis reads our Prometheus.** A `MetricTemplate` on the plane's
   Prometheus (`http://prometheus:9090`, a selector-less Service bridged to
   the compose service as ADR 0051 §4 bridges the others) computes the
   canary's 5xx rate minus the stable's, over 1 minute, from the two jobs the
   argo adapter already scrapes through Traefik
   (`backend-k8s-canary`, `backend-k8s-stable`). No traffic is 0, not "no
   data": Flagger counts a query with no result as a failed check, and a
   canary waiting at a closed gate with no load would roll back. Absence of
   traffic is `verify/slo.sh`'s finding, not Flagger's. The threshold is
   `ERROR_RATE_MAX_DELTA` from `policy/merge.env`, substituted when the
   manifests are applied: one number, one home.

5. **The cluster is ADR 0051's.** `adapters/argo-rollouts/cluster.sh` keeps
   the k3s node, Traefik, the registry mirror and the bridges, and takes the
   controller from `CLUSTER_ADAPTER` (default its own directory): it runs
   that directory's `install.sh` before the manifests and its `ready.sh`
   after. Argo's install moves into `adapters/argo-rollouts/install.sh`
   unchanged. Flagger's `install.sh` applies `kustomize/base/flagger` at
   v1.45.0 (CRDs, RBAC, account, the controller with `-mesh-provider=traefik`
   and `-metrics-server=http://prometheus.lights-out:9090`) and the
   loadtester, each file checked against the sha256 measured on 2026-11-16
   (the release's checksums cover only the binaries). The base's ClusterRole
  grants nothing on `traefik.io`, which the tag's Helm chart grants
  (`charts/flagger/templates/rbac.yaml`): without it the Canary never left
  `Initializing` on the first run (2026-11-16), so `install.sh` adds the
  chart's rule as a ClusterRole of its own. The workload's
   manifests for Flagger live in `k8s/flagger/` (this instance:
   `app/k8s/flagger/`); argo's stay at the top of `k8s/`, which `cluster.sh`
   reads without recursing.

6. **Proof.**
   - `./go flagger-conformance` is `argo-conformance` with
     `CLUSTER_ADAPTER=adapters/flagger`: the six-case suite, unchanged.
   - `./go flagger-proof` drives the adapter with the cluster up
     (`KEEP_CLUSTER=1` on the first target) and proves what the suite cannot:
     **a.** the kill switch on, deploy, shift to 5, load, and no rollback
     from anyone but Flagger: GO when Flagger marks the canary `Failed` at
     weight 0 on its failed checks; **b.** the kill switch off, deploy, shift
     to 5, 25 and 100, then the gate left open: GO when Flagger holds
     `WaitingPromotion` at weight 100 for three ticks, and, once the
     promotion URL is changed to `/gate/approve`, reaches `Succeeded` with
     the primary on the new version.
   - Both run on GitHub's runners in the `flagger` job, the shape of `argo`.
     The local podman VM runs them only with the plane slot free on both
     connections, and never beside another heavy run.

7. **GitLab CI as a surface.** `surfaces/gitlab-ci/.gitlab-ci.yml`, one job
   on merge request pipelines. It runs `lights-out gates
   BASE=$CI_MERGE_REQUEST_DIFF_BASE_SHA HEAD=$CI_COMMIT_SHA` and posts the
   line to `$CI_API_V4_URL/projects/$CI_PROJECT_ID/statuses/$CI_COMMIT_SHA`
   as `name=lights-out/gitlab-ci`, `success` on a GO line only, else
   `failed`. The token is `LIGHTS_OUT_STATUS_TOKEN` (`api` scope), a masked
   project variable. The job image is `docker:29-cli`; the runner provides
   the engine. `LIGHTS_OUT_OUT` sits under `$CI_BUILDS_DIR`, the one tree a
   GitLab runner shares with the engine both in socket binding and with a
   `docker:dind` service.
   - **Proof, `./go demo-gitlab-ci`:** the gitlab adapter's GitLab CE
     (`adapters/gitlab/instance.sh`, its `external_url` on the compose
     network's name), a real `gitlab-runner` v19.5.3 registered on it with
     the docker executor and socket binding, `workloads/minimal` pushed with
     the recipe, a merge request opened: GO when the merge request pipeline
     ran the job and GitLab shows `lights-out/gitlab-ci`, `success`, the
     line as its description, and nothing under `gates` or `review`.
     This is more than `gitlab-ci-local`: GitLab itself schedules the job.
     `gitlab-ci-local` cannot run the recipe's engine wiring (the build
     volume is not shared with services). Not proven: a `docker:dind`
     service, which the recipe documents as the alternative.

8. **CircleCI as a surface.** `surfaces/circleci/config.yml`, one job on the
   `machine` executor, because the wrapper binds the checkout into a gates
   container and a remote Docker (`setup_remote_docker`) cannot. It posts to
   a GitHub-shaped statuses endpoint as `lights-out/circleci`, as the
   Jenkinsfile does (ADR 0058 §6), from `LIGHTS_OUT_STATUSES` and
   `LIGHTS_OUT_STATUS_TOKEN` in a context or project variables; the base is
   `LIGHTS_OUT_BASE`, else the merge base with `origin/$LIGHTS_OUT_BASE_BRANCH`
   (default `main`), because CircleCI exposes no pull request base.
   - **Proof, `./go demo-circleci`:** a local run is impossible
     (`circleci local execute` runs no machine job, see Context). So the
     proof is two things, and only these: `circleci config validate` on the
     recipe, in the CLI's own container image, pinned by digest; and the
     job's `run` step, taken from the recipe by its delimiters, run on the
     host with the environment a CircleCI machine job sets (`CIRCLE_SHA1`,
     `CIRCLE_BRANCH`, `CIRCLE_WORKING_DIRECTORY`) against a Gitea PR, as
     `demo-jenkins` does: GO when Gitea shows `lights-out/circleci` with the
     line. On GitHub's runner that host is an Ubuntu VM with Docker, the
     shape of CircleCI's machine image. Not proven: CircleCI's scheduler,
     checkout and contexts.
   - **Validation needs a token, sometimes.** The CLI validated the recipe
     anonymously in two runs on 2026-11-16 (ci runs 43833404799 and
     43836791053), then CircleCI's API answered the third with "This
     CircleCI host requires an API token to validate config" (run
     43840181787). The demo passes `CIRCLE_TOKEN` when set; without it, that
     one answer makes the GO line say "config NOT validated", never valid.
     Any other refusal is a NO-GO.

9. **The commit's own image.** Every surface demo runs the gates image of
   the commit under test: `LIGHTS_OUT_IMAGE=localhost/lights-out-gates:dev`
   after `./go package-gates` in CI, and the wrapper file of the checkout,
   never a published version tag. The 0.7.0 release commit named an image
   its tag had not published yet, and the jenkins job broke on it.

10. **Surfaces never judge, checked cold.** `ci/outside.sh` (b) checks the
    three recipes: each takes its line from `lights-out gates`, posts
    `success` on a GO line only, under its own context (`lights-out/jenkins`,
    `lights-out/gitlab-ci`, `lights-out/circleci`), and none of those is a
    context the tower posts.

### Where every number comes from

| Number | Value | Provenance |
|---|---|---|
| Flagger | v1.45.0 | latest release, verified 2026-11-16 |
| Loadtester | 0.39.0 | the tag Flagger v1.45.0's `kustomize/tester` pins |
| Install files' sha256 | — | measured 2026-11-16 at the tag |
| `stepWeights` | 5, 25, 100 | the launchpad's (ADR 0013 §4) |
| Analysis `interval` | 10 s | decision: one tick is the longest a verb waits for Flagger to act; `shift.sh` closes the gate within a second of the step, so the next tick cannot take a second step |
| `threshold` | 2 failed checks | decision: two ticks of errors, 20 s, before Flagger rolls back by itself; one tick would roll back on a single bad scrape |
| Error-rate delta | `ERROR_RATE_MAX_DELTA` (0.01) | `policy/merge.env`, the number `verify/slo.sh` reads |
| Metric window | 1 m | `verify/slo.promql`'s |
| "Held" in proof b | 3 ticks | decision: long enough that a promotion Flagger would make on its own would have happened |
| GitLab Runner | v19.5.3 | newest tag, 2026-11-16 |
| CircleCI CLI | v1.15.0 | newest release, 2026-11-10 |
| Description cut | 140 characters | GitHub's limit, ADR 0043 §3, kept for all three |

## Alternatives rejected

- **`confirm-promotion` as the 25→100 step** (`stepWeights: [5, 25]`). A
  promotion leaves the canary at weight 0 and the primary on the new version,
  so `status.sh` could not say what the port says after GO (weight 100), and
  the suite would have to change. The brief asked for the same suite.
- **Flagger driving the weights on its own schedule**, the launchpad only
  waiting. Flagger would pass 5% and 25% before `verify/slo.sh` judged them,
  and nothing would hold 25% for the human.
- **Two loadtesters, one gate each.** One gate is enough for the stage; the
  promotion gate has nobody in lights-out to open it, and `/gate/halt` says
  that without a second component.
- **No metric analysis, Flagger as a router only.** A team on Flagger loses
  the automatic rollback it chose Flagger for, which is the point of the
  adapter.
- **A copy of `cluster.sh` for Flagger.** Eighty lines of k3s, Traefik and
  bridging twice, drifting apart.
- **The Flagger Helm chart.** Helm is a second binary in the workbench for one
  install; kubectl's built-in kustomize applies the tag's base.
- **`gitlab-ci-local` for GitLab CI.** It runs the script, but not with the
  engine wiring a GitLab runner gives a job, and the recipe's risk is there.
- **`setup_remote_docker` in the CircleCI recipe.** The gates container would
  run on a remote engine that cannot see the checkout.
- **Writing the CircleCI result without running anything.** A config that
  only validates would be "not proven"; the step's script does run.

## Consequences

- **The workload contract grows by a directory.** `k8s/flagger/` is read
  only by `flagger-conformance` and `flagger-proof`.
- **One more Actions job, `flagger`**, the size of `argo`. Two more host-side
  jobs: `gitlab-ci` (GitLab boot, about four minutes) and `circleci` (Gitea,
  a minute).
- **Two judges in one direction.** A Flagger canary can be rolled back by
  Flagger while the launchpad is still measuring; the release record then
  says what the launchpad saw. The README says so.
- **A rolled-back canary's errors last one window.** The metric reads one
  minute of the canary job, and a new canary of the same workload deployed
  inside that minute can be rolled back by Flagger on its predecessor's
  errors (seen on the first `flagger-proof` run, 2026-11-16: delta 0.14 on a
  healthy canary). The suite's order keeps releases that far apart, as ADR
  0013 §2 does for `verify/slo.sh`; `flagger-proof` waits 70 s between its
  two cases.
- **Not proven:** a team's own cluster and its Flagger; a `docker:dind`
  GitLab runner; CircleCI's own scheduler. **Still open from M32:** an
  outside AgentHarness, the full tower on GitLab, a real Jenkins controller.

## Golden-rule check

`./go demo` does not change: no cluster, no runner, no new account. The new
targets are opt-in, pull anonymously and need no key. `ci/outside.sh` stays
cold. Passes.
