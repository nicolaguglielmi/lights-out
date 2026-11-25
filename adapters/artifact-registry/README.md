# Google Artifact Registry Adapter (`REGISTRY=artifact-registry`)

`Registry` port wrapper for Google Artifact Registry (`*-docker.pkg.dev/$GCP_PROJECT/...`), resolving OIDC / ADC Bearer tokens into `OCI_REGISTRY_USER=oauth2accesstoken` + `OCI_REGISTRY_TOKEN` and delegating to `adapters/oci-registry/`.

> Contract-tested in CI (`ci/tests/test_managed_cloud_conformance.py`) via `CONFORMANCE=1 REGISTRY=artifact-registry sh gates/registry-conformance.sh` against a local OCI + `cosign` stub harness; live remote pushes and keyless Sigstore signing require your own Artifact Registry repository.

## Configuration

- `OCI_REGISTRY_REPO` (or `ARTIFACT_REGISTRY_REPO`): `<region>-docker.pkg.dev/<project>/<repository>/<image>`. Alternatively set `GCP_PROJECT`, `GCP_REGION` (or `GCP_AR_HOST`), and `ARTIFACT_REGISTRY_REPOSITORY` (plus optional `ARTIFACT_REGISTRY_IMAGE` / `CLOUD_RUN_SERVICE`).
- `ARTIFACT_REGISTRY_TOKEN` / `ARTIFACT_REGISTRY_AUTH_CMD` (optional; falls back to `OCI_REGISTRY_TOKEN`, `GCP_ACCESS_TOKEN`, `CLOUDSDK_AUTH_ACCESS_TOKEN`, `GOOGLE_OAUTH_ACCESS_TOKEN`, `gcloud auth print-access-token`, or GCE metadata server): resolved into `OCI_REGISTRY_USER=oauth2accesstoken` and `OCI_REGISTRY_TOKEN` (sent to HTTP endpoints via `-H @file`, never `argv`).
- Verification and SBOM settings follow [oci-registry](../oci-registry/README.md) (`COSIGN_CERT_IDENTITY_REGEXP`, `COSIGN_CERT_OIDC_ISSUER`, `COSIGN_KMS_KEY`, `COSIGN_KEY`).

## Verbs

- `ready.sh`: delegates to `adapters/oci-registry/ready.sh`.
- `publish.sh <tag>`: builds/pushes if configured, attests SPDX 2.3 SBOM, signs digest, verifies, and prints `<repo>@sha256:...`.
- `verify.sh <tag-or-ref>`: resolves digest first, verifies signature, and prints `<repo>@sha256:...`.
- `sbom.sh <repo>@sha256:...`: resolves newest SPDX 2.3 attestation digest (`sha256:...`).
