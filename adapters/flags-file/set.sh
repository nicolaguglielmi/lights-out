#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# set-flags-file: sets <flag>'s defaultVariant to <variant> and clears any
# targeting rule directly in $FLAGS_FILE, then verifies via evaluate.sh.
# Fail-closed on missing file, unknown flag, or unknown variant.
set -eu
flag=${1:-}
variant=${2:-}
[ -n "$flag" ] && [ -n "$variant" ] || { printf 'set-flags-file: usage: set.sh <flag> <variant>\n' >&2; exit 1; }
: "${FLAGS_FILE:?FLAGS_FILE not set}"
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

[ -f "$FLAGS_FILE" ] || { printf 'set-flags-file: %s not found\n' "$FLAGS_FILE" >&2; exit 1; }

jq -e --arg f "$flag" --arg v "$variant" \
  '(.flags | type == "object") and (.flags | has($f)) and (.flags[$f].variants | has($v))' \
  "$FLAGS_FILE" >/dev/null 2>&1 \
  || { printf 'set-flags-file: missing flag %s or variant %s in %s\n' "$flag" "$variant" "$FLAGS_FILE" >&2; exit 1; }

content=$(jq --arg f "$flag" --arg v "$variant" \
  'del(.flags[$f].targeting) | .flags[$f].defaultVariant = $v' "$FLAGS_FILE")
expect=$(printf '%s' "$content" | jq -r --arg f "$flag" --arg v "$variant" '
  .flags[$f].variants[$v] | if type == "string" then . else tojson end')

printf '%s\n' "$content" > "$FLAGS_FILE"
got=$(sh "$here/evaluate.sh" "$flag" 2>/dev/null) || got=""
[ "$got" = "$expect" ] || {
  printf 'set-flags-file: %s evaluated %s after write, expected %s\n' "$flag" "${got:-nothing}" "$expect" >&2
  exit 1
}
