#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# put-linear: creates a Linear issue idempotently on `- Origin: <origin>`.
# Usage: put.sh <title> <origin> < body.md
set -eu
[ $# -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  printf 'put-linear: usage: put.sh <title> <origin> < body\n' >&2
  exit 1
}
title=$1
origin=$2

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/linear/env.sh
. "$here/env.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/body.md"

# shellcheck disable=SC2016 # GraphQL variables use literal $
lookup_q='query($teamId: String!, $originLine: String!) { issues(filter: { team: { id: { eq: $teamId } }, description: { contains: $originLine } }, first: 50) { nodes { id identifier description } } }'
lookup_v=$(jq -cn --arg teamId "$LINEAR_TEAM_ID" --arg originLine "- Origin: $origin" '{teamId: $teamId, originLine: $originLine}')
linear_gql "$lookup_q" "$lookup_v" "$work/lookup.json" || {
  printf 'put-linear: lookup issues for team %s failed (%s)\n' "$LINEAR_TEAM_ID" "$(linear_gql_error "$work/lookup.json")" >&2
  exit 1
}

existing=$(jq -r --arg orig "$origin" '
  [ .data.issues.nodes[]?
    | select(((.description // "") | split("\n") | map(rtrimstr("\r")) | index("- Origin: " + $orig)) != null)
    | (.identifier // .id) ][0] // empty
' "$work/lookup.json")
if [ -n "$existing" ]; then
  printf '%s\n' "$existing"
  exit 0
fi

desc=$(linear_upsert_meta "$work/body.md" "open" "$origin" "" "")
# shellcheck disable=SC2016 # GraphQL variables use literal $
create_q='mutation($teamId: String!, $title: String!, $description: String!) { issueCreate(input: { teamId: $teamId, title: $title, description: $description }) { success issue { id identifier } } }'
create_v=$(jq -cn --arg teamId "$LINEAR_TEAM_ID" --arg title "$title" --arg description "$desc" '{teamId: $teamId, title: $title, description: $description}')
linear_gql "$create_q" "$create_v" "$work/created.json" || {
  printf 'put-linear: issueCreate failed (%s)\n' "$(linear_gql_error "$work/created.json")" >&2
  exit 1
}
jq -e '.data.issueCreate.success == true' "$work/created.json" >/dev/null 2>&1 || {
  printf 'put-linear: issueCreate returned success=false\n' >&2
  exit 1
}
id=$(jq -r '.data.issueCreate.issue.identifier // .data.issueCreate.issue.id // empty' "$work/created.json")
[ -n "$id" ] || { printf 'put-linear: issueCreate returned no issue identifier\n' >&2; exit 1; }
printf '%s\n' "$id"
