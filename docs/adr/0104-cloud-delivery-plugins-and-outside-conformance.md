# ADR 0104 — Cloud delivery plugins (`plugins/cloud/*`) and extended `outside/*` conformance

- Status: accepted (amends [ADR 0024](0024-delivery-as-a-plugin.md) §4 [third-party and cloud delivery connectors live as external plugins under `plugins/cloud/` via `ADAPTERS_PATH` rather than inflating `core/` or adding unverified stubs under `adapters/`]; amends [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §5 [`ci/outside.sh` proves hash, trial, register, admission, tamper refusal, and symlink refusal for `DELIVERY=outside/*` and `CODE_HOST=outside/*` alongside `STATE_STORE=outside/*`]; fixes nested `outside/` conformance record path in `core/gates/delivery.py`)
- Date: 2026-11-24
- Milestone: M44

## Context

`lights-out` runs its local L0/L1 release path against `adapters/envoy-canary/` (`DELIVERY=envoy-canary`) and local Kubernetes (`adapters/argo-rollouts/`, `adapters/flagger/`). Teams attaching their own repositories (ADR 0037) also need to deliver to managed clouds — GCP Cloud Run / Cloud Deploy, AWS ECS / CodeDeploy, Azure Container Apps, and Hetzner k3s — using the exact same Delivery port contract (`deploy.sh`, `shift.sh`, `rollback.sh`, `status.sh`, optional `canary-url.sh`, `slo.promql`, plus `ready.sh`) and the same human gate before 100% (ADR 0020, ADR 0026).

Three project rules constrain the design:

1. **Golden rule & no hidden mocks (`CLAUDE.md`)**: CI and local contributor laptops have no live cloud accounts, billing accounts, or static cloud keys. A cloud adapter inside CI must never pretend to be a live cloud run: every plugin must clearly declare what is contract-tested locally against PATH stub CLIs and what requires a real cloud account via `./go delivery-conformance`.
2. **No static credentials**: Long-lived service-account JSON keyfiles, static IAM access keys, client secrets, or committed kubeconfigs must never live in the repository or in `$WORKLOAD`. Cloud deploys authenticate via keyless OIDC Workload Identity Federation from GitHub Actions or GitLab CI.
3. **Outside adapter admission (ADR 0058)**: `core/` must stay 100% vendor-free (`ci/vendor-free.sh`) and app-free (`ci/app-free.sh`). `core/adapters.py` already wires `DELIVERY=outside/<name>` and `CODE_HOST=outside/<name>` via `ADAPTERS_PATH`, but `ci/outside.sh` only proved `STATE_STORE=outside/json-files`, and `core/gates/delivery.py` `write_record()` wrote `f"{record_dir}/delivery-{self.delivery}.env"` without creating the `delivery-outside/` subdirectory when `DELIVERY=outside/<name>`.

## Decision

1. **Four external-ready Delivery plugins under `plugins/cloud/`**:
   Ship four complete Delivery plugins consumed through ADR 0058's `ADAPTERS_PATH` + `DELIVERY=outside/<provider>` mechanism:
   - `plugins/cloud/gcp-cloud-deploy` (Cloud Run + Cloud Deploy `customCanaryDeployment`)
   - `plugins/cloud/aws-ecs-codedeploy` (ECS Fargate + CodeDeploy + ALB weighted target groups)
   - `plugins/cloud/azure-container-apps` (Azure Container Apps multiple-revision mode + traffic splitting)
   - `plugins/cloud/hetzner-k3s` (Hetzner Cloud k3s + Argo Rollouts / `kubectl` + `hcloud`)

   Each plugin ships:
   - Executable POSIX `sh` scripts (`shellcheck -s sh` clean): `env.sh`, `ready.sh`, `deploy.sh`, `shift.sh`, `rollback.sh`, `status.sh`, `canary-url.sh`.
   - `slo.promql`: three-block PromQL (`traffic`, `error_rate`, `latency_p95`) with OTel HTTP semconv selectors (`http_server_request_duration_seconds_*`, `http_response_status_code=~"5.."`, `target_info`, `service_version`) compatible with `verify/slo.sh`, `verify/metric-shape.sh`, and `core/gates/readiness.py` (ADR 0098 §6).
   - `infra/main.tf`, `infra/variables.tf`, `infra/outputs.tf`: Terraform starter configuring OIDC Workload Identity Federation, least-privilege deploy identity, and explicit monthly budget/spend controls (default `50` with `50%` and `100%` alerts or node/tier caps).
   - `ci/github-oidc.yml` and `ci/gitlab-oidc.yml`: keyless OIDC authentication workflows (`id-token: write` / `id_tokens`) driving `./go delivery-conformance` or release verbs without static credentials.
   - `README.md`: operator setup, required variables, cost notes, and explicit stub-vs-live declaration.

2. **Keyless OIDC and fail-closed static credential refusal**:
   Every plugin's `env.sh` refuses empty or placeholder (`replace-me`) cloud target variables and refuses static credential environments (`GOOGLE_APPLICATION_CREDENTIALS`, static `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` without STS/WebIdentity token, `ARM_CLIENT_SECRET`/`AZURE_CLIENT_SECRET`) as well as static credential files inside `$WORKLOAD` (`.aws/credentials`, service-account/credential `*.json` keyfiles, `*.pem`, `*.p12`, `*.pfx`, `hcloud-token*`, or embedded `*kubeconfig*` files).

3. **Honest local-stub vs live-cloud boundary**:
   Every plugin `README.md`, `plugins/cloud/README.md`, and this ADR declare verbatim:

   > Contract-tested locally against stub CLIs; live cloud conformance requires your own cloud account via `./go delivery-conformance DELIVERY=outside/<provider> ADAPTERS_PATH=...`.

   Locally (`ci/tests/test_cloud_plugins.py`), PATH stub CLIs (`gcloud`, `aws`, `az`, `kubectl`, `hcloud`) exercise:
   - `env.sh` missing configuration and static-credential refusal;
   - `ready.sh` readiness polling and HTTP probe;
   - `deploy.sh <version> <image>` staging at weight 0;
   - `shift.sh 5`, `shift.sh 25`, `shift.sh 100`, and refusal of invalid percentages (such as `0`, `50`, `101`, non-numeric);
   - idempotent `rollback.sh` returning weight to 0;
   - `status.sh` exact 3-key JSON `{"stable": str, "canary": str|null, "weight": int}` (`0..100`);
   - `canary-url.sh` returning a single canary-targeted URL;
   - `slo.promql` selectors parsed by `verify/slo.sh`;
   - `shellcheck -s sh` cleanliness across all scripts.

   Live cloud API behavior, OIDC token exchange, IAM policies, and real billing budgets are NOT proven in CI and require running `./go delivery-conformance` against the operator's own cloud account after `./go cloud-setup`.

4. **`./go cloud-setup CLOUD=<provider>` (`plugins/cloud/setup.py` + `Makefile`)**:
   - Validates `CLOUD` against the four supported providers (`NO-GO: cloud-setup — ...` exit 1 on missing/unknown provider).
   - Checks presence of provider CLI tools (`gcloud`, `aws`, `az`, `kubectl`, `hcloud`, `terraform`, `jq`, `curl`) and reports each status on `stderr`.
   - Stages `infra/*.tf` and `ci/*-oidc.yml` starter files into `.crew/cloud/<provider>/` (never writing into `$WORKLOAD`!).
   - Prints exact `ADAPTERS_PATH` and `./go delivery-conformance DELIVERY=outside/<provider> ADAPTERS_PATH=...` steps on `stderr`.
   - Exits `0` with one stdout line: `GO: cloud-setup — ...`.

5. **Nested `outside/` conformance record & extended `ci/outside.sh`**:
   - `core/gates/delivery.py` `write_record()` adds `os.makedirs(os.path.dirname(path), exist_ok=True)` so `DELIVERY=outside/<name>` writes `.crew/conformance/delivery-outside/<name>.env` cleanly, matching `core/gates/readiness.py`'s lookup path `f"{records}/delivery-{d}.env"`. `core/` remains 100% vendor-free and app-free.
   - Add cold, engine-free outside adapter fixtures `fixtures/outside-adapters/file-delivery/` and `fixtures/outside-adapters/file-codehost/`.
   - Extend `ci/outside.sh` to prove ADR 0058 refusal -> `trial` + verb smoke checks -> `register` -> admission -> 1-byte tamper refusal -> symlink-in-tree refusal for `DELIVERY=outside/file-delivery` and `CODE_HOST=outside/file-codehost` alongside `STATE_STORE=outside/json-files`.

## Alternatives rejected

- **Put the four cloud adapters directly under `adapters/`**: Rejected. `adapters/` is the platform's own catalog (ADR 0024 §4), where every Delivery adapter needs a live conformance proof or nightly. Keeping cloud connectors under `plugins/cloud/` and loading them as `outside/<provider>` respects ADR 0024 and ADR 0058 while keeping `core/` vendor-free.
- **Install cloud CLIs (`gcloud`, `aws`, `az`, `terraform`, `hcloud`) into `workbench/Containerfile`**: Rejected by ADR 0028. Bloats the L0 workbench image for every user; `./go cloud-setup` inspects available CLIs and stages Terraform/OIDC starters into `.crew/cloud/<provider>/` instead.
- **Store static service-account keys or cloud tokens in `.env` / `$WORKLOAD`**: Rejected by M44 rules and `ci/secrets.sh`. OIDC federation and fail-closed `env.sh` checks prevent static secrets from entering repositories.
- **Simulate cloud providers in `ci/outside.sh`**: Rejected by `CLAUDE.md` ("no hidden mocks"). `ci/outside.sh` uses declared local-file and bare-git fixtures (`fixtures/outside-adapters/file-delivery/`, `file-codehost/`) to prove ADR 0058's admission mechanics cold, while `ci/tests/test_cloud_plugins.py` explicitly tests the four cloud plugins' CLI contracts via PATH stubs.

## Consequences

- Operators can stage Terraform + OIDC scaffolding and test or run all four cloud Delivery plugins through `ADAPTERS_PATH` and `DELIVERY=outside/<provider>` without altering `core/`.
- `core/gates/delivery.py` supports nested `outside/` adapter names when recording conformance passes, enabling `production-readiness` to verify outside Delivery adapters.
- `ci/outside.sh` now exercises three of the four wired ADR 0058 ports (`STATE_STORE`, `DELIVERY`, `CODE_HOST`); `EVENT_BUS` remains wired in `core/adapters.py` without an outside fixture yet.
- Live cloud conformance stays explicitly unverified until run against a real account; provider CLI flag changes remain a drift risk monitored when operators run `./go delivery-conformance`.

## Golden-rule check

`git clone && ./go demo` stays untouched at L0 with zero cloud accounts, zero API keys, and zero added containers or services. `./go ci` stays cold and engine-free (`ci/outside.sh` and `ci/tests/test_cloud_plugins.py` add only local file/git/stub-CLI checks taking a few seconds).

## Provenance

| Parameter / Default | Value | Provenance |
|---|---|---|
| Canary traffic stages | `5`, `25`, `100` | Decision inherited from ADR 0013 §4 (`core/launchpad.sh`, `core/promote.sh`) |
| `READY_TIMEOUT` | `180` s | Inherited from `Makefile` (measured 2026-11-02 against cold `envoy-canary`) |
| Monthly budget starter default | `50` USD / EUR, alerts at `50%` & `100%` | Decision inherited from ADR 0027 §2 |
| `ERROR_RATE_MAX_DELTA` / `LATENCY_P95_MAX_DELTA` | `0.01` / `0.25` s | Inherited from `policy/merge.env` (ADR 0013 §5) |
| Local stub CLI coverage | PATH stubs for `gcloud`, `aws`, `az`, `kubectl`, `hcloud` | Explicit contract test boundary; live cloud requires user account |
