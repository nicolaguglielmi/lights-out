#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-jury: cross-provider jury reviewer selection and inspection
# (docs/adr/0130). Thin wrapper around core/jury.py; kept <= 20 code
# lines for ci/shell-boundary.sh.
set -eu

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

exec python3 -B "$here/jury.py" "$@"
