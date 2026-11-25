#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-portfolio: read-only multi-repo fleet federation hook (docs/adr/0138).
# Kept <= 25 non-blank, non-comment code lines (ci/shell-boundary.sh).
set -eu
# shellcheck disable=SC2034 # name is consumed by sourced ci/lib.sh
name=portfolio
# shellcheck disable=SC1091 # ci/lib.sh is resolved at runtime from repo root
. ci/lib.sh

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
if [ -n "${LIGHTS_OUT_CLI_ARGS:-}" ]; then
  old_ifs=$IFS
  IFS=$(printf '\037')
  set -f
  # shellcheck disable=SC2086 # deliberate splitting on ASCII unit separator \037
  set -- $LIGHTS_OUT_CLI_ARGS "$@"
  set +f
  IFS=$old_ifs
fi
for a in "$@"; do
  if [ "$a" = "--json" ]; then
    exec python3 -B "$here/portfolio.py" "$@"
  fi
done
if [ "${JSON:-0}" = "1" ]; then
  exec python3 -B "$here/portfolio.py" --json "$@"
fi
out=$(python3 -B "$here/portfolio.py" --contract "$@") || nogo "${out:-core/portfolio.py failed; see stderr}"
go_with "$out"
