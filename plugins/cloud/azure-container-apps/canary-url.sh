#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# canary-url.sh (azure-container-apps): prints the revision-labeled canary URL.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli az jq

url=${CANARY_URL:-}
if [ -z "$url" ]; then
  fqdn=$(az containerapp show \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$AZURE_RESOURCE_GROUP" \
    --name "$AZURE_CONTAINER_APP" \
    --output json | jq -r '.properties.configuration.ingress.fqdn // empty')
  [ -n "$fqdn" ] || {
    printf 'canary-url-azure-container-apps: ingress fqdn is empty for %s\n' "$AZURE_CONTAINER_APP" >&2
    exit 1
  }
  case "$fqdn" in
    *.*) url="https://${fqdn%%.*}---canary.${fqdn#*.}" ;;
    *) url="http://$fqdn" ;;
  esac
fi
printf '%s\n' "$url"
