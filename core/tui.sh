#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-tui: thin POSIX wrapper around core/tui.py (`top` / `tui` terminal
# cockpit, ADR 0114). Kept <= 30 code lines for ci/shell-boundary.sh.
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

exec python3 core/tui.py "$@"
