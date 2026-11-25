#!/bin/sh
# shellcheck disable=SC2034 # variables are consumed by delegating scripts and oci-registry/env.sh
# SPDX-License-Identifier: Apache-2.0
#
# artifact-registry env.sh: resolves GCP Artifact Registry repository and OIDC /
# ADC Bearer token (ARTIFACT_REGISTRY_TOKEN / ARTIFACT_REGISTRY_AUTH_CMD /
# gcloud auth print-access-token / GCE metadata) into OCI_REGISTRY_USER=
# oauth2accesstoken and OCI_REGISTRY_TOKEN for adapters/oci-registry/.
set -eu

OCI_REGISTRY_REPO=${OCI_REGISTRY_REPO:-${ARTIFACT_REGISTRY_REPO:-}}
if [ -z "$OCI_REGISTRY_REPO" ]; then
  _ar_proj=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}
  _ar_region=${GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-}}
  _ar_host=${GCP_AR_HOST:-${_ar_region:+${_ar_region}-docker.pkg.dev}}
  _ar_repo=${ARTIFACT_REGISTRY_REPOSITORY:-}
  _ar_pkg=${ARTIFACT_REGISTRY_IMAGE:-${CLOUD_RUN_SERVICE:-}}
  if [ -n "$_ar_host" ] && [ -n "$_ar_proj" ] && [ "$_ar_proj" != "replace-me" ] && [ -n "$_ar_repo" ]; then
    OCI_REGISTRY_REPO="$_ar_host/$_ar_proj/$_ar_repo${_ar_pkg:+/$_ar_pkg}"
  fi
fi

if [ -z "$OCI_REGISTRY_REPO" ] || [ "$OCI_REGISTRY_REPO" = "replace-me" ]; then
  printf 'artifact-registry: OCI_REGISTRY_REPO (or ARTIFACT_REGISTRY_REPO / GCP_PROJECT + GCP_REGION + ARTIFACT_REGISTRY_REPOSITORY) is empty or placeholder\n' >&2
  exit 1
fi
export OCI_REGISTRY_REPO

_ar_token=""
for _cand in \
  "${ARTIFACT_REGISTRY_TOKEN:-}" \
  "${OCI_REGISTRY_TOKEN:-}" \
  "${GCP_ACCESS_TOKEN:-}" \
  "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" \
  "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"; do
  if [ -n "$_cand" ] && [ "$_cand" != "replace-me" ]; then
    _ar_token=$_cand
    break
  fi
done

if [ -z "$_ar_token" ] && [ -n "${ARTIFACT_REGISTRY_AUTH_CMD:-}" ]; then
  _auth_out=$(sh -c "$ARTIFACT_REGISTRY_AUTH_CMD") || {
    printf 'artifact-registry: ARTIFACT_REGISTRY_AUTH_CMD failed\n' >&2
    exit 1
  }
  _auth_out=$(printf '%s' "$_auth_out" | tr -d '\r\n')
  if [ -n "$_auth_out" ]; then
    if printf '%s' "$_auth_out" | grep -Eq '^[^[:space:]]+$'; then
      _ar_token=$_auth_out
    else
      printf '%s\n' "$_auth_out" >&2
    fi
  fi
fi

if [ -z "$_ar_token" ] && command -v gcloud >/dev/null 2>&1; then
  _tok=$(gcloud auth print-access-token 2>/dev/null \
    || gcloud auth application-default print-access-token 2>/dev/null || true)
  _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
  if [ -n "$_tok" ]; then
    _ar_token=$_tok
  fi
fi

if [ -z "$_ar_token" ]; then
  _meta_url=${GCP_METADATA_URL:-}
  if [ -z "$_meta_url" ]; then
    case "$OCI_REGISTRY_REPO" in
      *-docker.pkg.dev/*) _meta_url="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" ;;
    esac
  fi
  if [ -n "$_meta_url" ]; then
    _meta_json=$(curl -fsS --connect-timeout 2 --max-time 5 \
      -H 'Metadata-Flavor: Google' "$_meta_url" 2>/dev/null) || _meta_json=""
    if [ -n "$_meta_json" ]; then
      _tok=$(printf '%s' "$_meta_json" | jq -r '.access_token // empty' 2>/dev/null) || _tok=""
      if [ -n "$_tok" ]; then
        _ar_token=$_tok
      fi
    fi
  fi
fi

OCI_REGISTRY_USER=${OCI_REGISTRY_USER:-oauth2accesstoken}
export OCI_REGISTRY_USER
if [ -n "$_ar_token" ]; then
  OCI_REGISTRY_TOKEN=$_ar_token
  export OCI_REGISTRY_TOKEN
fi
