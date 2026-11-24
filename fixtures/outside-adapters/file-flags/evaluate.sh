#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
flag=${1:-}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-flags/env.sh
. "$here/env.sh"

[ -n "$flag" ] || { printf 'file-flags: usage: evaluate.sh <flag> [version]\n' >&2; exit 1; }
require_jq
[ -f "$FILE_FLAGS_PATH" ] || { printf 'file-flags: %s not initialized\n' "$FILE_FLAGS_PATH" >&2; exit 1; }

val=$(jq -r --arg f "$flag" '
  if (.flags | type == "object") and (.flags | has($f)) and ((.flags[$f].state // "ENABLED") == "ENABLED") then
    .flags[$f] as $s
    | if ($s.variants | type == "object") and ($s.defaultVariant | type == "string") and ($s.variants | has($s.defaultVariant)) then
        $s.variants[$s.defaultVariant] | if type == "string" then . else tojson end
      else empty end
  else empty end
' "$FILE_FLAGS_PATH" 2>/dev/null) || val=""

[ -n "$val" ] || { printf 'file-flags: flag %s unavailable\n' "$flag" >&2; exit 1; }
printf '%s\n' "$val"
