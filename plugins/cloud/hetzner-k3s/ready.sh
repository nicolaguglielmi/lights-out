#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready.sh (hetzner-k3s): verifies Argo Rollout is Healthy/Paused, checks
# hcloud cluster servers when hcloud is on PATH, and probes HTTP /healthz.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq curl

probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}
case "$probe" in
  http:/*) probe_path=${probe#http:} ;;
  *) probe_path=/healthz ;;
esac

if command -v hcloud >/dev/null 2>&1; then
  running_nodes=$(hcloud server list -l "cluster=$HETZNER_CLUSTER" -o json 2>/dev/null \
    | jq -r '[.[] | select(.status == "running")] | length' 2>/dev/null) || running_nodes=0
  [ "$running_nodes" -ge 1 ] || {
    printf 'ready: hetzner-k3s cluster %s has no running hcloud servers\n' "$HETZNER_CLUSTER" >&2
    exit 1
  }
fi

start=$(date +%s)
phase="absent"
check_url=${APP_URL:-}
while :; do
  phase=$(rollout_json 2>/dev/null | jq -r '.status.phase // empty') || phase=""
  if [ -z "$check_url" ]; then
    ing=$(k get ingress "$HETZNER_CANARY_INGRESS" -o json 2>/dev/null) || ing="{}"
    host=$(printf '%s' "$ing" | jq -r '.spec.rules[0].host // .status.loadBalancer.ingress[0].ip // empty')
    [ -z "$host" ] || check_url="https://$host"
  fi
  case "$phase" in
    Healthy|Paused)
      if [ -n "$check_url" ] && curl -fsS --connect-timeout 2 --max-time 5 "$check_url$probe_path" >/dev/null 2>&1; then
        exit 0
      fi ;;
  esac
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$HETZNER_POLL_INTERVAL"
done
printf 'ready: hetzner-k3s not ready after %ss (rollout %s/%s phase=%s url=%s)\n' \
  "$READY_TIMEOUT" "$HETZNER_NAMESPACE" "$HETZNER_ROLLOUT" "${phase:-absent}" "${check_url:-none}" >&2
exit 1
