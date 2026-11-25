#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-orchestrator run.sh: cold local Orchestrator fixture draining
# EVENT_BUS and invoking core/tower.sh step <ticket> <step> (ADR 0058, ADR 0133).
set -eu

if [ "$#" -eq 2 ]; then
  exec sh core/tower.sh step "$1" "$2" >&2
elif [ "$#" -ne 0 ]; then
  printf 'file-orchestrator: usage: run.sh [<ticket> <step>]\n' >&2
  exit 1
fi

: "${EVENT_BUS:?EVENT_BUS not set; run this via ./go, not directly}"
[ -f "adapters/$EVENT_BUS/pull.sh" ] && [ -f "adapters/$EVENT_BUS/ack.sh" ] || {
  printf 'file-orchestrator: adapters/%s/{pull,ack}.sh not found\n' "$EVENT_BUS" >&2
  exit 1
}

failed=0
applied=0
while :; do
  msg=$(sh "adapters/$EVENT_BUS/pull.sh") || exit 1
  [ -n "$msg" ] || break
  ackid=${msg%% *}
  data=${msg#* }
  ticket=$(printf '%s' "$data" | jq -r '.ticket // empty' 2>/dev/null) || ticket=""
  step=$(printf '%s' "$data" | jq -r '.step // empty' 2>/dev/null) || step=""
  if [ -z "$ticket" ] || [ -z "$step" ]; then
    printf '[file-orchestrator] dropping message without {ticket, step}: %s\n' "$data" >&2
  elif ! sh core/tower.sh step "$ticket" "$step"; then
    failed=$((failed + 1))
  fi
  applied=$((applied + 1))
  sh "adapters/$EVENT_BUS/ack.sh" "$ackid" || exit 1
done

printf '[file-orchestrator] queue empty after %s event(s), %s failed\n' "$applied" "$failed" >&2
[ "$failed" -eq 0 ]
