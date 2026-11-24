#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# github-issues env.sh: configuration, secret-safe curl REST helper (token on
# stdin via -H @-, never in argv or URL), and Markdown metadata helpers.
set -eu

for _tool in curl jq; do
  command -v "$_tool" >/dev/null 2>&1 || {
    printf 'github-issues: %s is not on PATH\n' "$_tool" >&2
    exit 1
  }
done

if [ -z "${GITHUB_TOKEN:-}" ] || [ "$GITHUB_TOKEN" = replace-me ]; then
  printf 'github-issues: GITHUB_TOKEN is empty; set a token in .env\n' >&2
  exit 1
fi

case "${GITHUB_REPO:-}" in
  ''|replace-me)
    printf 'github-issues: GITHUB_REPO is empty; set <owner>/<name> in .env\n' >&2
    exit 1 ;;
  */*/* | /* | */)
    printf 'github-issues: GITHUB_REPO=%s is not <owner>/<name>\n' "$GITHUB_REPO" >&2
    exit 1 ;;
  */*) : ;;
  *)
    printf 'github-issues: GITHUB_REPO=%s is not <owner>/<name>\n' "$GITHUB_REPO" >&2
    exit 1 ;;
esac

GITHUB_API_URL=${GITHUB_API_URL:-https://api.github.com}
GITHUB_API_URL=${GITHUB_API_URL%/}
case "$GITHUB_API_URL" in
  http://* | https://*) ;;
  *)
    printf 'github-issues: GITHUB_API_URL=%s must start with http:// or https://\n' "$GITHUB_API_URL" >&2
    exit 1 ;;
esac
case "$GITHUB_API_URL" in
  *://*@*)
    printf 'github-issues: GITHUB_API_URL must not embed credentials\n' >&2
    exit 1 ;;
esac

READY_TIMEOUT=${READY_TIMEOUT:-60}

# gh_issues_api METHOD PATH OUT_FILE [PAYLOAD_FILE]
# Sends one REST request, writing the body to OUT_FILE and printing HTTP status.
# GITHUB_TOKEN is passed exclusively on curl's stdin (-H @-), never in argv.
gh_issues_api() {
  _method=$1 _path=$2 _out=$3 _payload=${4:-}
  if [ -n "$_payload" ]; then
    set -- -H 'Content-Type: application/json' --data-binary "@$_payload"
  else
    set --
  fi
  _code=$(printf 'Authorization: Bearer %s\n' "$GITHUB_TOKEN" \
    | curl -sS --connect-timeout 5 --max-time 30 -o "$_out" -w '%{http_code}' -H @- \
        -X "$_method" -H 'Accept: application/vnd.github+json' -H 'X-GitHub-Api-Version: 2022-11-28' \
        "$@" "$GITHUB_API_URL$_path" 2>/dev/null) || _code=000
  printf '%s' "${_code:-000}"
}

# gh_issues_upsert_meta BODY_FILE STATUS ORIGIN PR ADR -> stdout Markdown body
gh_issues_upsert_meta() {
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

# gh_issues_render ISSUE_JSON_FILE -> canonical Markdown ticket on stdout
gh_issues_render() {
  jq -r '
    def strip_cr: rtrimstr("\r");
    . as $iss
    | ($iss.body // "" | split("\n") | map(strip_cr)) as $lines
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
    | (([($iss.labels // [])[]?.name | select(startswith("status:")) | sub("^status:"; "")])[-1] // null) as $lbl_st
    | (if $iss.state == "closed" and $lbl_st == null then "closed" else ($lbl_st // $p.status // $iss.state // "open") end) as $st
    | ($p.origin // ([$trimmed[] | select(test("^- Origin: ")) | sub("^- Origin: "; "")][0] // "")) as $orig
    | ([
        "- Status: " + $st,
        (if $orig != "" then "- Origin: " + $orig else empty end),
        (if ($p.pr // "") != "" then "- PR: " + $p.pr else empty end),
        (if ($p.adr // "") != "" then "- ADR: " + $p.adr else empty end)
      ]) as $hdr
    | "# \($iss.number) — \($iss.title)\n\n" + ($hdr | join("\n"))
      + (if ($p.rest | length) > 0 then "\n\n" + ($p.rest | join("\n")) else "" end)
  ' "$1"
}
