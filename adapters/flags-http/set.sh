#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# set-flags-http: updates <flag> to <variant>. Fail-closed when read-only:
# requires FLAGS_SET_URL (HTTP control endpoint) or explicit
# FLAGS_HTTP_WRITE_FILE=1 (delegating local writes to $FLAGS_FILE).
set -eu
flag=${1:-}
variant=${2:-}
[ -n "$flag" ] && [ -n "$variant" ] || { printf 'set-flags-http: usage: set.sh <flag> <variant>\n' >&2; exit 1; }
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/flags-http/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [ -n "$FLAGS_SET_URL" ]; then
  payload=$(jq -cn --arg f "$flag" --arg v "$variant" '{flag: $f, variant: $v, defaultVariant: $v}')
  flags_http_post "$FLAGS_SET_URL" "$payload" "$tmp/set.json" \
    || { printf 'set-flags-http: update failed for %s=%s at %s\n' "$flag" "$variant" "$FLAGS_SET_URL" >&2; exit 1; }
  sh "$here/evaluate.sh" "$flag" >/dev/null \
    || { printf 'set-flags-http: %s failed post-set evaluation\n' "$flag" >&2; exit 1; }
  exit 0
fi

if [ "${FLAGS_HTTP_WRITE_FILE:-0}" = "1" ] && [ -f "$here/../flags-file/set.sh" ]; then
  sh "$here/../flags-file/set.sh" "$flag" "$variant"
  sh "$here/evaluate.sh" "$flag" >/dev/null \
    || { printf 'set-flags-http: %s failed post-set evaluation\n' "$flag" >&2; exit 1; }
  exit 0
fi

printf 'set-flags-http: OFREP HTTP endpoint is read-only unless FLAGS_SET_URL is set (or FLAGS_HTTP_WRITE_FILE=1 for file-backed server)\n' >&2
exit 1
