#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# sbom-oci-registry: queries OCI Distribution referrers for <repo>@sha256:...
# and prints the newest SPDX 2.3 attestation digest (sha256:...) on stdout.
set -eu
ref=${1:?usage: sbom.sh <repo>@sha256:...}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/oci-registry/env.sh
. "$here/env.sh"

oci_auth || exit 1

case "$ref" in
  *@sha256:*) digest="sha256:${ref#*@sha256:}" ;;
  sha256:*) digest=$ref ;;
  *)
    printf 'sbom-oci-registry: expected <repo>@sha256:... reference, got %s\n' "$ref" >&2
    exit 1 ;;
esac
printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' \
  || { printf 'sbom-oci-registry: invalid digest in %s\n' "$ref" >&2; exit 1; }

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

code=$(oci_curl GET "$OCI_REGISTRY_URL/v2/$OCI_REGISTRY_PATH/referrers/$digest" "$tmp/body" "$tmp/hdrs" \
  'application/vnd.oci.image.index.v1+json')
[ "$code" = "200" ] || { printf 'sbom-oci-registry: %s has no SPDX attestation (HTTP %s)\n' "$ref" "$code" >&2; exit 1; }

got=$(jq -r '[.manifests[]? | select(.annotations["dev.sigstore.bundle.predicateType"] == "https://spdx.dev/Document")]
             | sort_by(.annotations["org.opencontainers.image.created"] // "") | last | .digest // empty' "$tmp/body" 2>/dev/null) || got=""
[ -n "$got" ] || { printf 'sbom-oci-registry: %s has no SPDX attestation\n' "$ref" >&2; exit 1; }

printf '%s\n' "$got"
