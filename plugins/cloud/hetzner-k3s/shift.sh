#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# shift.sh (hetzner-k3s): advances the Argo Rollout to setWeight 5, 25, or 100.
set -eu
percent=${1:?usage: shift.sh <canary-percent>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq

case "$percent" in
  5|25|100) ;;
  *)
    printf 'shift-hetzner-k3s: invalid canary percent %s (allowed: 5, 25, 100)\n' "$percent" >&2
    exit 1
    ;;
esac

r=$(rollout_json)
target=$(printf '%s' "$r" | jq -r --argjson p "$percent" '
  [.spec.strategy.canary.steps // [] | to_entries[] | select(.value.setWeight == $p) | .key] | first // empty')
[ -n "$target" ] || {
  printf 'shift-hetzner-k3s: rollout %s/%s has no setWeight: %s step\n' "$HETZNER_NAMESPACE" "$HETZNER_ROLLOUT" "$percent" >&2
  exit 1
}
want=$((target + 1))

promoted=-1
start=$(date +%s)
got=""
at=""
while :; do
  r=$(rollout_json 2>/dev/null) || r='{}'
  at=$(printf '%s' "$r" | jq -r '.status.currentStepIndex // 0')
  paused=$(printf '%s' "$r" | jq -r '(.status.pauseConditions // []) | length')
  aborted=$(printf '%s' "$r" | jq -r '.status.abort // false')
  [ "$aborted" = "false" ] || {
    printf 'shift-hetzner-k3s: rollout %s/%s is aborted; deploy first\n' "$HETZNER_NAMESPACE" "$HETZNER_ROLLOUT" >&2
    exit 1
  }
  if [ "$at" -eq "$want" ] && [ "$paused" -gt 0 ]; then
    got=$(weight_of "$r")
    [ "$got" = "$percent" ] && exit 0
  elif [ "$at" -lt "$want" ] && [ "$paused" -gt 0 ] && [ "$at" -ne "$promoted" ]; then
    status_patch '{"status":{"pauseConditions":null}}'
    promoted=$at
  elif [ "$at" -gt "$want" ]; then
    printf 'shift-hetzner-k3s: rollout %s/%s is already past step %s\n' "$HETZNER_NAMESPACE" "$HETZNER_ROLLOUT" "$target" >&2
    exit 1
  fi
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$HETZNER_POLL_INTERVAL"
done
printf 'shift-hetzner-k3s: weight %s at step %s after %ss, want %s at step %s\n' \
  "${got:-none}" "${at:-?}" "$READY_TIMEOUT" "$percent" "$want" >&2
exit 1
