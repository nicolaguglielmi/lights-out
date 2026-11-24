#!/bin/sh
# shellcheck disable=SC2034 # variables here are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# hetzner-k3s env.sh: validates Hetzner Cloud k3s cluster/kubeconfig config
# and refuses static credential files inside $WORKLOAD.
set -eu

HETZNER_CLUSTER=${HETZNER_CLUSTER:-}
HETZNER_KUBECONFIG=${HETZNER_KUBECONFIG:-${KUBECONFIG:-}}
HETZNER_NAMESPACE=${HETZNER_NAMESPACE:-lights-out}
HETZNER_ROLLOUT=${HETZNER_ROLLOUT:-backend}
HETZNER_CANARY_INGRESS=${HETZNER_CANARY_INGRESS:-backend-canary}
HETZNER_HEALTH_PORT=${HETZNER_HEALTH_PORT:-8000}
HETZNER_REGISTRY_HOST=${HETZNER_REGISTRY_HOST:-ghcr.io}
READY_TIMEOUT=${READY_TIMEOUT:-180}
HETZNER_POLL_INTERVAL=${HETZNER_POLL_INTERVAL:-1}

if [ -z "$HETZNER_CLUSTER" ]; then
  printf 'hetzner-k3s: HETZNER_CLUSTER is empty\n' >&2
  exit 1
fi
if [ -z "$HETZNER_KUBECONFIG" ]; then
  printf 'hetzner-k3s: HETZNER_KUBECONFIG (or KUBECONFIG) is empty\n' >&2
  exit 1
fi

check_no_static_creds() {
  _wl=${WORKLOAD:-}
  [ -n "$_wl" ] && [ -d "$_wl" ] || return 0
  case "$HETZNER_KUBECONFIG" in
    "$_wl"/*)
      printf 'hetzner-k3s: HETZNER_KUBECONFIG must not live inside WORKLOAD (%s)\n' "$HETZNER_KUBECONFIG" >&2
      exit 1 ;;
  esac
  _found=$(find "$_wl" -maxdepth 4 \
    \( -name .git -o -name node_modules -o -name .venv -o -name __pycache__ \) -prune -o \
    -type f \( \
      -path '*/.aws/credentials' -o -path '*/.aws/config' -o \
      -name '*credentials*.json' -o -name '*service-account*.json' -o -name '*service_account*.json' -o \
      -name '*-key.json' -o -name '*.pem' -o -name '*.p12' -o -name '*.pfx' -o \
      -name 'hcloud-token*' -o -name '*kubeconfig*' \
    \) -print 2>/dev/null | head -n 1 || true)
  if [ -n "$_found" ]; then
    printf 'hetzner-k3s: static credential file refused in WORKLOAD: %s\n' "$_found" >&2
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
    printf 'hetzner-k3s: static credential JSON file refused in WORKLOAD: %s\n' "$_json_hit" >&2
    exit 1
  fi
}

check_no_static_creds

require_cli() {
  for _tool in "$@"; do
    command -v "$_tool" >/dev/null 2>&1 || {
      printf 'hetzner-k3s: %s is not on PATH\n' "$_tool" >&2
      exit 1
    }
  done
}

k() {
  kubectl --kubeconfig "$HETZNER_KUBECONFIG" --namespace "$HETZNER_NAMESPACE" "$@"
}

rollout_json() {
  k get rollout "$HETZNER_ROLLOUT" -o json
}

status_patch() {
  k patch rollout "$HETZNER_ROLLOUT" --subresource=status --type merge -p "$1" >/dev/null
}

version_of() {
  _pod=$(k get pods -l "rollouts-pod-template-hash=$1" -o json \
    | jq -r '[.items[] | select(.metadata.deletionTimestamp == null)
              | select(any(.status.conditions[]?; .type == "Ready" and .status == "True"))
              | .metadata.name] | first // empty')
  [ -n "$_pod" ] || return 1
  kubectl --kubeconfig "$HETZNER_KUBECONFIG" get --raw \
    "/api/v1/namespaces/$HETZNER_NAMESPACE/pods/$_pod:$HETZNER_HEALTH_PORT/proxy/healthz" 2>/dev/null \
    | jq -er .version
}

weight_of() {
  printf '%s' "$1" | jq -r '.status.canary.weights.canary.weight // 0'
}
