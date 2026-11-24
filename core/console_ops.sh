#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
# Control-plane workstation operations entrypoint (docs/adr/0125 §1).
set -eu
# shellcheck disable=SC1007
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 -B "$here/console_ops.py" "$@"
