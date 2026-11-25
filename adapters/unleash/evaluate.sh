#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# evaluate-unleash: evaluates <flag> [version] against Unleash client/frontend
# API using stdin -H @- auth. Prints true or false; exits 1 with empty stdout on error.
set -eu
flag=${1:-}
version=${2:-}
[ -n "$flag" ] || { printf 'evaluate-unleash: usage: evaluate.sh <flag> [version]\n' >&2; exit 1; }
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/unleash/env.sh
. "$here/env.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

enc_flag=$(unleash_encode "$flag")
code=$(unleash_curl GET "$UNLEASH_URL/api/client/features/$enc_flag" "$tmp/resp.json")
if [ "$code" != "200" ] && [ "$code" = "404" ]; then
  fcode=$(unleash_curl GET "$UNLEASH_URL/api/frontend" "$tmp/frontend.json")
  if [ "$fcode" = "200" ] && jq -e 'type == "object" and (.toggles | type == "array")' "$tmp/frontend.json" >/dev/null 2>&1; then
    mv "$tmp/frontend.json" "$tmp/resp.json"
    code=200
  fi
fi

if [ "$code" != "200" ]; then
  printf 'evaluate-unleash: GET %s failed for %s (HTTP %s)\n' "$UNLEASH_URL" "$flag" "$code" >&2
  exit 1
fi

# shellcheck disable=SC2016 # jq variables inside single-quoted program are intentional
val=$(jq -r --arg flag "$flag" --arg ver "$version" '
  def match_str($val; $c):
    ($c.caseInsensitive // false) as $ci
    | (if $ci then ($val | ascii_downcase) else $val end) as $v
    | (($c.values // []) | map(tostring | if $ci then ascii_downcase else . end)) as $vals
    | (($c.value // "") | tostring | if $ci then ascii_downcase else . end) as $single
    | ($c.operator // "IN") as $op
    | (if $op == "IN" or $op == "STR_IN" then
         ($vals | index($v)) != null
       elif $op == "NOT_IN" then
         ($vals | index($v)) == null
       elif $op == "STR_EQ" then
         ($v == $single or (($vals | length) > 0 and ($vals | index($v)) != null))
       elif $op == "STR_CONTAINS" then
         any($vals[]; . as $s | ($v | contains($s)))
       elif $op == "STR_STARTS_WITH" then
         any($vals[]; . as $s | ($v | startswith($s)))
       elif $op == "STR_ENDS_WITH" then
         any($vals[]; . as $s | ($v | endswith($s)))
       else
         false
       end);
  def constraint_ok($c):
    ($c.contextName // "") as $ctx
    | ($c.inverted // false) as $inv
    | (if $ctx == "version" or $ctx == "appVersion" then
         (if $ver == "" then
            (($c.operator // "IN") == "NOT_IN")
          else
            match_str($ver; $c)
          end)
       else
         false
       end) as $raw
    | if $inv then ($raw | not) else $raw end;
  def strategy_ok($s):
    if ($s.disabled // false) then false
    elif (($s.constraints // []) | length) == 0 then true
    else all(($s.constraints // [])[]; constraint_ok(.))
    end;
  def pick_feature:
    if type != "object" then null
    elif has("toggles") and (.toggles | type == "array") then
      ([.toggles[] | select(.name == $flag)][0] // null)
    elif has("features") and (.features | type == "array") then
      ([.features[] | select(.name == $flag)][0] // null)
    elif ((has("name") | not) or .name == $flag) and (.enabled | type == "boolean") then
      .
    else null
    end;
  pick_feature as $f
  | if $f == null or ($f.enabled | type) != "boolean" then empty
    elif $f.enabled == false then "false"
    elif ($f.strategies | type) == "array" and (($f.strategies | length) > 0) then
      if any($f.strategies[]; strategy_ok(.)) then "true" else "false" end
    else "true"
    end
' "$tmp/resp.json" 2>/dev/null) || val=""

case "$val" in
  true|false) printf '%s\n' "$val" ;;
  *)
    printf 'evaluate-unleash: malformed or missing feature response for %s\n' "$flag" >&2
    exit 1 ;;
esac
