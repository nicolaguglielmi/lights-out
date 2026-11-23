# argo-rollouts

The `Delivery` adapter for a Kubernetes cluster that runs [Argo Rollouts](https://argo-rollouts.readthedocs.io/). lights-out's launchpad still decides every stage: it stages the canary, sends 5% and then 25% of traffic to it, has `verify/slo.sh` judge each stage, and rolls back on a NO-GO. The go-for-launch takes it to 100%. Argo Rollouts and your traffic router carry the traffic. Choose it with `DELIVERY=argo-rollouts`. `core/` does not change ([ADR 0051](../../docs/adr/0051-delivery-on-kubernetes-with-argo-rollouts.md)). Opt-in: `./go demo` never uses it.

## What it needs from your cluster

- **kubectl access.** `KUBECONFIG` must point at the cluster, with rights to read and patch the Rollout, including its `status` subresource. It must also be able to list pods and use the pod proxy (`pods/proxy`) in the namespace. kubectl is in the workbench. The `kubectl-argo-rollouts` plugin is not needed: the adapter sends the same status patches the plugin sends.
- **Argo Rollouts and a traffic router.** It is proved on v1.10.2. The canary must route through `trafficRouting`, whichever router you use. Without a router, Argo can only approximate a weight by replica counts: an exact 5% would take 20 replicas.
- **A Rollout with these steps.** `ARGO_NAMESPACE` sets the namespace (default `lights-out`) and `ARGO_ROLLOUT` the name (default `backend`).
  ```yaml
  steps:
    - setCanaryScale: {replicas: 1}   # staging: one canary pod, weight 0
    - pause: {}
    - setWeight: 5
    - pause: {}
    - setWeight: 25
    - pause: {}
    - setWeight: 100
    - pause: {}
  ```
  - Each `pause: {}` is where the launchpad takes over. `shift.sh <pct>` promotes until the Rollout is paused just after `setWeight: <pct>`. A percentage with no step fails.
  - The 100% of go-for-launch is the last pause, not a full promotion. The canary takes all the traffic, the stable ReplicaSet stays, and the next release's opening rollback returns to it. `envoy-canary` gives 100% the same meaning.
  - After an abort the canary pods drain on Argo's own schedule (`abortScaleDownDelaySeconds`, 30 s by default; 0 means never). `status.sh` reports `canary: null` as soon as the Rollout is aborted, whatever is still draining.
- **`/healthz` answering `{"version": "..."}`** on `ARGO_HEALTH_PORT` (default 8000, the Workload port's). The adapter reads it from each revision's pod through the API server's pod proxy, not through the router.
- **The version.** `deploy.sh <version> <image>` sets the first container's image and the pod-template annotation `lights-out.dev/version`. If your image reports its own version, ignore the annotation. If one image serves several versions, read it into an environment variable through the downward API.
- **The image.** `deploy.sh` receives a verified `repo@digest` from the Registry port and prefixes it with `ARGO_REGISTRY_HOST` (default `localhost:5050`, the L0 Zot). The cluster must be able to pull that reference.
- **Metrics (declared limit, ADR 0098).** Unlike L0 `envoy-canary` (which reads `verify/slo.promql` from Envoy proxy stats), `adapters/argo-rollouts/slo.promql` still compares the canary and stable revisions by `service_version` using app `/metrics` and `target_info`, until a Kubernetes proxy/gateway adapter replaces it. Your Prometheus must scrape both revisions' pods.

## The verbs

| Verb | Argo Rollouts |
|---|---|
| `deploy.sh <version> <image>` | JSON patch of the template (image and annotation). If the Rollout was aborted with the same template, clear the abort (`retry`). Wait for the staging pause, then for the canary pod to answer with `<version>`. |
| `shift.sh <pct>` | Promote one pause at a time (`status.pauseConditions: null`) until the step after `setWeight: <pct>`, then read the router's weight back. |
| `rollback.sh` | Abort (`status.abort: true`) if a canary is in flight and the Rollout is not aborted already, then wait for weight 0. Idempotent. |
| `status.sh` | `{stable, canary, weight}`. `stable` is from a `stableRS` pod. `canary` is from a `currentPodHash` pod, or `null` when nothing is in flight or the Rollout is aborted. `weight` is `status.canary.weights.canary.weight`, or 0. |
| `ready.sh` | The Rollout is Healthy or Paused, and `$APP_URL/healthz` answers through the router. |

## The local proof

`./go argo-conformance` runs `gates/delivery-conformance.sh` unchanged against this adapter, on a k3s cluster beside the L0 plane. The plane must be up first (`./go up`).

- `cluster.sh up` starts one k3s node, v1.37.2, as the compose service `k8s` from `compose.yaml` here, on the plane's network. `install.sh` installs Argo Rollouts v1.10.2, after checking the sha256 of `install.yaml`. With `CLUSTER_ADAPTER=adapters/flagger` the same node runs Flagger instead ([ADR 0095](../../docs/adr/0095-flagger-under-the-launchpads-gates-and-two-more-ci-surfaces.md)).
- It applies the workload's `k8s/` manifests, with the digest the Registry port verified as the stable image.
- Traffic: k3s bundles Traefik v3, which is the router there.
  - The app is reached on `ARGO_APP_PORT` (8180), and the API server on `ARGO_API_PORT` (6443).
  - The node pulls from the plane's registry through `registries.yaml`.
  - Prometheus scrapes the stable and canary Services through that Traefik, at `/lights-out/stable/metrics` and `/lights-out/canary/metrics`. Traefik sends each scrape to the Service's current pod; a NodePort scrape would keep its connection on the pod it first reached.
  - Flags stay on the plane's flagd.
- Bridging: a Service without a selector in the manifests is bridged to the plane's compose service of the same name.
- The cluster is removed when the suite ends. With `KEEP_CLUSTER=1` it stays, and `KUBECONFIG=.crew/argo/kubeconfig` reaches it.

**The engine must be rootful.** k3s in a container needs the cpuset cgroup controller. A rootless podman machine does not delegate it (`failed to find cpuset cgroup (v2)`, measured on podman 5.7 on 2026-11-06), and neither does a rootless podman on Linux without extra delegation. The machine's rootful connection works without changing any setting:

```sh
CONTAINER_CONNECTION=podman-machine-default-root ./go up
CONTAINER_CONNECTION=podman-machine-default-root ./go argo-conformance
```

That plane lives in root's storage, separate from your rootless one: images are built again the first time, and `./go down` takes the same variable. Docker, which GitHub Actions runs, is rootful already.
