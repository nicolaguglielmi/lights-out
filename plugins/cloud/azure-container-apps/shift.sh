#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# shift.sh (azure-container-apps): sets Container App label traffic weights for
# canary percentages 5, 25, or 100.
set -eu
percent=${1:?usage: shift.sh <canary-percent>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli az jq

case "$percent" in
  5|25|100) ;;
  *)
    printf 'shift-azure-container-apps: invalid canary percent %s (allowed: 5, 25, 100)\n' "$percent" >&2
    exit 1
    ;;
esac

stable_w=$((100 - percent))
az containerapp ingress traffic set \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --label-weight "stable=$stable_w" "canary=$percent" \
  --output json >/dev/null

got=$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --output json | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .weight] | first // 0')
[ "$got" = "$percent" ] || {
  printf 'shift-azure-container-apps: ingress reports canary weight %s, want %s\n' "$got" "$percent" >&2
  exit 1
}
