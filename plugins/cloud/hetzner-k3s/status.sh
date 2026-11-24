#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# status.sh (hetzner-k3s): emits exact 3-key JSON {stable, canary, weight}.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq

r=$(rollout_json) || {
  printf 'status-hetzner-k3s: no rollout %s/%s\n' "$HETZNER_NAMESPACE" "$HETZNER_ROLLOUT" >&2
  exit 1
}
stable_rs=$(printf '%s' "$r" | jq -r '.status.stableRS // empty')
canary_rs=$(printf '%s' "$r" | jq -r '.status.currentPodHash // empty')
aborted=$(printf '%s' "$r" | jq -r '.status.abort // false')

stable=$(version_of "$stable_rs") || {
  printf 'status-hetzner-k3s: stable revision %s did not report a version\n' "${stable_rs:-(none)}" >&2
  exit 1
}

if [ -n "$canary_rs" ] && [ "$canary_rs" != "$stable_rs" ] && [ "$aborted" = "false" ]; then
  canary=$(version_of "$canary_rs") || {
    printf 'status-hetzner-k3s: canary revision %s did not report a version\n' "$canary_rs" >&2
    exit 1
  }
  canary_json=$(printf '%s' "$canary" | jq -R .)
else
  canary_json=null
fi

jq -cn --arg s "$stable" --argjson c "$canary_json" --argjson w "$(weight_of "$r")" \
  '{stable: $s, canary: $c, weight: $w}'
