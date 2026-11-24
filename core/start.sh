#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-start: thin POSIX wrapper around core/start.py (`start` and `next`,
# ADR 0103). Kept <= 30 code lines for ci/shell-boundary.sh.
set -eu

cmd=${1:-next}
[ $# -gt 0 ] && shift

if [ -n "${LIGHTS_OUT_CLI_ARGS:-}" ]; then
  # Unpack ASCII-unit-separated CLI flags forwarded from ./go without hitting make flag parsing.
  old_ifs=$IFS
  IFS=$(printf '\037')
  set -f
  # shellcheck disable=SC2086 # deliberate splitting on unit separator \037
  set -- $LIGHTS_OUT_CLI_ARGS "$@"
  set +f
  IFS=$old_ifs
fi

exec python3 core/start.py "$cmd" "$@"
