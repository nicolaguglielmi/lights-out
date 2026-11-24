#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready.sh (azure-container-apps): verifies provisioningState=Succeeded,
# runningStatus=Running, and HTTP health probe.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli az jq curl

probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}
case "$probe" in
  http:/*) probe_path=${probe#http:} ;;
  *) probe_path=/healthz ;;
esac

start=$(date +%s)
prov="absent"
running="absent"
check_url=""
while :; do
  if app_json=$(az containerapp show \
      --subscription "$AZURE_SUBSCRIPTION_ID" \
      --resource-group "$AZURE_RESOURCE_GROUP" \
      --name "$AZURE_CONTAINER_APP" \
      --output json 2>/dev/null); then
    prov=$(printf '%s' "$app_json" | jq -r '.properties.provisioningState // "Unknown"')
    running=$(printf '%s' "$app_json" | jq -r '.properties.runningStatus // "Unknown"')
    fqdn=$(printf '%s' "$app_json" | jq -r '.properties.configuration.ingress.fqdn // empty')
    check_url=${APP_URL:-${fqdn:+https://$fqdn}}
    if [ "$prov" = "Succeeded" ] && [ "$running" = "Running" ] && [ -n "$check_url" ] \
       && curl -fsS --connect-timeout 2 --max-time 5 "$check_url$probe_path" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$AZURE_POLL_INTERVAL"
done
printf 'ready: azure-container-apps not ready after %ss (app %s prov=%s run=%s url=%s)\n' \
  "$READY_TIMEOUT" "$AZURE_CONTAINER_APP" "$prov" "$running" "${check_url:-none}" >&2
exit 1
