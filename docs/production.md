# Toward production

This page lists the decisions a team makes before lights-out takes real traffic. For each one it gives the keys, their default, the file the default lives in, and where the default comes from. The provenance is copied from the comment above each key in that file. Where the file gives none, this page says "no provenance recorded". It does not fill the gap.

Decisions: [ADR 0057](adr/0057-the-stepped-way-in.md) §10 and §11. Cloud levels (L2, L3) are designed, not run, so most of this page is about what you can set today.

## The layers of configuration

Later layers win over earlier ones.

1. **The profile**, `profiles/<level>.env`: the level, the model, the harness and the name of each adapter. `PROFILE=l1` picks it. The Makefile includes it first.
2. **The guided plan** (optional, non-secret), `.crew/start-plan.env`: written by `./go start`, loaded after `profiles/$(PROFILE).env` (when not running `demo`, `ci`, `e2e`, `corpus`, or contract/gate targets), before `.crew/ports.env` and `.crew/remote.env` ([ADR 0108](adr/0108-start-plan-wiring-wrapper-engine-gates-and-promote-clarity.md)). Secrets here are refused.
3. **The workload's contract**, `.lights-out/workload.env`: the commands and paths that are yours. `lights-out init` drafts it with a provenance line above every value ([ADR 0042](adr/0042-gates-anywhere-init-and-the-toolchain-contract.md)), using `INIT_SERVICE=<subdir>` in monorepos and optional stateful compose hints ([ADR 0110](adr/0110-stateful-monorepo-rust-java-and-js-managers.md)).
4. **Policy**: `policy/merge.env` is the platform's default. The workload's `.lights-out/policy.env` overrides nine keys, each under a `Provenance, measurement|external|decision:` line, and `core/policy.py` refuses an override without one. A PR is judged by its base's copy of both.
5. **`.env`**, this machine: ignored by git, included after the profile, guided plan, and auto-detected ports/remotes. Copy `.env.example`.
6. **The command line**: `./go demo PROFILE=l1`, `./go verify METRICS_URL=...`.

Role budgets sit beside these in `roles/<role>/role.env`. `core/crew.sh` reads the one of the role it runs.

Two commands tell you where you stand:

```sh
./go doctor --level l2        # what the level still lacks, one line per item
./go production-readiness     # GO only when delivery, metrics, secrets and the human gate hold
```

`./go doctor --level <l0|l1|l2|l3>` reads `profiles/<level>.env` and prints one line per item: the engine, its socket, git, the ports, the key, each adapter the profile names that has no `adapters/<name>/` ("designed, not built"), and for `l2` and `l3` the cloud toolchain. It ends with `GO: doctor` or `NO-GO: doctor`, which counts what is missing.

`./go production-readiness` ([ADR 0057](adr/0057-the-stepped-way-in.md) §10) is GO only when all four chapters below hold. It is NO-GO at L0 by construction. It is not one of the eight PR gates.

## 1. Provider and budget

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `MODEL_PROVIDER` | `gemini` at L1 to L3, empty at L0 | `profiles/<level>.env`; `.env` and the command line override | `profiles/l1.env`: "MODEL_PROVIDER in gemini\|anthropic\|openai\|zai" ([ADR 0040](adr/0040-the-model-is-a-choice.md) §1). `profiles/l0.env`: no model at L0, the roles replay recorded responses. No provenance recorded for the choice of Gemini as the default |
| `MODEL` | `gemini-3.8-flash` at L1 to L3, empty at L0 | same | `profiles/l1.env`: "The id is the one ADR 0018 pinned." The file does not say whether that is a measurement or a decision |
| `AGENT_HARNESS` | `replay` at L0, `gemini-cli` at L1 to L3 | same | No provenance recorded |
| `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `ZAI_API_KEY` | none (`replace-me` in `.env.example`) | `.env`, or your shell's environment | A secret, not a default. In production they come from the environment, not from `.env` |
| `MAP_BRIEF` | `off` | `profiles/<level>.env` | `profiles/l1.env`: "off at every level until M24 measures it" ([ADR 0054](adr/0054-the-project-map.md) §5). A pending measurement, not a measured value |
| `THINKING` | `high` for dev-worker, architect and test-reviewer; `medium` for product; `low` for ops-triage and release | `roles/<role>/role.env` | Each file: "THINKING from plan §5's table". The table is in `docs/PLAN.md` |
| `MAX_TURNS` | `60` for dev-worker, architect and test-reviewer | `roles/dev-worker/role.env` (the others stay at its number) | `roles/dev-worker/role.env`: 250 requests per day (Gemini API free tier, external constraint) divided by four attempts per day (decision) |
| `TIMEOUT_S` | `900` for every role | `roles/<role>/role.env` | `roles/dev-worker/role.env`: "Decision: the one wall-clock figure this repo has with provenance (plan §2)". Checked at M6 against a run of 557 s for 43 calls |
| `TOKEN_BUDGET` | dev-worker `2600000` (and `TOKEN_BUDGET_GEMINI=3900000` on Gemini), test-reviewer `1500000`, architect `600000`; none for product, ops-triage and release | `roles/<role>/role.env` | Measurement, M9 ([ADR 0021](adr/0021-review-roles-at-m9.md) §6, §7): the largest recorded run of the role plus one more call's headroom, rounded up. `TOKEN_BUDGET_GEMINI=3900000`: Measurement, M24 ([ADR 0092](adr/0092-glm-by-zai-and-a-budget-per-provider.md) §7). The three ADK roles have none yet: "run.json records the first real numbers" |

An adapter that calls a model refuses an empty `MODEL`, a provider outside the three, or an empty or `replace-me` key for the chosen provider ([ADR 0040](adr/0040-the-model-is-a-choice.md) §1).

## 2. Risk

All in `policy/merge.env`. A workload overrides nine keys of this file in its own `.lights-out/policy.env` (`core/policy.py`, `OVERRIDABLE`): `MUTATION_MARGIN`, `RISK_SIZE_MEDIUM_LINES`, `RISK_AUTO_MERGE_LEVELS`, the three `RISK_HIGH_*` path lists, `ERROR_RATE_MAX_DELTA`, `LATENCY_P95_MAX_DELTA` and `MUTATION_BASELINE`. The highest matched signal wins ([ADR 0016](adr/0016-risk-model-and-merge-policy.md) §2).

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `RISK_HIGH_MIGRATIONS`, `RISK_HIGH_AUTH`, `RISK_HIGH_PAYMENTS` | empty: no path | `policy/merge.env`; the workload's `policy.env` says where its own are | `policy/merge.env`: "the workload's to say, in its own policy.env" ([ADR 0037](adr/0037-attach-a-repository.md) §5) |
| `RISK_HIGH_PLATFORM` | the platform's own paths, plus `.lights-out/*`, `*policy.env` and `*worker.md` | `policy/merge.env`; not overridable | `policy/merge.env`: "Kept by owner decision, 2026-09-14", with the later additions dated and cited one by one. A change to a gate must not merge itself |
| `RISK_SIZE_MEDIUM_LINES` | `200` | `policy/merge.env`; overridable | External: "the low end of the '200-400 lines per review' guidance from the SmartBear/Cisco code-review study" |
| `RISK_SIZE_EXCLUDE` | `*.lock:*package-lock.json` | `policy/merge.env` | The comment gives the reason (lockfiles are generated) and the glob rule, not a provenance kind. No provenance recorded |
| `RISK_AUTO_MERGE_LEVELS` | `low` | `policy/merge.env`; may never include `high` | The comment says "low is the only level a PR can auto-merge at" ([ADR 0016](adr/0016-risk-model-and-merge-policy.md) §4). No provenance recorded for the choice |
| `MUTATION_MARGIN` | `0.10` | `policy/merge.env`; overridable | Decision: "one score quantum of the honest-fix fixture (about ten mutants on its changed lines)" |
| `MUTATION_BASELINE` | none in `policy/merge.env`; measured per workload | `.lights-out/workload.env` | Measurement, written by `init` (`./go mutation-baseline`): the whole-scope mutation score. The threshold is `MUTATION_BASELINE - MUTATION_MARGIN` ([ADR 0015](adr/0015-pr-gates-at-m4.md) §3). lights-out's own is `0.3934` in `.lights-out/workload.env` |

## 3. Rework

Read from the tower's own checkout, never from the PR it judges. Not overridable by a workload.

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `REWORK_MAX_ATTEMPTS` | `4` | `policy/rework.env` | The owner's decision recorded in `roles/dev-worker/role.env` (four attempts per day) against the Gemini API free tier's 250 requests per day (external constraint) at `MAX_TURNS=60`: 4 x 60 = 240 <= 250 ([ADR 0038](adr/0038-the-rework-loop.md) §3) |
| `MAINLINE_GROUP_MAX` | `4` | `policy/mainline.env` | Same source, in `policy/mainline.env`: at most four merges a day reach main through the free-tier tower, so a group never spans more than a day of them, and bisecting it costs at most two gate runs ([ADR 0056](adr/0056-main-always-green-without-a-queue.md) §3) |

Both numbers come from a free-tier quota. On a paid key they are a decision to revisit, and the files do not say how.

## 4. Delivery

`DELIVERY` names a directory under `adapters/`. A delivery adapter has the verbs the launchpad calls, and a conformance suite every adapter must pass ([ADR 0024](adr/0024-delivery-as-a-plugin.md)). Production readiness needs `rollback.sh` in the adapter, and a record in `.crew/conformance/` that the suite wrote on GO for the adapter's files as they are now. The record is this machine's proof and is not committed. Change a file in the adapter and the record no longer matches.

```sh
./go delivery-conformance     # the suite, on the adapter DELIVERY names
./go argo-conformance         # the same suite for argo-rollouts, on a k3s node (rootful engine)
./go registry-conformance     # Registry port suite (zot locally, or REGISTRY=oci-registry)
./go flags-conformance        # Flags port suite (FLAGS=flags-file, FLAGS=flags-http, or flagd)
```

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `DELIVERY` | `envoy-canary` at L0 and L1, `cloud-deploy` at L2 and L3; `argo-rollouts` is opt-in | `profiles/<level>.env` | `profiles/l0.env`: "Adapter names are directory names under adapters/". No provenance recorded for the choice |
| `REGISTRY`, `FLAGS` | `zot` and `flagd` at L0/L1; `oci-registry` and `flags-file`/`flags-http` for remote cloud E2E | `profiles/<level>.env` or CLI | Remote OCI distribution + keyless OIDC signing and daemonless/HTTP OFREP flags ([ADR 0107](adr/0107-cloud-registry-flags-and-proxy-slos.md)); contract-tested locally against stub servers, live cloud runs require user accounts |
| `SLO_MODE`, `SLO_PROMQL` | `proxy` (`plugins/cloud/*/slo-ingress.promql` in `cloud-setup`) or explicit `verify/slo.promql` / plugin `slo.promql` | `.env`, CLI, or `plugins/cloud/setup.py` | Default `proxy` reads ingress/Envoy cluster stats without app `/metrics` or `target_info` ([ADR 0098](adr/0098-verify-from-the-proxy.md), [ADR 0107](adr/0107-cloud-registry-flags-and-proxy-slos.md)) |
| `STAGE_SECONDS` | `60` | the Makefile, overridable on the command line | The Makefile: "equal to the slo.promql window, so a stage's numbers describe only this stage's canary" ([ADR 0013](adr/0013-launchpad-canary-verify-rollback-at-l0.md) §2, §5) |
| `READY_TIMEOUT` | `180` | the Makefile | Measurement: 60 was four times the 15 s a cold plane took at M2 close; raised to 180 on 2026-11-02 after a GitHub runner measured envoy-canary still not ready at 60 s. A ceiling, not a delay |
| `ARGO_NAMESPACE`, `ARGO_ROLLOUT`, `ARGO_HEALTH_PORT`, `ARGO_REGISTRY_HOST`, `KUBECONFIG` | `lights-out`, `backend`, `8000`, `localhost:5050`; `KUBECONFIG` has none | environment or `.env`; read by `adapters/argo-rollouts/` | [Its README](../adapters/argo-rollouts/README.md) states the defaults and what the cluster must allow. No provenance recorded |

The stages, 5% then 25%, are fixed in `core/launchpad.sh`, not keys. The go-for-launch takes the canary to 100%. `docs/PLAN.md` §7 and ADR 0013 list them without a provenance kind.

`DEMO_BUDGET_SECONDS` (`900`, plan §2) is the demo's ceiling, not a production key.

## 5. Metrics

For production, `METRICS=promql` on an endpoint you name. The plane's own `prometheus` scrapes the demo app, not yours. `./go production-readiness` asks the endpoint for `count(target_info)` and wants at least one series. `verify/slo.promql` reads the OTel HTTP semconv histogram joined to `target_info{service_version}`. On other metrics the answer is NO-GO with "no data" ([ADR 0044](adr/0044-incident-in-postmortem-out.md) §8).

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `METRICS` | `prometheus` at L0 and L1, `managed-prometheus` at L2 and L3; `promql` for your endpoint | `profiles/<level>.env` | No provenance recorded |
| `METRICS_URL` | empty | `.env`, or the command line (`./go verify METRICS_URL=<url>`) | A location, not a threshold. `.env.example`: judge your own metrics ([ADR 0044](adr/0044-incident-in-postmortem-out.md)) |
| `METRICS_AUTH_HEADER` | empty | your shell's environment in production; `.env` only on a laptop | A secret. One header line, never printed. Readiness is NO-GO if `.env` holds it |
| `ERROR_RATE_MAX_DELTA` | `0.01` (canary minus stable, a fraction of requests) | `policy/merge.env`; overridable | Decision, ADR 0013 §5: "deliberately loose at L0/M3; M8 replaces it with a number measured at L2". It has not been replaced |
| `LATENCY_P95_MAX_DELTA` | `0.25` (seconds) | `policy/merge.env`; overridable | The same decision and the same caveat |

The two deltas are the platform's loose defaults. Set yours in `.lights-out/policy.env` with a `Provenance, measurement:` line when you have measured your own service.

## 6. The human gate

The step to 100% waits for a person. This is the semantics of Argo Rollouts' [indefinite pause](https://argo-rollouts.readthedocs.io/en/stable/features/canary/) ("If no duration is specified for a pause step, the rollout will be paused indefinitely") and of Flagger's [confirm-promotion webhook](https://docs.flagger.app/usage/webhooks) (Manual Gating), here as the single human gate of a chain from ticket to production. Silence never promotes. `./go promote` prints the brief and waits for the answer. The console can press it for a named person.

Readiness is GO here when `GO_FOR_LAUNCH` is not preset in `.env` or the environment (a preset answer is silence that promotes), `CONSOLE_AUTH=proxy`, `CONSOLE_IDENTITY_HEADER` is set and `CONSOLE_LAUNCHERS` names someone.

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `GO_FOR_LAUNCH` | not set: the answer is typed, or pressed in the console | the command line, or the console's press | [ADR 0020](adr/0020-adk-agents-and-go-for-launch-at-m7.md) §4. Never preset in production |
| `GO_FOR_LAUNCH_BY`, `GO_FOR_LAUNCH_VIA` | `unauthenticated`, `cli` | set by the console on a press | [ADR 0047](adr/0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) §3. They are written to the release record |
| `CONSOLE_AUTH` | `loopback` | environment or `.env` | [ADR 0047](adr/0047-console-reads-one-versioned-logbook-and-writes-only-go-for-launch.md) §5: `loopback` is the default, `proxy` and `public` are the other two. The console refuses to start on any other combination |
| `CONSOLE_IDENTITY_HEADER` | none | environment or `.env` | ADR 0047 §5: the header a trusted proxy sets. A request without it gets 401 |
| `CONSOLE_LAUNCHERS` | none | environment or `.env` | ADR 0047 §5: comma-separated identities, compared verbatim; "kept in .env or the profile; it is not a secret" |
| `CONSOLE_PORT` | `8098` | environment or `.env` | ADR 0047: "That port is my decision" |

In `proxy` mode the console trusts the identity header verbatim. It cannot authenticate it. Keep the console reachable from the proxy only; ready-to-adapt OIDC reverse-proxy templates live in [`deploy/proxy/README.md`](../deploy/proxy/README.md) (`deploy/proxy/Caddyfile`, `deploy/proxy/oauth2-proxy.cfg`, `deploy/proxy/cloudflare-access.md`, [ADR 0111](adr/0111-hive-guided-onboarding-ui-and-proxy-recipes.md); live remote proxy validation requires user hosting). Readiness cannot see whether the port is open around the proxy ([ADR 0057](adr/0057-the-stepped-way-in.md), consequences).

## 7. Continuous ops loop & rollback automation

Continuous tower polling (`./go watch`, `core/runner.py`), fail-closed alert webhook intake (`./go alert-webhook`, `core/webhook.py`), and automatic rollback incident creation (`./go auto-incident`, `core/ops_loop.py`) follow [ADR 0109](adr/0109-continuous-orchestration-auto-incident-and-alert-webhook.md):

| Key | Default | Layer | Provenance |
|---|---|---|---|
| `POLL_S`, `ONCE` | `5.0` s poll interval, continuous | command line or `.env` for `./go watch` | `core/runner.py`: single-instance `fcntl.flock`, graceful `SIGTERM`/`SIGINT` exit |
| `ALERT_WEBHOOK_TOKEN` | empty (refuses non-loopback `--serve`) | shell environment (forwarded by name by `./go`) | `core/webhook.py`: `hmac.compare_digest` bearer auth + 64 KiB body cap (`./go alert-webhook`) |
| `AUTO_INCIDENT_ON_ROLLBACK`, `AUTO_POSTMORTEM_ON_ROLLBACK` | `off` in `core/releases.py` (`on` written into `.crew/start-plan.env` by `./go start`) | `.crew/start-plan.env`, `.env`, or CLI | `core/ops_loop.py`: deterministic `.crew/incidents/rollback-*.json` on rollback, optional postmortem dispatch |

Trigger-ready CI orchestrator and alert integration recipes live in [`surfaces/github-actions/tower-orchestrator.yml`](../surfaces/github-actions/tower-orchestrator.yml), [`surfaces/gitlab-ci/tower-orchestrator.yml`](../surfaces/gitlab-ci/tower-orchestrator.yml), and [`surfaces/alerts/README.md`](../surfaces/alerts/README.md).

## What this page does not cover

The coding harness's network allowance, the signature check before staging, and the flags (`FLAGS`, the kill switch) have their own pages: [the reference](reference.md) and [the guide](use-on-your-repo.md).
