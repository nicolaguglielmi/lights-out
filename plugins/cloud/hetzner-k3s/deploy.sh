#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# deploy.sh (hetzner-k3s): updates the Rollout container image and
# lights-out.dev/version annotation, clears abort if set, and waits at weight 0.
set -eu
version=${1:?usage: deploy.sh <version> <image>}
image=${2:?usage: deploy.sh <version> <image>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq

case "$image" in
  *.*/*|localhost:*/*) full_image=$image ;;
  *) full_image="$HETZNER_REGISTRY_HOST/$image" ;;
esac

annotations=$(rollout_json | jq -c --arg v "$version" '(.spec.template.metadata.annotations // {}) + {"lights-out.dev/version": $v}')
patch=$(jq -cn --arg i "$full_image" --argjson a "$annotations" '[
  {op: "replace", path: "/spec/template/spec/containers/0/image", value: $i},
  {op: "add", path: "/spec/template/metadata/annotations", value: $a}
]')
k patch rollout "$HETZNER_ROLLOUT" --type json -p "$patch" >/dev/null

if [ "$(rollout_json | jq -r '.status.abort // false')" = "true" ]; then
  status_patch '{"status":{"abort":false}}'
fi

start=$(date +%s)
got=""
r='{}'
while :; do
  r=$(rollout_json 2>/dev/null) || r='{}'
  stable=$(printf '%s' "$r" | jq -r '.status.stableRS // empty')
  canary=$(printf '%s' "$r" | jq -r '.status.currentPodHash // empty')
  paused=$(printf '%s' "$r" | jq -r '(.status.pauseConditions // []) | length')
  if [ -n "$canary" ] && [ "$canary" != "$stable" ] && [ "$paused" -gt 0 ]; then
    got=$(version_of "$canary") || got=""
    [ "$got" = "$version" ] && exit 0
  fi
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$HETZNER_POLL_INTERVAL"
done
printf 'deploy-hetzner-k3s: canary reports %s after %ss, want %s\n' \
  "${got:-no answer}" "$READY_TIMEOUT" "$version" >&2
exit 1
