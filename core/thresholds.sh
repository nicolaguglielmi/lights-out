#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-thresholds: threshold drift and override governance (docs/adr/0135).
# Kept <= 25 non-blank, non-comment code lines (ci/shell-boundary.sh).
set -eu
name=thresholds
. ci/lib.sh

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
for a in "$@"; do
  if [ "$a" = "--json" ]; then
    exec python3 -B "$here/thresholds.py" "$@"
  fi
done
if [ "${JSON:-0}" = "1" ]; then
  exec python3 -B "$here/thresholds.py" --json "$@"
fi
out=$(python3 -B "$here/thresholds.py" --contract "$@") || nogo "${out:-core/thresholds.py failed; see stderr}"
go_with "$out"
