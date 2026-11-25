#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# set-unleash: toggles <flag> to <variant> (on/off or true/false) via Unleash
# Admin API with stdin -H @- auth, then verifies via evaluate.sh.
set -eu
flag=${1:-}
variant=${2:-}
[ -n "$flag" ] && [ -n "$variant" ] || { printf 'set-unleash: usage: set.sh <flag> <variant>\n' >&2; exit 1; }
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/unleash/env.sh
. "$here/env.sh"

case "$variant" in
  on|true)
    action=on
    expect=true ;;
  off|false)
    action=off
    expect=false ;;
  *)
    printf 'set-unleash: unsupported variant %s for %s (want on|off)\n' "$variant" "$flag" >&2
    exit 1 ;;
esac

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

enc_proj=$(unleash_encode "$UNLEASH_PROJECT")
enc_flag=$(unleash_encode "$flag")
enc_env=$(unleash_encode "$UNLEASH_ENVIRONMENT")
url="$UNLEASH_URL/api/admin/projects/$enc_proj/features/$enc_flag/environments/$enc_env/$action"

code=$(unleash_curl POST "$url" "$tmp/resp.json")
case "$code" in
  200|201|202|204) ;;
  *)
    printf 'set-unleash: POST %s failed for %s=%s (HTTP %s)\n' "$url" "$flag" "$variant" "$code" >&2
    exit 1 ;;
esac

got=$(sh "$here/evaluate.sh" "$flag" 2>/dev/null) || got=""
if [ "$got" != "$expect" ]; then
  printf 'set-unleash: %s evaluated %s after %s, want %s\n' "$flag" "${got:-error}" "$action" "$expect" >&2
  exit 1
fi
