#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-demo-loop: closed end-to-end L0 self-healing loop (docs/adr/0132).
# Thin wrapper around core/demo_loop.py; kept <= 20 code lines for
# ci/shell-boundary.sh.
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

exec python3 -B core/demo_loop.py "$@"
