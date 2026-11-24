#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-init: `make init` (docs/adr/0042 §6, 0113), the thin POSIX driver behind
# `./go init [<path>]`. Unpacks LIGHTS_OUT_CLI_ARGS and delegates draft/apply,
# baseline measurement, contract validation, and scoped commit to core/init.py
# under ci/lib.sh's `verdict` contract (<= 30 code lines for ci/shell-boundary.sh).
set -eu
name=init
. ci/lib.sh

if [ -n "${LIGHTS_OUT_CLI_ARGS:-}" ]; then
  # Unpack ASCII-unit-separated CLI flags forwarded from ./go or entrypoint without hitting make flag parsing.
  old_ifs=$IFS
  IFS=$(printf '\037')
  set -f
  # shellcheck disable=SC2086 # deliberate splitting on unit separator \037
  set -- $LIGHTS_OUT_CLI_ARGS "$@"
  set +f
  IFS=$old_ifs
fi

verdict core.init --run "$@"
