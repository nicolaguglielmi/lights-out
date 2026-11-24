#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The Go mutation runner (docs/adr/0042 §1, ADR 0102): zero-dependency
# go/ast line-scoped mutant generator and runner.
#
#   sh .lights-out/runners/mutation.sh <scope> "<test command>"
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
command -v go >/dev/null 2>&1 || { printf 'mutation.sh: go is not installed on PATH\n' >&2; exit 1; }

prefix=${LIGHTS_OUT_RUNNERS_PREFIX:-/tmp/lights-out-go-runners}
mkdir -p "$prefix"
bin="$prefix/mutate"
if [ ! -x "$bin" ] || [ "$here/mutate.go" -nt "$bin" ]; then
  tmpbin=$(mktemp "$prefix/mutate.XXXXXX")
  trap 'rm -f "$tmpbin"' EXIT
  (cd "$here" && GO111MODULE=off GOWORK=off go build -o "$tmpbin" mutate.go) >&2
  mv "$tmpbin" "$bin"
  trap - EXIT
fi

exec "$bin" "$src" "$test_cmd"
