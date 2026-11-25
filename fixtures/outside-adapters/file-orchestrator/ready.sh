#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# outside/file-orchestrator ready.sh: verifies EVENT_BUS, STATE_STORE, and
# core/tower.sh readiness without writing stdout (ADR 0058, ADR 0133).
set -eu

for tool in jq python3; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'file-orchestrator: %s is not on PATH\n' "$tool" >&2
    exit 1
  }
done

: "${EVENT_BUS:?EVENT_BUS not set}"
: "${STATE_STORE:?STATE_STORE not set}"

for v in publish pull ack; do
  [ -f "adapters/$EVENT_BUS/$v.sh" ] || {
    printf 'file-orchestrator: adapters/%s/%s.sh not found\n' "$EVENT_BUS" "$v" >&2
    exit 1
  }
done
for v in get put; do
  [ -f "adapters/$STATE_STORE/$v.sh" ] || {
    printf 'file-orchestrator: adapters/%s/%s.sh not found\n' "$STATE_STORE" "$v" >&2
    exit 1
  }
done
[ -f "core/tower.sh" ] || {
  printf 'file-orchestrator: core/tower.sh not found\n' >&2
  exit 1
}

if [ -f "adapters/$EVENT_BUS/ready.sh" ]; then
  sh "adapters/$EVENT_BUS/ready.sh" >/dev/null || exit 1
fi
if [ -f "adapters/$STATE_STORE/ready.sh" ]; then
  sh "adapters/$STATE_STORE/ready.sh" >/dev/null || exit 1
fi
