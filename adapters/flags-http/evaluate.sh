#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# evaluate-flags-http: evaluates <flag> [version] against OFREP HTTP endpoint
# $FLAGS_URL/ofrep/v1/evaluate/flags/<flag>. Fail-closed on HTTP/JSON/value errors.
set -eu
flag=${1:-}
version=${2:-}
[ -n "$flag" ] || { printf 'evaluate-flags-http: usage: evaluate.sh <flag> [version]\n' >&2; exit 1; }
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/flags-http/env.sh
. "$here/env.sh"

if [ -n "$version" ]; then
  ctx=$(jq -cn --arg v "$version" '{context: {version: $v}}')
else
  ctx='{"context":{}}'
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

enc_flag=$(flags_http_encode "$flag")
flags_http_post "$FLAGS_URL/ofrep/v1/evaluate/flags/$enc_flag" "$ctx" "$tmp/resp.json" \
  || { printf 'evaluate-flags-http: request failed for %s at %s\n' "$flag" "$FLAGS_URL" >&2; exit 1; }

val=$(jq -r 'if (type == "object") and has("value") and (.value != null) then (.value | if type == "string" then . else tojson end) else empty end' "$tmp/resp.json" 2>/dev/null) || val=""
[ -n "$val" ] || { printf 'evaluate-flags-http: missing value for %s from %s\n' "$flag" "$FLAGS_URL" >&2; exit 1; }

printf '%s\n' "$val"
