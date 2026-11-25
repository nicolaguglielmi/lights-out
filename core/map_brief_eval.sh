#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-map-brief-eval: measure MAP_BRIEF=off vs MAP_BRIEF=on brief footprint
# via core/map.py (docs/adr/0054 §5). Thin wrapper around core/map_brief_eval.py;
# kept <= 20 code lines for ci/shell-boundary.sh.
set -eu

if [ -n "${LIGHTS_OUT_CLI_ARGS:-}" ]; then
  old_ifs=$IFS
  IFS=$(printf '\037')
  set -f
  # shellcheck disable=SC2086 # deliberate splitting on ASCII unit separator \037
  set -- $LIGHTS_OUT_CLI_ARGS "$@"
  set +f
  IFS=$old_ifs
fi

exec python3 -B core/map_brief_eval.py "$@"
