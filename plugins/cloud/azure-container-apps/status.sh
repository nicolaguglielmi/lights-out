#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# status.sh (azure-container-apps): emits exact 3-key JSON {stable, canary, weight}.
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
revs_json=$(az containerapp revision list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$AZURE_CONTAINER_APP" \
  --output json)

stable_rev=$(printf '%s' "$app_json" | jq -r '([.properties.configuration.ingress.traffic[]? | select((.label // "") == "stable" or (.label // "") != "canary") | .revisionName] | first) // empty')
canary_rev=$(printf '%s' "$app_json" | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .revisionName] | first // empty')
weight=$(printf '%s' "$app_json" | jq -r '[.properties.configuration.ingress.traffic[]? | select(.label == "canary") | .weight] | first // 0')

ver_of() {
  printf '%s' "$revs_json" | jq -er --arg r "$1" '
    (.[] | select(.name == $r) | .properties.template.containers[0]) as $c
    | (([($c.env // [])[] | select(.name == "APP_VERSION") | .value] | first)
       // ($c.image | split("@")[1] // (split(":") | last)))
  '
}

[ -n "$stable_rev" ] || {
  printf 'status-azure-container-apps: no stable revision found\n' >&2
  exit 1
}
stable_ver=$(ver_of "$stable_rev") || {
  printf 'status-azure-container-apps: could not resolve version for %s\n' "$stable_rev" >&2
  exit 1
}

if [ -n "$canary_rev" ]; then
  canary_ver=$(ver_of "$canary_rev") || {
    printf 'status-azure-container-apps: could not resolve version for canary %s\n' "$canary_rev" >&2
    exit 1
  }
  canary_json=$(printf '%s' "$canary_ver" | jq -R .)
else
  canary_json=null
fi

jq -cn --arg s "$stable_ver" --argjson c "$canary_json" --argjson w "$weight" \
  '{stable: $s, canary: $c, weight: $w}'
