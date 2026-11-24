#!/bin/sh
# shellcheck disable=SC2034 # variables here are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# gcp-cloud-deploy env.sh: validates Cloud Run / Cloud Deploy settings and
# refuses static credentials in env or $WORKLOAD. Sourced by plugin verbs.
set -eu

GCP_PROJECT=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}
GCP_REGION=${GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-}}
CLOUD_DEPLOY_PIPELINE=${CLOUD_DEPLOY_PIPELINE:-}
CLOUD_DEPLOY_TARGET=${CLOUD_DEPLOY_TARGET:-prod}
CLOUD_RUN_SERVICE=${CLOUD_RUN_SERVICE:-backend}
GCP_AR_HOST=${GCP_AR_HOST:-${GCP_REGION}-docker.pkg.dev}
READY_TIMEOUT=${READY_TIMEOUT:-180}
CLOUD_DEPLOY_POLL_INTERVAL=${CLOUD_DEPLOY_POLL_INTERVAL:-1}

if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "replace-me" ]; then
  printf 'gcp-cloud-deploy: GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) is empty or placeholder\n' >&2
  exit 1
fi
if [ -z "$GCP_REGION" ]; then
  printf 'gcp-cloud-deploy: GCP_REGION (or GOOGLE_CLOUD_LOCATION) is empty\n' >&2
  exit 1
fi
if [ -z "$CLOUD_DEPLOY_PIPELINE" ]; then
  printf 'gcp-cloud-deploy: CLOUD_DEPLOY_PIPELINE is empty\n' >&2
  exit 1
fi

if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]; then
  printf 'gcp-cloud-deploy: GOOGLE_APPLICATION_CREDENTIALS is set; use keyless OIDC Workload Identity instead of static JSON keys\n' >&2
  exit 1
fi

check_no_static_creds() {
  _wl=${WORKLOAD:-}
  [ -n "$_wl" ] && [ -d "$_wl" ] || return 0
  _found=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f \( \
      -path '*/.aws/credentials' -o -path '*/.aws/config' -o \
      -name '*credentials*.json' -o -name '*service-account*.json' -o -name '*service_account*.json' -o \
      -name '*-key.json' -o -name '*.pem' -o -name '*.p12' -o -name '*.pfx' -o \
      -name 'hcloud-token*' -o -name '*kubeconfig*' \
    \) -print 2>/dev/null | head -n 1 || true)
  if [ -n "$_found" ]; then
    printf 'gcp-cloud-deploy: static credential file refused in WORKLOAD: %s\n' "$_found" >&2
    exit 1
  fi
  _json_hit=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f -name '*.json' ! -name 'package-lock.json' -print 2>/dev/null | while IFS= read -r _jf; do
      if grep -Eq '"type"[[:space:]]*:[[:space:]]*"service_account"|"private_key_id"[[:space:]]*:|"client_secret"[[:space:]]*:|"clientSecret"[[:space:]]*:|"aws_secret_access_key"[[:space:]]*:' "$_jf" 2>/dev/null; then
        printf '%s\n' "$_jf"
        break
      fi
    done || true)
  if [ -n "$_json_hit" ]; then
    printf 'gcp-cloud-deploy: static credential JSON file refused in WORKLOAD: %s\n' "$_json_hit" >&2
    exit 1
  fi
}

check_no_static_creds

require_cli() {
  for _tool in "$@"; do
    command -v "$_tool" >/dev/null 2>&1 || {
      printf 'gcp-cloud-deploy: %s is not on PATH\n' "$_tool" >&2
      exit 1
    }
  done
}

resolve_revision_version() {
  _rev=$1
  _rev_json=$(gcloud run revisions describe "$_rev" \
    --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json) || return 1
  printf '%s' "$_rev_json" | jq -er '
    (.metadata.labels["lights-out.version"] // .metadata.annotations["lights-out.version"]
     // ([.spec.containers[0].env[]? | select(.name == "APP_VERSION") | .value] | first)
     // (.spec.containers[0].image | split("@")[1] // (split(":") | last)))
  '
}
