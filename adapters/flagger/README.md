# flagger

The `Delivery` adapter for a Kubernetes cluster that runs [Flagger](https://flagger.app). Flagger keeps its own analysis and its automatic rollback. lights-out adds what Flagger does not have: the launchpad decides when each stage opens, `verify/slo.sh` judges it, and 100% waits for the human answer of go-for-launch. Choose it with `DELIVERY=flagger`. `core/` does not change ([ADR 0095](../../docs/adr/0095-flagger-under-the-launchpads-gates-and-two-more-ci-surfaces.md)). Opt-in: `./go demo` never uses it.

## How the two fit

Flagger moves a canary through its `stepWeights` on its own, one step per analysis tick, and rolls it back when its metric checks fail. lights-out holds three of the Canary's webhooks on the loadtester's gate API:

| Webhook | URL | Who opens it |
|---|---|---|
| `confirm-traffic-increase` | `/gate/check` | `shift.sh`, for exactly one step, then closes it |
| `rollback` | `/rollback/check` | `rollback.sh` |
| `confirm-promotion` | `/gate/halt` | nobody in lights-out |

- **Each stage waits for the launchpad.** The gate is closed at rest, so Flagger cannot pass 5% or 25% before `verify/slo.sh` has judged the stage before.
- **Flagger can still roll back on its own.** Its metric checks run every tick whatever the gate says. A canary that fails them `threshold` times is rolled back by Flagger, even while the launchpad is still measuring. Flagger can stop a canary lights-out would have passed; it can never pass one lights-out stopped.
- **100% waits for a person.** Only `./go promote` with `GO_FOR_LAUNCH=GO` calls `shift.sh 100`.
- **Flagger's own promotion waits for a person too.** In Flagger, promotion means copying the canary into the primary, after which the canary's weight is 0. lights-out's 100% is the canary taking every request, as for Argo. So `confirm-promotion` points at `/gate/halt`: at 100% Flagger holds `WaitingPromotion` until someone changes that URL to `/gate/approve` (or to an approval service of your own). Completing a release is not something the Delivery port says yet.

## What it needs from your cluster

- **kubectl access.** `KUBECONFIG` with rights to read the Canary, patch the target Deployment, list pods, use the pod proxy (`pods/proxy`) and the service proxy (`services/proxy`, for the loadtester) in the namespace. kubectl is in the workbench.
- **Flagger with a traffic router.** It is proved on Flagger v1.45.0 with the Traefik router. Any router Flagger supports should do; only Traefik is proved.
- **The loadtester** (`ghcr.io/fluxcd/flagger-loadtester`, proved on 0.39.0) as the Service `flagger-loadtester` in the Canary's namespace.
- **A Canary like this one.** `FLAGGER_NAMESPACE` (default `lights-out`) and `FLAGGER_CANARY` (default `backend`) name it; the Canary and its target Deployment share the name.
  ```yaml
  analysis:
    interval: 10s
    threshold: 2
    stepWeights: [5, 25, 100]       # the launchpad's weights
    metrics: [...]                  # your checks: Flagger's rollback
    webhooks:
      - {name: lights-out-stage, type: confirm-traffic-increase, url: http://flagger-loadtester.<ns>/gate/check}
      - {name: lights-out-promotion, type: confirm-promotion, url: http://flagger-loadtester.<ns>/gate/halt}
      - {name: lights-out-rollback, type: rollback, url: http://flagger-loadtester.<ns>/rollback/check}
  ```
  A metric with no data counts as a failed check in Flagger. A canary waiting at the closed gate with no traffic must not fail: write the query so that no traffic is 0 (`... or on() vector(0)`), as the example instance's `k8s/flagger/backend.yaml` does.
- **`/healthz` answering `{"version": "..."}`** on `FLAGGER_HEALTH_PORT` (default 8000). The adapter reads it from a pod of the primary (`app.kubernetes.io/name=<name>-primary`) and of the canary (`<name>`), through the pod proxy.
- **Metrics (declared limit, ADR 0098).** Unlike L0 `envoy-canary` (which reads `verify/slo.promql` from Envoy proxy stats), `adapters/flagger/slo.promql` and Flagger's `MetricTemplate` still query application `/metrics` and `target_info{service_version}` until a Kubernetes proxy/gateway adapter replaces them.
- **The version.** `deploy.sh` sets the first container's image, the pod-template annotation `lights-out.dev/version`, and `lights-out.dev/deploy`, a stamp per deploy: Flagger ignores a template it has already seen, and the same version deployed again after a rollback must start a new analysis.
- **The image.** `deploy.sh` receives a verified `repo@digest` and prefixes it with `FLAGGER_REGISTRY_HOST` (default `localhost:5050`).

## The verbs

| Verb | Flagger |
|---|---|
| `deploy.sh <version> <image>` | Close the gate and the rollback flag, patch the Deployment, wait for `Progressing` at weight 0 with a canary pod answering `<version>`. |
| `shift.sh <pct>` | Refuse a weight no step declares or one below the current one; open the gate, wait for `<pct>`, close it. A rollback by Flagger while waiting is an error that names the event. |
| `rollback.sh` | Nothing unless the canary is `Progressing` or `WaitingPromotion`; else close the gate, open the rollback flag, wait for `Failed` at weight 0, close the flag. |
| `status.sh` | `{stable, canary, weight}`: the primary's version, the canary's while it is in flight (else `null`), `status.canaryWeight`. |
| `ready.sh` | The Canary is past `Initializing`, and `$APP_URL/healthz` answers 10 times in a row. |

`install.sh` and `proof.sh` are not verbs: the first installs Flagger on the local node, the second is the proof below.

## The local proof

The cluster is the one `argo-rollouts` is proved on: one k3s node beside the L0 plane, Traefik, the registry mirror and the bridges ([its README](../argo-rollouts/README.md)). `CLUSTER_ADAPTER=adapters/flagger` makes `adapters/argo-rollouts/cluster.sh` install Flagger instead of Argo and apply the workload's `k8s/flagger/` manifests.

- `./go flagger-conformance` runs `gates/delivery-conformance.sh` unchanged against this adapter. The plane must be up (`./go up`).
- `./go flagger-proof`, on the cluster `./go flagger-conformance KEEP_CLUSTER=1` left up, proves what the suite cannot:
  - **Flagger rolls back by itself.** The kill switch on for the canary's version, a deploy, a shift to 5% and load; nobody else rolls back. GO when Flagger marks the canary `Failed` on its failed checks.
  - **Flagger's promotion waits.** The kill switch off, shifts to 5, 25 and 100, the gate left open. GO when Flagger holds `WaitingPromotion` at 100% for three ticks, and completes the promotion once the promotion URL becomes `/gate/approve`.
  It takes the node down after, unless `KEEP_CLUSTER=1`.
- Both run on every full push in the `flagger` job of `ci.yml`.

**The engine must be rootful**, for k3s, as for Argo: `CONTAINER_CONNECTION=podman-machine-default-root`. On a small podman VM, run it with nothing else heavy beside the plane.

**Not proven:** your cluster and your Flagger; a router other than Traefik.
