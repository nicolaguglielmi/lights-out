#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# verify-oci-registry: resolves <tag-or-ref> to an immutable sha256 digest
# FIRST, verifies the cosign signature on that digest (fail-closed if no
# verification policy is set), and prints <repo>@sha256:... on stdout.
set -eu
ref_in=${1:?usage: verify.sh <tag-or-ref>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/oci-registry/env.sh
. "$here/env.sh"

oci_auth || exit 1

case "$ref_in" in
  sha256:*)
    digest=$ref_in
    printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' \
      || { printf 'verify-oci-registry: invalid digest %s\n' "$digest" >&2; exit 1; } ;;
  *@sha256:*)
    digest="sha256:${ref_in#*@sha256:}"
    printf '%s' "$digest" | grep -Eq '^sha256:[0-9a-f]{64}$' \
      || { printf 'verify-oci-registry: invalid digest reference %s\n' "$ref_in" >&2; exit 1; } ;;
  *)
    tag=$ref_in
    case "$tag" in
      "$OCI_REGISTRY_REPO":*) tag=${tag#"$OCI_REGISTRY_REPO":} ;;
    esac
    digest=$(oci_resolve_digest "$tag") \
      || { printf 'verify-oci-registry: could not resolve digest for %s:%s\n' "$OCI_REGISTRY_REPO" "$tag" >&2; exit 1; } ;;
esac

immutable_ref="$OCI_REGISTRY_REPO@$digest"
cosign_verify_image "$immutable_ref" \
  || { printf 'verify-oci-registry: %s does not verify\n' "$immutable_ref" >&2; exit 1; }

printf '%s\n' "$immutable_ref"
