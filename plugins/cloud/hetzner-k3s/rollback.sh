#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# rollback.sh (hetzner-k3s): idempotently aborts any active Rollout canary and
# waits until canary weight returns to 0.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli kubectl jq

r=$(rollout_json)
in_flight=$(printf '%s' "$r" | jq -r '.status.currentPodHash != .status.stableRS')
aborted=$(printf '%s' "$r" | jq -r '.status.abort // false')
if [ "$in_flight" = "true" ] && [ "$aborted" = "false" ]; then
  status_patch '{"status":{"abort":true}}'
fi

start=$(date +%s)
while :; do
  r=$(rollout_json 2>/dev/null) || r='{}'
  [ "$(weight_of "$r")" = "0" ] && exit 0
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$HETZNER_POLL_INTERVAL"
done
printf 'rollback-hetzner-k3s: canary weight still %s after %ss\n' "$(weight_of "$r")" "$READY_TIMEOUT" >&2
exit 1
