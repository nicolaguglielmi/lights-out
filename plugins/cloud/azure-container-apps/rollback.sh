#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# rollback.sh (azure-container-apps): idempotently sets stable=100, canary=0
# and deactivates any active canary revision.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli az jq

app_json=$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --output json)

canary_rev=$(printf '%s' "$app_json" | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .revisionName] | first // empty')
canary_w=$(printf '%s' "$app_json" | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .weight] | first // 0')

if [ "$canary_w" != "0" ]; then
  az containerapp ingress traffic set \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_CONTAINER_APP" \
    --label-weight stable=100 canary=0 \
    --output json >/dev/null
fi

if [ -n "$canary_rev" ]; then
  az containerapp revision deactivate \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_CONTAINER_APP" \
    --revision "$canary_rev" \
    --output json >/dev/null
fi

got=$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --output json | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .weight] | first // 0')
[ "$got" = "0" ] || {
  printf 'rollback-azure-container-apps: canary weight still %s after rollback\n' "$got" >&2
  exit 1
}
