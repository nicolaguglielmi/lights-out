#!/bin/sh
# shellcheck disable=SC2034 # variables here are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# azure-container-apps env.sh: validates Azure Container Apps config and refuses
# static client secrets or credential files in $WORKLOAD.
set -eu

AZURE_SUBSCRIPTION_ID=${AZURE_SUBSCRIPTION_ID:-}
AZURE_RESOURCE_GROUP=${AZURE_RESOURCE_GROUP:-}
AZURE_CONTAINER_APP=${AZURE_CONTAINER_APP:-}
AZURE_CONTAINER_NAME=${AZURE_CONTAINER_NAME:-backend}
READY_TIMEOUT=${READY_TIMEOUT:-180}
AZURE_POLL_INTERVAL=${AZURE_POLL_INTERVAL:-1}

if [ -z "$AZURE_SUBSCRIPTION_ID" ]; then
  printf 'azure-container-apps: AZURE_SUBSCRIPTION_ID is empty\n' >&2
  exit 1
fi
if [ -z "$AZURE_RESOURCE_GROUP" ]; then
  printf 'azure-container-apps: AZURE_RESOURCE_GROUP is empty\n' >&2
  exit 1
fi
if [ -z "$AZURE_CONTAINER_APP" ]; then
  printf 'azure-container-apps: AZURE_CONTAINER_APP is empty\n' >&2
  exit 1
fi

if [ -n "${ARM_CLIENT_SECRET:-}${AZURE_CLIENT_SECRET:-}" ]; then
  printf 'azure-container-apps: static ARM_CLIENT_SECRET/AZURE_CLIENT_SECRET refused; use OIDC federated credentials\n' >&2
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
    printf 'azure-container-apps: static credential file refused in WORKLOAD: %s\n' "$_found" >&2
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
    printf 'azure-container-apps: static credential JSON file refused in WORKLOAD: %s\n' "$_json_hit" >&2
    exit 1
  fi
}

check_no_static_creds

require_cli() {
  for _tool in "$@"; do
    command -v "$_tool" >/dev/null 2>&1 || {
      printf 'azure-container-apps: %s is not on PATH\n' "$_tool" >&2
      exit 1
    }
  done
}
