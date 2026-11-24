#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# core-cli: thin POSIX launcher around core/cli.py, the unified noun-verb CLI
# (docs/adr/0112). The grammar, flags, JSON envelopes and completions are
# Python; kept <= 30 code lines for ci/shell-boundary.sh.
set -eu

here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
exec python3 "$here/cli.py" "$@"
