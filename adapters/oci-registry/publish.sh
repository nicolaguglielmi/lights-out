#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# publish-oci-registry: builds (unless skipped), pushes to $OCI_REGISTRY_REPO:<tag>,
# resolves immutable digest, generates & attests SPDX 2.3 SBOM via osv-scanner +
# cosign attest when an image archive is inspectable, signs the digest via cosign,
# verifies signature, and prints <repo>@sha256:... on stdout.
set -eu
tag=${1:?usage: publish.sh <tag>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/oci-registry/env.sh
. "$here/env.sh"

oci_auth || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

pick_engine_cli() {
  if [ -n "${CONTAINER_ENGINE:-}" ] && command -v "$CONTAINER_ENGINE" >/dev/null 2>&1; then
    printf '%s' "$CONTAINER_ENGINE"
    return 0
  fi
  for _c in docker podman; do
    if command -v "$_c" >/dev/null 2>&1; then
      printf '%s' "$_c"
      return 0
    fi
  done
  return 1
}

target_ref="$OCI_REGISTRY_REPO:$tag"
engine_cli=$(pick_engine_cli || true)

if [ "${OCI_SKIP_PUSH:-0}" != "1" ]; then
  if [ -n "$engine_cli" ]; then
    if [ -n "${OCI_SOURCE_IMAGE:-}" ]; then
      "$engine_cli" tag "$OCI_SOURCE_IMAGE" "$target_ref" >&2 \
        || { printf 'publish-oci-registry: %s tag %s -> %s failed\n' "$engine_cli" "$OCI_SOURCE_IMAGE" "$target_ref" >&2; exit 1; }
    elif [ "${OCI_SKIP_BUILD:-0}" != "1" ] && [ -n "${WORKLOAD:-}" ] && [ -d "${WORKLOAD:-}" ]; then
      if [ -f "$WORKLOAD/Containerfile" ]; then
        "$engine_cli" build -t "$target_ref" -f "$WORKLOAD/Containerfile" "$WORKLOAD" >&2 \
          || { printf 'publish-oci-registry: %s build %s failed\n' "$engine_cli" "$target_ref" >&2; exit 1; }
      elif [ -f "$WORKLOAD/Dockerfile" ]; then
        "$engine_cli" build -t "$target_ref" -f "$WORKLOAD/Dockerfile" "$WORKLOAD" >&2 \
          || { printf 'publish-oci-registry: %s build %s failed\n' "$engine_cli" "$target_ref" >&2; exit 1; }
      fi
    fi
    "$engine_cli" push "$target_ref" >&2 \
      || { printf 'publish-oci-registry: %s push %s failed\n' "$engine_cli" "$target_ref" >&2; exit 1; }
  elif command -v docker-compose >/dev/null 2>&1 && [ -n "${DOCKER_HOST:-}" ]; then
    compose_list=${COMPOSE_FILE:-}
    [ -n "$compose_list" ] || [ ! -f compose.yaml ] || compose_list=compose.yaml
    [ -n "$compose_list" ] || { printf 'publish-oci-registry: COMPOSE_FILE not set and no container CLI on PATH\n' >&2; exit 1; }
    printf 'services:\n  backend:\n    image: %s\n' "$target_ref" > "$tmp/oci-override.yml"
    old_ifs=$IFS
    IFS=:
    set --
    for _cf in $compose_list; do
      [ -z "$_cf" ] || set -- "$@" -f "$_cf"
    done
    IFS=$old_ifs
    set -- "$@" -f "$tmp/oci-override.yml"
    if [ "${OCI_SKIP_BUILD:-0}" != "1" ]; then
      APP_TAG="$tag" docker-compose "$@" build backend >&2 \
        || { printf 'publish-oci-registry: docker-compose build %s failed\n' "$target_ref" >&2; exit 1; }
    fi
    APP_TAG="$tag" docker-compose "$@" push backend >&2 \
      || { printf 'publish-oci-registry: docker-compose push %s failed\n' "$target_ref" >&2; exit 1; }
  else
    printf 'publish-oci-registry: no container CLI or DOCKER_HOST + docker-compose to push %s (set OCI_SKIP_PUSH=1 if already pushed)\n' "$target_ref" >&2
    exit 1
  fi
fi

digest=$(oci_resolve_digest "$tag") \
  || { printf 'publish-oci-registry: could not resolve digest for %s\n' "$target_ref" >&2; exit 1; }
immutable_ref="$OCI_REGISTRY_REPO@$digest"

archive_path=""
if [ -n "${OCI_IMAGE_ARCHIVE:-}" ] && [ -f "$OCI_IMAGE_ARCHIVE" ]; then
  archive_path=$OCI_IMAGE_ARCHIVE
elif [ -n "$engine_cli" ]; then
  if "$engine_cli" save -o "$tmp/image.tar" "$target_ref" >/dev/null 2>&1; then
    archive_path="$tmp/image.tar"
  fi
fi
if [ -z "$archive_path" ] && [ -n "${DOCKER_HOST:-}" ]; then
  sock=${DOCKER_HOST#unix://}
  if [ -S "$sock" ]; then
    enc_ref=$(oci_url_encode "$target_ref")
    if curl -fsS --unix-socket "$sock" -o "$tmp/image.tar" "http://engine/images/$enc_ref/get" 2>/dev/null; then
      archive_path="$tmp/image.tar"
    fi
  fi
fi

if [ -n "$archive_path" ]; then
  osv-scanner scan image --archive "$archive_path" --format spdx-2-3 --all-packages \
      --output-file "$tmp/sbom.spdx.json" >/dev/null 2>"$tmp/osv.err" && rc=0 || rc=$?
  case "$rc" in
    0|1) : ;;
    *)
      cat "$tmp/osv.err" >&2
      printf 'publish-oci-registry: osv-scanner exited %s on %s\n' "$rc" "$target_ref" >&2
      exit 1 ;;
  esac
  cosign_attest_spdx "$tmp/sbom.spdx.json" "$immutable_ref" \
    || { printf 'publish-oci-registry: cosign attest SBOM failed for %s\n' "$immutable_ref" >&2; exit 1; }
  if [ -n "${COSIGN_KMS_KEY:-}${COSIGN_KEY:-}${COSIGN_PUB_KEY:-}${COSIGN_CERT_IDENTITY_REGEXP:-}${COSIGN_CERT_IDENTITY:-}" ]; then
    cosign_verify_attestation_spdx "$immutable_ref" \
      || { printf 'publish-oci-registry: SBOM attestation verification failed for %s\n' "$immutable_ref" >&2; exit 1; }
  fi
elif [ "${OCI_REQUIRE_SBOM:-0}" = "1" ]; then
  printf 'publish-oci-registry: OCI_REQUIRE_SBOM=1 but no local image archive is inspectable for %s\n' "$target_ref" >&2
  exit 1
else
  printf 'publish-oci-registry: no local image archive inspectable for %s; skipping local SBOM generation\n' "$target_ref" >&2
fi

if [ "${OCI_SIGN_SKIP:-0}" != "1" ]; then
  cosign_sign_image "$immutable_ref" \
    || { printf 'publish-oci-registry: cosign sign failed for %s\n' "$immutable_ref" >&2; exit 1; }
fi

if [ -n "${COSIGN_KMS_KEY:-}${COSIGN_KEY:-}${COSIGN_PUB_KEY:-}${COSIGN_CERT_IDENTITY_REGEXP:-}${COSIGN_CERT_IDENTITY:-}" ]; then
  cosign_verify_image "$immutable_ref" \
    || { printf 'publish-oci-registry: signature verification failed for %s\n' "$immutable_ref" >&2; exit 1; }
fi

printf '%s\n' "$immutable_ref"
