#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# canary-url.sh (hetzner-k3s): prints the canary ingress URL on stdout.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq

url=${CANARY_URL:-}
if [ -z "$url" ]; then
  ing=$(k get ingress "$HETZNER_CANARY_INGRESS" -o json)
  host=$(printf '%s' "$ing" | jq -r '.spec.rules[0].host // empty')
  ip=$(printf '%s' "$ing" | jq -r '.status.loadBalancer.ingress[0].ip // empty')
  if [ -n "$host" ]; then
    url="https://$host"
  elif [ -n "$ip" ]; then
    url="http://$ip"
  fi
fi
[ -n "$url" ] || {
  printf 'canary-url-hetzner-k3s: no host or LB IP found on ingress %s\n' "$HETZNER_CANARY_INGRESS" >&2
  exit 1
}
printf '%s\n' "$url"
