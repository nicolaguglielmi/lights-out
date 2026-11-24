#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# query-range-grafana: Charts port verb querying /api/v1/query_range and
# emitting {"status":"ok","series":[{"name":...,"points":[[ts,val],...]}]}
# on stdout. Exits non-zero with empty stdout on error.
set -eu

if [ "$#" -ne 4 ]; then
  printf 'usage: query-range.sh <query> <start> <end> <step_s>\n' >&2
  exit 1
fi

query=$1
start_ts=$2
end_ts=$3
step_s=$4

if [ -z "$(printf '%s' "$query" | tr -d '[:space:]')" ]; then
  printf 'query-range-grafana: query must not be empty\n' >&2
  exit 1
fi
if [ -z "$start_ts" ] || [ -z "$end_ts" ]; then
  printf 'query-range-grafana: start and end must both be non-empty\n' >&2
  exit 1
fi
if ! awk -v s="$step_s" 'BEGIN { exit !(s ~ /^[0-9]+(\.[0-9]+)?$/ && (s + 0) > 0) }'; then
  printf 'query-range-grafana: step_s must be a positive number (got %s)\n' "$step_s" >&2
  exit 1
fi

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/grafana/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if ! grafana_curl_get -o "$tmp/resp.json" -G "$GRAFANA_BASE_URL/api/v1/query_range" \
    --data-urlencode "query=$query" \
    --data-urlencode "start=$start_ts" \
    --data-urlencode "end=$end_ts" \
    --data-urlencode "step=$step_s" 2>"$tmp/err"; then
  cat "$tmp/err" >&2
  exit 1
fi

if ! jq -c --arg q "$query" '
  def series_name($fallback):
    (.metric // {}) as $m
    | ($m.__name__ // "") as $base
    | ($m | del(.__name__) | to_entries | sort_by(.key) | map("\(.key)=\"\(.value)\"") | join(",")) as $lbls
    | if ($base != "" and $lbls != "") then "\($base){\($lbls)}"
      elif ($base != "") then $base
      elif ($lbls != "") then "{\($lbls)}"
      else $fallback
      end;
  if .status == "ok" and (.series | type) == "array" then
    {
      status: "ok",
      series: [.series[] | {name: (.name | tostring), points: [.points[] | [(.[0] | tonumber), (.[1] | tonumber)]]}]
    }
  elif .status == "success" and (.data.result | type) == "array" then
    {
      status: "ok",
      series: [
        .data.result[]
        | {
            name: series_name($q),
            points: [
              (.values // [])[]
              | select((type == "array") and (length >= 2))
              | [(.[0] | tonumber), (.[1] | tonumber)]
            ]
          }
      ]
    }
  else
    error("unexpected query_range payload")
  end
' "$tmp/resp.json" > "$tmp/out.json" 2>"$tmp/jq.err"; then
  cat "$tmp/jq.err" >&2
  exit 1
fi

cat "$tmp/out.json"
