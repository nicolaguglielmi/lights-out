#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-cli: thin POSIX launcher around core/cli.py, the unified noun-verb CLI
# (docs/adr/0112). The grammar, flags, JSON envelopes and completions are
# Python; kept <= 30 code lines for ci/shell-boundary.sh.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)

if [ -n "${LIGHTS_OUT_CLI_ARGS:-}" ]; then
  # Unpack ASCII-unit-separated CLI args forwarded from ./go without hitting make flag parsing.
  old_ifs=$IFS
  IFS=$(printf '\037')
  set -f
  # shellcheck disable=SC2086 # deliberate splitting on unit separator \037
  set -- $LIGHTS_OUT_CLI_ARGS "$@"
  set +f
  IFS=$old_ifs
fi

exec python3 "$here/cli.py" "$@"
