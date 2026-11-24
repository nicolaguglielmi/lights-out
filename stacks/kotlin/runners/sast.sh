#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The Kotlin SAST runner (ADR 0072 §1, ADR 0129): zero-dependency Python 3
# stdlib static analyzer over one Kotlin source directory.
#
#   sh runners/sast.sh <scope>
#
# Output contract: exactly one JSON line on stdout,
# {"findings": N, "items": [{"rule", "file", "line"}]}, with `file`
# relative to the directory the command runs from; diagnostics on stderr.
set -eu

scope=${1:?usage: sast.sh <scope>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
[ -d "$scope" ] || { printf 'sast.sh: %s is not a directory here (%s)\n' "$scope" "$(pwd)" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf 'sast.sh: python3 is not installed on PATH\n' >&2; exit 1; }

exec python3 "$here/sast.py" "$scope"
