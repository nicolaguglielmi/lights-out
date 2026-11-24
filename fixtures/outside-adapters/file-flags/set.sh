#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
flag=${1:-}
variant=${2:-}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-flags/env.sh
. "$here/env.sh"

[ -n "$flag" ] && [ -n "$variant" ] || { printf 'file-flags: usage: set.sh <flag> <variant>\n' >&2; exit 1; }
require_jq
[ -f "$FILE_FLAGS_PATH" ] || { printf 'file-flags: %s not initialized\n' "$FILE_FLAGS_PATH" >&2; exit 1; }

jq -e --arg f "$flag" --arg v "$variant" \
  '(.flags | type == "object") and (.flags | has($f)) and (.flags[$f].variants | has($v))' \
  "$FILE_FLAGS_PATH" >/dev/null 2>&1 \
  || { printf 'file-flags: missing flag %s or variant %s\n' "$flag" "$variant" >&2; exit 1; }

updated=$(jq -c --arg f "$flag" --arg v "$variant" \
  'del(.flags[$f].targeting) | .flags[$f].defaultVariant = $v' "$FILE_FLAGS_PATH")
write_flags "$updated"
