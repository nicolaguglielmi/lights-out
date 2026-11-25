# ADR 0051 — Delivery on Kubernetes: `adapters/argo-rollouts/` drives a Rollout with kubectl alone, through declared steps and a traffic router, and is proved by the unchanged conformance suite on a k3s node beside the L0 plane

- Status: accepted (builds the third candidate of ADR 0024 §5; amends ADR 0030 [the Workload contract gains an optional `k8s/`]; plan §4's Delivery row gains `argo-rollouts`)
- Date: 2026-11-06
- Milestone: M25

## Context

`envoy-canary` is the only Delivery adapter that exists. `cloud-deploy`
is designed for M8 and needs a GCP account. A team that already ships on
Kubernetes therefore has to write an adapter before lights-out does
anything for it. ADR 0024 §5 named the adapter to write first:
Kubernetes with Argo Rollouts. It runs on any cluster, it needs no
account, and it is the most likely to be used by someone not on GCP.

The following was checked on 2026-11-06 against the docs, the GitHub
release API and the upstream source.

- **Argo Rollouts version.** v1.10.2 (2026-11-04) is the latest stable,
  and v1.11.0-rc1 is a prerelease. The CRD is still
  `argoproj.io/v1alpha1`, and `pause: {}` holds until something promotes.
- **No imperative weight.** Argo Rollouts has no command to set a weight
  on the spot. Weights are `setWeight` steps declared in the Rollout.
- **Weights without a router.** With no traffic router, a weight is
  approximated by replica counts (`utils/replicaset/canary.go`). An
  exact 5% takes 20 replicas.
- **Weights with a router.** With one, the split is exact, and
  `setCanaryScale` and `abortScaleDownDelaySeconds` apply. The built-in
  Traefik router defaults to Traefik v3 (`traefik.io/v1alpha1`).
- **`abortScaleDownDelaySeconds`.** Its default is 30 s, and 0 means
  the aborted canary is never scaled down (measured on the cluster below,
  2026-11-06).
- **What the plugin sends.** `kubectl-argo-rollouts` (in
  `pkg/kubectl-argo-rollouts/cmd/*`) promotes, aborts and retries with
  merge patches on the Rollout's `status` subresource:
  `{"status":{"pauseConditions":null}}`, `{"status":{"abort":true}}` and
  `{"status":{"abort":false}}`.
- **The plugin's `get`.** `get rollout` has no JSON output. The object
  itself carries `status.stableRS`, `currentPodHash`, `currentStepIndex`,
  `abort` and `canary.weights.canary.weight`.
- **After an abort.** A new pod template clears an abort. An identical
  one needs a retry.
- **kind.** It shells out to the `docker` or `podman` CLI, so a socket
  alone is not enough. Its podman provider is still experimental.
- **k3d.** It is one more binary, and it brings its own helper
  containers and its own network.
- **k3s.** It runs as one privileged container, the pattern k3d itself
  uses.
- **Measured on this machine:**
  - `rancher/k3s:v1.37.2-k3s1` on the rootless podman 5.7 machine dies
    with `failed to find cpuset cgroup (v2)`. The machine's own
    `/etc/systemd/system/user@.service.d/delegate.conf` delegates
    `memory pids cpu io`, not cpuset.
  - On the same VM's rootful connection it reaches node Ready, with
    Traefik running, in under 50 s.
- **GitHub's ubuntu runners.** They run docker, rootful, which this
  repository's jobs already use.

## Decision

1. **The adapter is a folder and a profile line.**
   `adapters/argo-rollouts/` contains the four verbs, `ready.sh`, and an
   `env.sh` that the verbs source. `DELIVERY=argo-rollouts` selects it.
   - `core/` does not change, and `ci/vendor-free.sh` already has
     `argo|rollouts` in its list.
   - The adapter reads five variables:
     - `KUBECONFIG`
     - `ARGO_NAMESPACE` (default `lights-out`)
     - `ARGO_ROLLOUT` (default `backend`)
     - `ARGO_HEALTH_PORT` (default 8000, the Workload port's)
     - `ARGO_REGISTRY_HOST` (default `localhost:5050`, the L0 registry,
       which `envoy-canary` already hardcodes)

2. **kubectl only, no plugin.** The workbench gains kubectl 1.37.2,
   pinned and sha256-checked. That is the release `dl.k8s.io` called
   stable on 2026-11-06, and the same minor as the k3s below.
   - Promote, abort and retry are the plugin's own status patches, sent
     with `kubectl patch --subresource=status`.
   - The plugin would be a second binary in the workbench, and an
     interface over those three patches. The patches are what the
     upstream source sends, cited in `env.sh`. If a release changes
     them, the conformance suite is what notices.

3. **How the verbs map onto Argo Rollouts.** Steps are declared,
   because Argo has no imperative weight.
   - **The Rollout's steps** must be `setCanaryScale {replicas: 1}`,
     `pause`, then `setWeight 5`, `25` and `100`, each followed by a
     `pause`.
   - **`deploy.sh <version> <image>`**
     - It JSON-patches the first container's image and the pod-template
       annotation `lights-out.dev/version`. A Rollout left aborted with
       the same template is retried.
     - It then waits for the staging pause, and for a canary pod whose
       `/healthz` reports `<version>`. It reads that through the API
       server's pod proxy: the pod itself, not the router, as
       `envoy-canary` reads its containers (ADR 0013 §4).
   - **`shift.sh <pct>`**
     - It promotes one pause at a time until the Rollout is paused just
       after `setWeight: <pct>`, then reads the router's weight back.
     - A weight that no step declares is an error, not a nearest match.
   - **`rollback.sh`**
     - It aborts when a canary is in flight and the Rollout is not
       already aborted, then waits for weight 0. Otherwise it does
       nothing.
     - That makes the launchpad's opening rollback free and idempotent
       (ADR 0026 §6 case 4).
   - **`status.sh`** prints three keys:
     - `stable` is what a `stableRS` pod reports.
     - `canary` is what a `currentPodHash` pod reports, or `null` when
       nothing is in flight or the Rollout is aborted.
     - `weight` is `status.canary.weights.canary.weight`, or 0.
   - **The 100% of go-for-launch** is the `setWeight 100` pause, not a
     full promotion. That is the meaning `core/promote.sh` gives 100% at
     L0, and the next release's opening rollback returns to stable.
     Completing the Rollout, so that the canary becomes stable, is not
     something the port can say today. It waits for the M8 decision
     that also makes Cloud Deploy's canary the new stable.

4. **The local proof is k3s beside the L0 plane, not a second plane.**
   - **The node.** `adapters/argo-rollouts/compose.yaml` declares one
     k3s node, `rancher/k3s:v1.37.2-k3s1` pinned by its index digest, as
     the compose service `k8s` under profile `argo`. Only
     `cluster.sh up` merges that file in; `./go up` never does.
   - **What stays on the plane.** The plane keeps Registry (Zot), Flags
     (flagd), Metrics (Prometheus) and the workload's dependencies. Only
     Delivery moves.
   - **Pulling the image.** k3s pulls `localhost:5050/...@sha256:...`
     through a `registries.yaml` mirror to `zot:5000` on the plane's
     network. That is the same digest the Registry port verified, with
     no second copy.
   - **Scraping.** Prometheus scrapes the stable and canary Services
     through the node's Traefik, at `k8s:80/lights-out/stable/metrics`
     and `k8s:80/lights-out/canary/metrics`. That is two new jobs in
     `adapters/prometheus/prometheus.yml`, and their targets are down
     whenever the cluster is.
     - The first design scraped the two Services on NodePorts. Prometheus
       keeps one keep-alive connection per target, and conntrack holds
       an established connection on the pod it first reached.
     - So when Argo switched the canary Service to the canary pod, the
       "canary" scrape went on reading the stable pod. Verify then said
       "no traffic from canary" (measured 2026-11-06).
     - Prometheus refuses a `Connection: close` scrape header.
     - Traefik, which sends every request to the Service's current pod
       IPs, fixes it without touching Prometheus.
   - **Reaching plane services.** A pod cannot use the engine's
     resolver: docker answers on 127.0.0.11 inside the node only.
     Instead, every selector-less Service in the workload's manifests is
     bridged to the compose service of the same name. `cluster.sh` reads
     that service's IP from the engine API and writes an EndpointSlice.
   - **Argo Rollouts.** It is installed from the release's
     `install.yaml`, v1.10.2, checked against the sha256 measured that
     day. The release's checksums file does not list `install.yaml`.
   - **The command.** `./go argo-conformance` needs the plane up. It
     verifies the stable digest through the Registry port, runs
     `cluster.sh up`, then runs `make delivery-conformance
     DELIVERY=argo-rollouts` with `APP_URL` on Traefik and `KUBECONFIG`
     on the node. It takes the node down afterwards (`KEEP_CLUSTER=1`
     keeps it).
   - **The rootful engine.** Under podman the proof needs a rootful
     engine, for the cpuset reason above.
     `CONTAINER_CONNECTION=podman-machine-default-root` gives one
     without changing any setting. The README says so.

5. **The workload contract gains an optional `k8s/`.** It holds the
   manifests that run the workload on a cluster, the counterpart of its
   `compose.yaml`.
   - `app/k8s/backend.yaml` holds the Rollout `backend`, its two
     Services, the Traefik objects, and the selector-less Services for
     its plane dependencies.
   - It lives in the workload because it names the workload's
     dependencies, which `ci/app-free.sh` keeps out of `adapters/`.
   - `@STABLE_IMAGE@` is the one placeholder. `cluster.sh` fills it with
     the verified digest.
   - A workload without `k8s/` simply cannot run `argo-conformance`. No
     other target reads the directory.

6. **Proved on every push.** The `argo` job in `ci.yml` runs `./go up`
   then `./go argo-conformance` on docker, in the shape of the `launch`
   job.

### Where every number comes from

| Number | Value | Provenance |
|---|---|---|
| Argo Rollouts | v1.10.2 | latest stable, verified 2026-11-06 |
| k3s | v1.37.2+k3s1 | the stable channel on 2026-11-06 (`update.k3s.io`) |
| kubectl | 1.37.2 | the stable release on 2026-11-06 |
| `install.yaml` sha256 | — | measured 2026-11-06 |
| Replicas | 1 per revision | decision: each Service then fronts exactly one pod, so a scrape is one series per revision. A Service in front of N pods alternates scrapes between them and breaks `rate()` |
| `abortScaleDownDelaySeconds` | left at Argo's default, 30 s | measured 2026-11-06: 0 means *never* scale the aborted canary down, not "at once". `status.sh` does not depend on it, because an aborted Rollout reports `canary: null` whatever is still draining, so its reads are stable around case 5 (ADR 0026 §6) |
| `ready.sh` | 10 consecutive answers, 1 s apart | ADR 0013 §4's 10, reused. One answer was not enough: the router answered once and then failed the load's login, measured 2026-11-06 |
| Host ports | 8180 and 6443 | decision: absent from the plane's published list in `make up`'s error message. 6443 is the k3s API port, and both move with `ARGO_APP_PORT` and `ARGO_API_PORT` |
| Weights | 5, 25 and 100 | the launchpad's (ADR 0013 §4), not the adapter's |
| Waits | `READY_TIMEOUT` | the Makefile's 180 s, with its provenance there |
| Healthz count | 10 of 10 | ADR 0013 §4's, through the suite, unchanged |
| Runtime | about 5 min | measured 2026-11-06 on the podman machine (4 CPUs, 4 GiB, rootful connection): cluster up plus six cases in 4 min 47 s with the images cached. No ceiling binds it, as for ADR 0026 §7 |

## Alternatives rejected

- **Flagger (v1.45.0).** Flagger drives the canary itself: its own
  analysis promotes or rolls back on a schedule. lights-out's launchpad
  has to decide every stage, with `verify/slo.sh` as the only judge.
  Driving Flagger stage by stage fights its design, and it needs a mesh
  or an ingress for weights, exactly as Argo does.
- **Cloud Deploy.** It stays M8, the one cloud adapter (ADR 0024 §4),
  and it needs a GCP account. That is what this adapter avoids.
- **An Argo `AnalysisTemplate` on the same PromQL.** Argo would then
  judge as well, and a stage could pass one judge and fail the other.
  `verify/slo.sh` stays the only judge, and the Rollout holds at each
  pause until the launchpad promotes.
- **A replica-weighted canary, with no router.** An exact 5% needs 20
  replicas, and with fewer the weight `status.sh` reported would not be
  where the traffic is.
- **The `kubectl-argo-rollouts` plugin.** See §2: a second binary for
  three patches.
- **kind.** It needs a container-engine CLI in the workbench, beyond the
  socket the workbench already has, and its podman provider is still
  experimental.
- **k3d.** It is a second binary, adds two helper containers and a
  network of its own, and its podman support is experimental too.
- **A Gateway API router plugin.** The controller downloads the plugin
  at start. Traefik is built in and ships with k3s.
- **Moving the whole plane into the cluster.** Flags, Registry and
  Metrics would each need a second home, and `adapters/flagd/set.sh`
  writes the flag file that the compose flagd reads. The suite proves
  Delivery, so only Delivery moves.
- **The whole proof on a rootless engine.** Not possible without
  delegating cpuset. Doing that inside the user's podman machine is a
  host setting, and it is the user's to change.

## Consequences

- **The workbench gains kubectl.** The image rebuilds once for everyone,
  and a first run reports its time instead of being judged (ADR 0013,
  amendment 2026-11-04).
- **Prometheus has a second job.** It is down on every plane that has no
  cluster, the same way `backend-canary` is between releases.
- **One more Actions job.** It boots the plane and a k3s node and runs
  the six cases.
- **What `./go demo` touches.** Only the image tag (kubectl) and the
  Prometheus job, whose targets are down. Neither is on its path.
- **Rootful podman in the README.** It is now a requirement written
  there, for this target only.
- **A team's own cluster.** The adapter has not been run against one:
  its proof is the local cluster. Its README lists what such a cluster
  needs.

## Golden-rule check

`git clone && ./go demo` does not change. It needs no cluster, no
kubectl call, and no new account. `./go argo-conformance` is opt-in,
needs neither an account nor a key, and runs locally on a rootful engine
or on GitHub's runners.
