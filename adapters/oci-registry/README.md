# Generic Remote OCI Registry Adapter (`REGISTRY=oci-registry`)

Generic remote OCI Distribution v2 Registry adapter supporting GHCR (`ghcr.io`), Google Artifact Registry (`*-docker.pkg.dev`), AWS ECR (`*.dkr.ecr.*.amazonaws.com`), Azure Container Registry (`*.azurecr.io`), and custom OCI registries.

> Tested locally in CI against PATH stub CLIs (`cosign`, `osv-scanner`, `docker`, `docker-compose`) and a local HTTP OCI test server (`ci/tests/test_oci_registry_and_flags.py`); live remote push, OIDC/KMS signing, and SBOM attestation require your own registry repository and `CONFORMANCE=1 REGISTRY=oci-registry sh gates/registry-conformance.sh`.

## Required configuration

- `OCI_REGISTRY_REPO`: `<registry>/<repository>` without scheme or trailing slash (for example `ghcr.io/owner/service` or `europe-west1-docker.pkg.dev/project/repo/service`).

## Optional configuration

- `OCI_AUTH_CMD`: shell command executed to authenticate (such as `docker login` via cloud OIDC helper or a command printing a single bearer token on stdout).
- `OCI_REGISTRY_SCHEME`: `https` (default everywhere except loopback hosts) or `http`.
- Keyless OIDC verification: `COSIGN_CERT_IDENTITY_REGEXP` (or `COSIGN_CERT_IDENTITY`) paired with `COSIGN_CERT_OIDC_ISSUER` (or `COSIGN_CERT_OIDC_ISSUER_REGEXP`).
- Key/KMS signing & verification: `COSIGN_KMS_KEY` (for example `gcpkms://...`, `awskms://...`, `azurekms://...`) or `COSIGN_KEY` / `COSIGN_PUB_KEY` stored outside `$WORKLOAD`.
- `READY_TIMEOUT`: readiness poll ceiling in seconds (default `60`).

## Security guarantees

- Static key refusal: `env.sh` refuses static keys and credential files inside `$WORKLOAD` (`*.key`, `cosign.key`, `*.pem`, `*.p12`, `*.pfx`, `*credentials*.json`, `*service-account*.json`, `.aws/credentials`) and refuses `COSIGN_KEY` pointing inside `$WORKLOAD`.
- Digest-first verification: `verify.sh` resolves `<tag>` to an immutable `sha256:...` manifest digest before calling `cosign verify` and fails closed if no verification policy is set.
- SPDX 2.3 SBOM: when a local image archive is inspectable (`OCI_IMAGE_ARCHIVE`, `docker`/`podman save`, or `$DOCKER_HOST` socket `/images/.../get`), `publish.sh` generates an SPDX 2.3 SBOM via `osv-scanner` and attests it via `cosign attest` before signing. When no local archive is inspectable (for instance verifying or signing an already-pushed remote digest), `publish.sh` skips local archive scanning cleanly unless `OCI_REQUIRE_SBOM=1` is set.
