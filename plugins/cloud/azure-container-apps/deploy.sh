#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# deploy.sh (azure-container-apps): enables Multiple revision mode, deploys
# <image> with APP_VERSION=<version>, labels it canary, and pins traffic 100/0.
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli az jq

az containerapp revision set-mode \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --mode multiple --output json >/dev/null

rev_suffix="c-$(printf '%s' "$version" | tr -cd 'a-zA-Z0-9-' | tr '[:upper:]' '[:lower:]' | cut -c1-18)"
[ "$rev_suffix" != "c-" ] || rev_suffix="c-$(date -u +%s)"

upd=$(az containerapp update \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --image "$image" \
  --set-env-vars "APP_VERSION=$version" \
  --revision-suffix "$rev_suffix" \
  --output json)
new_rev=$(printf '%s' "$upd" | jq -er '.properties.latestRevisionName')

az containerapp revision label add \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --label canary \
  --revision "$new_rev" --output json >/dev/null

az containerapp ingress traffic set \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --label-weight stable=100 canary=0 \
  --output json >/dev/null

start=$(date +%s)
while :; do
  rstate=$(az containerapp revision show \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_CONTAINER_APP" \
    --revision "$new_rev" --output json 2>/dev/null \
    | jq -r '.properties.runningState // "Unknown"') || rstate="Unknown"
  [ "$rstate" = "Running" ] && exit 0
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$AZURE_POLL_INTERVAL"
done
printf 'deploy-azure-container-apps: revision %s runningState=%s after %ss\n' \
  "$new_rev" "$rstate" "$READY_TIMEOUT" >&2
exit 1
