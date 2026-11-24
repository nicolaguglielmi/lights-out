#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The C#/.NET mutation runner (ADR 0015 §3, ADR 0129): zero-dependency
# Python 3 stdlib line-scoped mutant generator and runner.
#
#   sh runners/mutation.sh <scope> "<test command>"
#
# MUTATION_LINES in the environment scopes the run ("<file>=<start>-<end>,..."
# space-separated, or "all" / unset for the whole scope).
# Output contract: one JSON line on stdout {"total","killed","survived","score"},
# survivors on stderr as "SURVIVOR file:line mutator".
set -eu

scope=${1:?usage: mutation.sh <scope> "<test command>"}
test_cmd=${2:?usage: mutation.sh <scope> "<test command>"}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
src=${scope%/}
[ -n "$src" ] || src="."
[ -d "$src" ] || { printf 'mutation.sh: %s is not a directory here (%s)\n' "$src" "$(pwd)" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { printf 'mutation.sh: python3 is not installed on PATH\n' >&2; exit 1; }

exec python3 "$here/mutate.py" "$src" "$test_cmd"
