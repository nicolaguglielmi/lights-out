# ADR 0107 — Cloud OCI Registry (`oci-registry`), File/HTTP Flags (`flags-file`, `flags-http`), Registry/Flags Conformance Suites, and Cloud Proxy/Ingress SLO Templates

- Status: accepted (amends [ADR 0024](0024-delivery-as-a-plugin.md) §4, [ADR 0025](0025-release-artefact-build-sign-verify.md), [ADR 0058](0058-outside-adapters-and-other-cis-as-surfaces.md) §1/§5, [ADR 0072](0072-security-scanning-sast-sca-dast-sbom.md) §2, [ADR 0098](0098-verify-from-the-proxy.md) §6, and [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md))
- Date: 2026-11-24
- Track: Cloud E2E Bridge

## Context

`lights-out` runs its local L0/L1 release path against `REGISTRY=zot` (`localhost:5050`) and `FLAGS=flagd` (a Compose daemon), and [ADR 0104](0104-cloud-delivery-plugins-and-outside-conformance.md) added four cloud Delivery plugins under `plugins/cloud/` (`gcp-cloud-deploy`, `aws-ecs-codedeploy`, `azure-container-apps`, `hetzner-k3s`). However, taking an attached repository end-to-end on remote clouds still lacked four pieces:

1. **No remote OCI `REGISTRY` adapter**: `adapters/zot/` hardcodes `http://$PLANE_HOST:5050/v2/lights-out-backend/...` and `.crew/cosign.key`. Cloud targets (GHCR, GAR, ECR, ACR, or any custom OCI Distribution registry) need an adapter supporting `OCI_REGISTRY_REPO`, optional `OCI_AUTH_CMD`, keyless OIDC or KMS/`cosign` signing/verification, SPDX 2.3 SBOM attestation, and fail-closed refusal of static keys inside `$WORKLOAD`.
2. **No daemonless `FLAGS` adapters**: `adapters/flagd/` assumes the Compose `flagd` daemon on `$PLANE_HOST:8016`. Attached repositories and cloud jobs need both a pure file-backed JSON flag reader/writer (`adapters/flags-file/`) requiring no running daemon and a generic OFREP HTTP client (`adapters/flags-http/`) with safe header injection (`FLAGS_AUTH_HEADER` via file/stdin, never `curl` argv).
3. **No `REGISTRY` / `FLAGS` conformance suites or `outside/*` admission**: `core/adapters.py` listed `REGISTRY` and `FLAGS` in `NO_SUITE`, blocking external registry/flag plugins under `outside/*`. Both ports need deterministic conformance suites (`gates/registry-conformance.sh` + `core/gates/registry.py`, `gates/flags-conformance.sh` + `core/gates/flags.py`), cold fixtures (`fixtures/outside-adapters/file-registry/`, `fixtures/outside-adapters/file-flags/`), and `ci/outside.sh` coverage.
4. **Cloud Delivery SLO templates required app `/metrics`**: `plugins/cloud/*/slo.promql` only provided OpenTelemetry app-instrumentation queries (`http_server_request_duration_seconds_*` + `target_info`). Uninstrumented services (ADR 0098) need proxy/ingress SLO templates (`slo-ingress.promql`) and a `--slo-mode proxy|otel` selector in `plugins/cloud/setup.py` (default `proxy`).

## Decision

### 1. Generic Remote OCI Registry Adapter (`adapters/oci-registry/`)

Ship `adapters/oci-registry/{env.sh,ready.sh,publish.sh,verify.sh,sbom.sh,README.md}` implementing the Registry port (`ADR 0025`, `ADR 0072`):

- **Configuration (`env.sh`)**:
  - `OCI_REGISTRY_REPO` (required, `<registry>/<repository>`, e.g. `ghcr.io/org/service`, `europe-west1-docker.pkg.dev/proj/repo/service`, `123456789012.dkr.ecr.eu-west-1.amazonaws.com/service`, `myreg.azurecr.io/service`). Refuses empty, `replace-me`, scheme prefixes, `@`, whitespace, or malformed paths.
  - `OCI_REGISTRY_SCHEME`: defaults to `http` only for loopback hosts (`localhost`, `127.0.0.1`, `[::1]`) and `https` everywhere else.
  - `OCI_AUTH_CMD` (optional): shell command invoked to authenticate (e.g. `docker`/`podman` login or token command); if stdout yields a single non-whitespace token line, it is used via `Authorization: Bearer ...` on `curl` stdin (`-H @-`) without printing or placing tokens in `argv`. Standard OCI v2 `WWW-Authenticate: Bearer realm="..."` token exchange and `$DOCKER_CONFIG/config.json` auth are also supported for `curl`.
  - Verification & signing policy: supports keyless OIDC (`COSIGN_CERT_IDENTITY_REGEXP` or `COSIGN_CERT_IDENTITY`, paired with `COSIGN_CERT_OIDC_ISSUER` or `COSIGN_CERT_OIDC_ISSUER_REGEXP`) OR `COSIGN_KMS_KEY` / `COSIGN_KEY` (with optional `COSIGN_PUB_KEY`; `.key` maps to `.pub` for verification when present). Refuses partial keyless config (identity without issuer or vice versa) and fails closed on `verify.sh` if no verification policy is configured.
  - **Static key refusal**: refuses static credential or private-key files inside `$WORKLOAD` (`*.key`, `cosign.key`, `*.pem`, `*.p12`, `*.pfx`, `*credentials*.json`, `*service-account*.json`, `*service_account*.json`, `*-key.json`, `.aws/credentials`, `.aws/config`, `hcloud-token*`, `*kubeconfig*`, or JSON key material) and refuses `COSIGN_KEY` pointing inside `$WORKLOAD`.
- **Verbs**:
  - `ready.sh`: runs `oci_auth` and polls `$OCI_REGISTRY_URL/v2/` (with OCI Bearer token exchange when challenged) up to `READY_TIMEOUT`; silent on success (exit 0), 1 stderr line and exit 1 on failure.
  - `publish.sh <tag>`: builds/pushes `$OCI_REGISTRY_REPO:$tag` using `docker`/`podman` CLI when available (supporting `OCI_SOURCE_IMAGE` or `Containerfile`/`Dockerfile`, or skipping build/push when `OCI_SKIP_PUSH=1`), or `docker-compose` with a generated temporary service image override when `$DOCKER_HOST` is present; resolves immutable digest (`Docker-Content-Digest` or manifest SHA-256); if an image archive is inspectable (`OCI_IMAGE_ARCHIVE`, `$DOCKER_HOST` `/images/<ref>/get`, or `docker`/`podman save`), generates SPDX 2.3 SBOM via `osv-scanner` (accepting exit `0|1`), attests via `cosign attest --type spdxjson`, and verifies via `cosign verify-attestation`; signs `$OCI_REGISTRY_REPO@$digest` via `cosign sign` and verifies signature; prints `<repo>@sha256:...` on stdout. When no local archive is inspectable (e.g. remote already-pushed image without local daemon socket), logs clearly on stderr and proceeds to verify existing digest/attestation and sign cleanly (unless `OCI_REQUIRE_SBOM=1`).
  - `verify.sh <tag-or-ref>`: resolves immutable `sha256:...` digest from the registry manifest FIRST (or validates explicit `@sha256:...` digest ref), verifies the signature against that digest via `cosign verify`, fails closed on any verification or digest mismatch, and prints `<repo>@sha256:...` on stdout.
  - `sbom.sh <repo>@sha256:...`: queries OCI `/v2/<path>/referrers/<digest>` for `dev.sigstore.bundle.predicateType == "https://spdx.dev/Document"`, printing the newest attestation digest `sha256:...` on stdout or exiting 1 with 1 stderr line if absent.

### 2. Daemonless Flags Adapters (`adapters/flags-file/`, `adapters/flags-http/`)

- **`adapters/flags-file/{ready.sh,evaluate.sh,set.sh,README.md}`**:
  - Operates directly on `$FLAGS_FILE` (OFREP/flagd JSON format) with zero running daemon.
  - `ready.sh`: fails closed if `FLAGS_FILE` is unset, missing, invalid JSON, or missing `release-freeze`; silent on success.
  - `evaluate.sh <flag> [version]`: evaluates targeting rules (JSONLogic `if`, `==`, `!=`, `===`, `!==`, `in`, `and`, `or`, `!`, `var` against `{"version": ...}`) and falls back to `defaultVariant`; requires `state` (when present) to equal `"ENABLED"`; prints raw JSON scalar/string on stdout (`false` preserved); fails closed (exit 1, empty stdout, 1 stderr line) on missing file, invalid JSON, disabled/absent flag, or missing variant.
  - `set.sh <flag> <variant>`: validates flag and variant exist, clears `targeting`, updates `defaultVariant`, writes `$FLAGS_FILE`, and confirms via `evaluate.sh`; fails closed on invalid flag/variant/file.
- **`adapters/flags-http/{env.sh,ready.sh,evaluate.sh,set.sh,README.md}`**:
  - Generic OFREP HTTP client for `$FLAGS_URL` (`http://` or `https://`, no embedded credentials in URL).
  - Optional `FLAGS_AUTH_HEADER` passed exclusively via temp `0600` header file (`-H @"$file"`) or `curl` stdin (`-H @-`), NEVER in `curl` `argv` or logs.
  - `ready.sh`: polls `POST $FLAGS_URL/ofrep/v1/evaluate/flags/release-freeze` for valid JSON with `.value`; silent on success, 1 stderr line and exit 1 on failure.
  - `evaluate.sh <flag> [version]`: `POST $FLAGS_URL/ofrep/v1/evaluate/flags/<flag>` with `{"context": {"version": ...}}` (or `{"context": {}}`); prints raw `.value` (`false` preserved); fails closed on HTTP error, invalid JSON, or missing/null `.value`.
  - `set.sh <flag> <variant>`: fails closed with a clear read-only message unless `FLAGS_SET_URL` is configured (posting `{"flag": ..., "variant": ..., "defaultVariant": ...}` with `FLAGS_AUTH_HEADER` via file/stdin and verifying via `evaluate.sh`) or `FLAGS_HTTP_WRITE_FILE=1` explicitly delegates local writes to `$FLAGS_FILE`.

### 3. Conformance Suites & `outside/*` Admission (`REGISTRY` & `FLAGS`)

- **`gates/registry-conformance.sh`** (<= 30 code lines) + **`core/gates/registry.py`** (`core/` 100% vendor/app/model-free):
  - Cold NO-GO unless `CONFORMANCE=1` (`ci/contract.sh` compliant).
  - Six cases against `adapters/$REGISTRY/{ready,publish,verify,sbom}.sh`:
    1. `case1 ready`: optional `ready.sh` exits 0 with empty stdout.
    2. `case2 unsigned-refused`: `verify.sh` on absent/unsigned tag exits non-zero with empty stdout.
    3. `case3 publish-digest`: `publish.sh <tag>` exits 0 and prints 1 immutable `<repo>@sha256:<64hex>` line.
    4. `case4 verify-roundtrip`: `verify.sh <tag>` exits 0 and prints the exact same `<repo>@sha256:<64hex>` reference.
    5. `case5 missing-arg-refused`: `publish.sh ""` and `verify.sh ""` exit non-zero with empty stdout.
    6. `case6 sbom`: if `sbom.sh` exists, absent digest exits non-zero with empty stdout; published digest either prints valid `sha256:<64hex>` (exit 0) or exits non-zero with empty stdout (unless `CONFORMANCE_REQUIRE_SBOM=1`).
- **`gates/flags-conformance.sh`** (<= 30 code lines) + **`core/gates/flags.py`** (`core/` 100% vendor/app/model-free):
  - Cold NO-GO unless `CONFORMANCE=1`.
  - Six cases against `adapters/$FLAGS/{ready,evaluate,set}.sh`, restoring the initial state of `CONFORMANCE_FLAG` (default `release-freeze`) in `finally`:
    1. `case1 ready`: `ready.sh` exits 0 with empty stdout.
    2. `case2 absent-flag-refused`: `evaluate.sh <absent>` exits non-zero with empty stdout.
    3. `case3 evaluate-known`: `evaluate.sh <flag>` and `evaluate.sh <flag> v-conformance` each return `"true"` or `"false"`.
    4. `case4 set-and-evaluate`: `set.sh <flag> on` -> evaluates `"true"`, `set.sh <flag> off` -> evaluates `"false"` (skipped only when `CONFORMANCE_READONLY=1`).
    5. `case5 invalid-set-refused`: `set.sh <absent> on` and `set.sh <flag> <absent-variant>` exit non-zero with empty stdout and leave `<flag>` unchanged.
    6. `case6 missing-arg-refused`: `evaluate.sh ""` and `set.sh "" ""` exit non-zero with empty stdout.
- **`core/adapters.py` & `ci/outside.sh`**:
  - Add `"REGISTRY": "registry-conformance"` and `"FLAGS": "flags-conformance"` to `SUITES`; reduce `NO_SUITE` to `("TRACKER", "ORCHESTRATOR", "METRICS")`.
  - Add cold fixtures `fixtures/outside-adapters/file-registry/` and `fixtures/outside-adapters/file-flags/` and extend `ci/outside.sh` to verify refusal, trial, tamper refusal, broken-copy rejection, registration, admission, 1-byte tamper refusal, and symlink refusal for both `REGISTRY=outside/file-registry` and `FLAGS=outside/file-flags`.

### 4. Cloud Proxy/Ingress SLO Templates & Setup (`plugins/cloud/`)

- Add `plugins/cloud/{gcp-cloud-deploy,aws-ecs-codedeploy,azure-container-apps,hetzner-k3s}/slo-ingress.promql` providing `traffic`, `error_rate`, and `latency_p95` queries from ingress/proxy metrics without requiring app `/metrics` or `target_info`:
  - `gcp-cloud-deploy`, `aws-ecs-codedeploy`, `azure-container-apps`: Envoy upstream cluster metrics (`envoy_cluster_upstream_rq_completed`, `envoy_cluster_upstream_rq_xx{envoy_response_code_class="5"}`, `envoy_cluster_upstream_rq_time_bucket / 1000` grouped by `envoy_cluster_name`).
  - `hetzner-k3s`: Ingress-Nginx controller metrics (`nginx_ingress_controller_requests`, `status=~"5.."`, `nginx_ingress_controller_request_duration_seconds_bucket`) mapping `exported_service` `.*-(stable|canary)$` to `envoy_cluster_name` via `label_replace` so `verify/slo.sh` and `core/gates/readiness.py` consume it natively with zero `target_info`.
- Update `plugins/cloud/setup.py` with `--slo-mode proxy|otel` (default `proxy`, validated with `NO-GO: cloud-setup — ...` on invalid choice) emitting `SLO_PROMQL` for the chosen mode along with `REGISTRY=oci-registry` and `FLAGS=flags-file` / `flags-http` instructions.
- Update `plugins/cloud/README.md` and `plugins/cloud/*/README.md` documenting proxy vs OTel SLO modes, `oci-registry`, and `flags-file`/`flags-http`, preserving the exact stub-CLI vs live-cloud honesty notice.

## Alternatives rejected

- **Vendor-specific registry adapters (`ghcr`, `gar`, `ecr`, `acr`)**: Rejected — all four serve OCI Distribution API v2 + `cosign` signatures; a single parameterized `adapters/oci-registry/` keeps the adapter catalog lean without duplicating auth/SBOM logic.
- **Pass `FLAGS_AUTH_HEADER` via `curl -H "$FLAGS_AUTH_HEADER"`**: Rejected — leaks secret headers into process `argv`; reading from stdin (`-H @-`) or a `0600` temp file (`-H @"$file"`) keeps secrets out of `ps`.
- **Fallback from `flags-http/set.sh` to `$FLAGS_FILE` automatically**: Rejected — `Makefile` exports `FLAGS_FILE` globally; silent fallback would mutate a local JSON file while remote `FLAGS_URL` stays unchanged. Fail-closed unless `FLAGS_SET_URL` or explicit `FLAGS_HTTP_WRITE_FILE=1` is set.
- **Require app `/metrics` for cloud deployments**: Rejected — contradicts ADR 0098's uninstrumented workload design; keeping `slo-ingress.promql` as the default `--slo-mode proxy` lets uninstrumented containers deliver to cloud plugins while keeping `slo.promql` for `--slo-mode otel`.

## Consequences

- `REGISTRY` and `FLAGS` can now be plugged externally (`outside/*`) with full hash-pinned conformance admission (`registry-conformance`, `flags-conformance`).
- Teams deploying via `plugins/cloud/*` have concrete, vendor-neutral `REGISTRY=oci-registry`, `FLAGS=flags-file` / `flags-http`, and proxy SLO PromQL templates without running local Zot or `flagd` daemons.
- **Honest stub vs live boundary**: local CI verifies `oci-registry` against PATH stub CLIs (`cosign`, `osv-scanner`, `docker`, `podman`) and local Python HTTP servers, and `flags-file`/`flags-http` against real temp files and local Python OFREP HTTP servers. Live remote registry pushes, KMS/Fulcio OIDC signing, and real cloud ingress Prometheus endpoints require operator accounts and `CONFORMANCE=1` conformance runs.

## Golden-rule check

`git clone && ./go demo` and L0 `zot` / `flagd` / `envoy-canary` remain untouched. `./go ci` gains cold unit/fixture checks (`core/tests/test_cloud_e2e.py`, `ci/tests/test_oci_registry_and_flags.py`, extended `ci/outside.sh`) with zero network calls, zero live cloud accounts, and zero extra containers.
