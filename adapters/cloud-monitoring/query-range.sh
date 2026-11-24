#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# query-range-cloud-monitoring: Charts port verb querying MONITORING_API_URL
# (projects.timeSeries.query) with bearer token on stdin (-H @-), emitting
# {"status":"ok","series":[{"name":...,"points":[[ts,val],...]}]} on stdout.
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
  printf 'query-range-cloud-monitoring: query must not be empty\n' >&2
  exit 1
fi
if [ -z "$start_ts" ] || [ -z "$end_ts" ]; then
  printf 'query-range-cloud-monitoring: start and end must both be non-empty\n' >&2
  exit 1
fi
if ! awk -v s="$step_s" 'BEGIN { exit !(s ~ /^[0-9]+(\.[0-9]+)?$/ && (s + 0) > 0) }'; then
  printf 'query-range-cloud-monitoring: step_s must be a positive number (got %s)\n' "$step_s" >&2
  exit 1
fi

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/cloud-monitoring/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

req_json=$(jq -n -c --arg q "$query" --arg s "$start_ts" --arg e "$end_ts" --argjson step "$step_s" \
  '{query: $q, startTime: $s, endTime: $e, stepSeconds: $step}')

if ! cm_curl_post "$CM_QUERY_URL" "$req_json" "$tmp/resp.json" 2>"$tmp/err"; then
  cat "$tmp/err" >&2
  exit 1
fi

if ! jq -c --arg q "$query" '
  def to_epoch:
    if type == "number" then .
    elif type == "string" then
      if test("^[0-9]+(\\.[0-9]+)?$") then tonumber
      else (sub("\\.[0-9]+Z$"; "Z") | fromdateiso8601)
      end
    else error("invalid timestamp")
    end;
  def typed_num:
    if .doubleValue != null then (.doubleValue | tonumber)
    elif .int64Value != null then (.int64Value | tonumber)
    elif .distributionValue.mean != null then (.distributionValue.mean | tonumber)
    else error("unsupported point value")
    end;
  def label_val:
    .stringValue // .int64Value // .boolValue // "";
  if type != "object" then
    error("response is not a JSON object")
  elif has("error") then
    error(.error.message // "cloud monitoring error")
  elif .status == "ok" and (.series | type) == "array" then
    {
      status: "ok",
      series: [.series[] | {name: (.name | tostring), points: [.points[] | [(.[0] | to_epoch), (.[1] | tonumber)]]}]
    }
  elif has("timeSeriesData") then
    (.timeSeriesDescriptor.labelDescriptors // []) as $descs
    | {
        status: "ok",
        series: [
          (.timeSeriesData // [])[]
          | (.labelValues // []) as $vals
          | ([range(0; ($vals | length)) as $i | (($descs[$i].key // "label_\($i)") + "=" + ($vals[$i] | label_val | tostring))] | join(",")) as $lbls
          | {
              name: (if $lbls != "" then $lbls else $q end),
              points: [
                (.pointData // [])[]
                | [((.timeInterval.endTime // .timeInterval.startTime) | to_epoch), ((.values // [])[0] | typed_num)]
              ] | sort_by(.[0])
            }
        ]
      }
  elif has("timeSeries") then
    {
      status: "ok",
      series: [
        (.timeSeries // [])[]
        | ((.metric.type // "") as $mtype | (.metric.labels // {} | to_entries | sort_by(.key) | map("\(.key)=\"\(.value)\"") | join(",")) as $lbls
          | {
              name: (if ($mtype != "" and $lbls != "") then "\($mtype){\($lbls)}" elif $mtype != "" then $mtype else $q end),
              points: [
                (.points // [])[]
                | [((.interval.endTime // .interval.startTime) | to_epoch), (.value | typed_num)]
              ] | sort_by(.[0])
            })
      ]
    }
  elif length == 0 then
    {status: "ok", series: []}
  else
    error("unexpected cloud-monitoring response shape")
  end
' "$tmp/resp.json" > "$tmp/out.json" 2>"$tmp/jq.err"; then
  cat "$tmp/jq.err" >&2
  exit 1
fi

cat "$tmp/out.json"
