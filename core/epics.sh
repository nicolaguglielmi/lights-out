#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-epics: multi-ticket epics, depends_on DAG, and multi-service monorepo
# workspace coordinator (docs/adr/0119). Thin wrapper around core/epics.py;
# kept <= 25 code lines for ci/shell-boundary.sh.
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

exec python3 -B core/epics.py "$@"
