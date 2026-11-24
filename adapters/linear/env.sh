#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# linear env.sh: configuration, secret-safe curl GraphQL helper (token on
# stdin via -H @-, never in argv or URL), and Markdown metadata helpers.
set -eu

for _tool in curl jq; do
  command -v "$_tool" >/dev/null 2>&1 || {
    printf 'linear: %s is not on PATH\n' "$_tool" >&2
    exit 1
  }
done

if [ -z "${LINEAR_TOKEN:-}" ] || [ "$LINEAR_TOKEN" = replace-me ]; then
  printf 'linear: LINEAR_TOKEN is empty; set a token in .env\n' >&2
  exit 1
fi

if [ -z "${LINEAR_TEAM_ID:-}" ] || [ "$LINEAR_TEAM_ID" = replace-me ]; then
  printf 'linear: LINEAR_TEAM_ID is empty; set your team id in .env\n' >&2
  exit 1
fi

_LINEAR_API_HOST=api.linear.app
LINEAR_API_URL=${LINEAR_API_URL:-"https://$_LINEAR_API_HOST/graphql"}
LINEAR_API_URL=${LINEAR_API_URL%/}
case "$LINEAR_API_URL" in
  http://* | https://*) ;;
  *)
    printf 'linear: LINEAR_API_URL=%s must start with http:// or https://\n' "$LINEAR_API_URL" >&2
    exit 1 ;;
esac
case "$LINEAR_API_URL" in
  *://*@*)
    printf 'linear: LINEAR_API_URL must not embed credentials\n' >&2
    exit 1 ;;
esac

READY_TIMEOUT=${READY_TIMEOUT:-60}

case "$LINEAR_TOKEN" in
  lin_api_*) _LINEAR_AUTH_HDR="Authorization: $LINEAR_TOKEN" ;;
  *) _LINEAR_AUTH_HDR="Authorization: Bearer $LINEAR_TOKEN" ;;
esac

# linear_gql QUERY VARS_JSON OUT_FILE -> exits 0 on HTTP 200 without GraphQL errors
linear_gql() {
  _query=$1 _vars=$2 _out=$3
  _req=$(mktemp)
  if ! jq -cn --arg q "$_query" --argjson v "$_vars" '{query: $q, variables: $v}' > "$_req"; then
    rm -f "$_req"
    return 1
  fi
  _code=$(printf '%s\n' "$_LINEAR_AUTH_HDR" \
    | curl -sS --connect-timeout 5 --max-time 30 -o "$_out" -w '%{http_code}' -H @- \
        -H 'Content-Type: application/json' --data-binary @"$_req" "$LINEAR_API_URL" 2>/dev/null) || _code=000
  rm -f "$_req"
  LINEAR_LAST_HTTP=${_code:-000}
  [ "$LINEAR_LAST_HTTP" = 200 ] || return 1
  jq -e 'type == "object" and ((.errors // []) | length == 0) and has("data")' "$_out" >/dev/null 2>&1
}

linear_gql_error() {
  _out=$1
  _msg=$(jq -r '.errors[0].message // empty' "$_out" 2>/dev/null) || _msg=""
  if [ -n "$_msg" ]; then
    printf 'HTTP %s: %s' "${LINEAR_LAST_HTTP:-000}" "$_msg"
  else
    printf 'HTTP %s' "${LINEAR_LAST_HTTP:-000}"
  fi
}

# linear_upsert_meta BODY_FILE STATUS ORIGIN PR ADR -> stdout Markdown description
linear_upsert_meta() {
  _bf=$1 _st=$2 _orig=$3 _pr=$4 _adr=$5
  jq -Rsr --arg st "$_st" --arg orig "$_orig" --arg pr "$_pr" --arg adr "$_adr" '
    def strip_cr: rtrimstr("\r");
    (split("\n") | map(strip_cr)) as $lines
    | (if ($lines | length) > 0 and ($lines[-1] == "") then $lines[:-1] else $lines end) as $trimmed
    | reduce $trimmed[] as $ln (
        {in_head: true, status: null, origin: null, pr: null, adr: null, rest: []};
        if .in_head and ($ln == "") and (.rest | length == 0) then .
        elif .in_head and ($ln | test("^- Status: ")) then .status = ($ln | sub("^- Status: "; ""))
        elif .in_head and ($ln | test("^- Origin: ")) then .origin = ($ln | sub("^- Origin: "; ""))
        elif .in_head and ($ln | test("^- PR: ")) then .pr = ($ln | sub("^- PR: "; ""))
        elif .in_head and ($ln | test("^- ADR: ")) then .adr = ($ln | sub("^- ADR: "; ""))
        else .in_head = false | .rest += [$ln]
        end
      ) | . as $p
    | (if $st != "" then $st else $p.status end) as $final_st
    | (if $orig != "" then $orig else $p.origin end) as $final_orig
    | (if $pr != "" then $pr else $p.pr end) as $final_pr
    | (if $adr != "" then $adr else $p.adr end) as $final_adr
    | ([
        (if ($final_st // "") != "" then "- Status: " + $final_st else empty end),
        (if ($final_orig // "") != "" then "- Origin: " + $final_orig else empty end),
        (if ($final_pr // "") != "" then "- PR: " + $final_pr else empty end),
        (if ($final_adr // "") != "" then "- ADR: " + $final_adr else empty end)
      ]) as $hdr
    | ($hdr | join("\n"))
      + (if ($p.rest | length) > 0 then "\n\n" + ($p.rest | join("\n")) else "" end)
  ' "$_bf"
}

# linear_render ISSUE_JSON_FILE -> canonical Markdown ticket on stdout
linear_render() {
  jq -r '
    def strip_cr: rtrimstr("\r");
    .data.issue as $iss
    | ($iss.description // "" | split("\n") | map(strip_cr)) as $lines
    | (if ($lines | length) > 0 and ($lines[-1] == "") then $lines[:-1] else $lines end) as $trimmed
    | reduce $trimmed[] as $ln (
        {in_head: true, status: null, origin: null, pr: null, adr: null, rest: []};
        if .in_head and ($ln == "") and (.rest | length == 0) then .
        elif .in_head and ($ln | test("^- Status: ")) then .status = ($ln | sub("^- Status: "; ""))
        elif .in_head and ($ln | test("^- Origin: ")) then .origin = ($ln | sub("^- Origin: "; ""))
        elif .in_head and ($ln | test("^- PR: ")) then .pr = ($ln | sub("^- PR: "; ""))
        elif .in_head and ($ln | test("^- ADR: ")) then .adr = ($ln | sub("^- ADR: "; ""))
        else .in_head = false | .rest += [$ln]
        end
      ) | . as $p
    | ($p.status // $iss.state.name // "open") as $st
    | ($p.origin // ([$trimmed[] | select(test("^- Origin: ")) | sub("^- Origin: "; "")][0] // "")) as $orig
    | ([
        "- Status: " + $st,
        (if $orig != "" then "- Origin: " + $orig else empty end),
        (if ($p.pr // "") != "" then "- PR: " + $p.pr else empty end),
        (if ($p.adr // "") != "" then "- ADR: " + $p.adr else empty end)
      ]) as $hdr
    | "# \($iss.identifier // $iss.id) — \($iss.title)\n\n" + ($hdr | join("\n"))
      + (if ($p.rest | length) > 0 then "\n\n" + ($p.rest | join("\n")) else "" end)
  ' "$1"
}
