#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-reviewer-eval: labeled-diff reviewer & gate catch-rate evaluation
# suite (docs/adr/0137). Thin wrapper around core/reviewer_eval.py;
# kept <= 25 non-blank, non-comment code lines for ci/shell-boundary.sh.
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

exec python3 -B "$here/reviewer_eval.py" "$@"
