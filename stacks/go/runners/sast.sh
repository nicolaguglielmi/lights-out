#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# The Go SAST runner (docs/adr/0042 §1, ADR 0102): zero-dependency go/ast
# and go vet scanner over one scope directory.
#
#   sh .lights-out/runners/sast.sh <scope>
#
# Output contract (ADR 0072 §1): exactly one JSON line on stdout,
# {"findings": N, "items": [{"rule", "file", "line"}]}, with `file`
# relative to the directory the command runs from; diagnostics on stderr;
# non-zero exit only when the tool itself failed.
set -eu

scope=${1:?usage: sast.sh <scope>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
[ -d "$scope" ] || { printf 'sast.sh: %s is not a directory here (%s)\n' "$scope" "$(pwd)" >&2; exit 1; }
command -v go >/dev/null 2>&1 || { printf 'sast.sh: go is not installed on PATH\n' >&2; exit 1; }

prefix=${LIGHTS_OUT_RUNNERS_PREFIX:-/tmp/lights-out-go-runners}
mkdir -p "$prefix"
bin="$prefix/sast"
if [ ! -x "$bin" ] || [ "$here/sast.go" -nt "$bin" ]; then
  tmpbin=$(mktemp "$prefix/sast.XXXXXX")
  trap 'rm -f "$tmpbin"' EXIT
  (cd "$here" && GO111MODULE=off GOWORK=off go build -o "$tmpbin" sast.go) >&2
  mv "$tmpbin" "$bin"
  trap - EXIT
fi

exec "$bin" "$scope"
