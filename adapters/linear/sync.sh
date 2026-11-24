#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# sync-linear: idempotently updates Linear issue workflow state, description
# metadata (- Status:, - PR:, - ADR:), and deduplicated sync comment.
# Usage: sync.sh <id> <state> [pr_url] [adr_ref]
set -eu
id=${1:-}
state=${2:-}
pr_url=${3:-}
adr_ref=${4:-}
[ -n "$id" ] && [ -n "$state" ] || {
  printf 'sync-linear: usage: sync.sh <issue-id> <state> [pr_url] [adr_ref]\n' >&2
  exit 1
}
case "$id" in
  *[[:space:]]*) printf 'sync-linear: invalid issue id: %s\n' "$id" >&2; exit 1 ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/linear/env.sh
. "$here/env.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# shellcheck disable=SC2016 # GraphQL variables use literal $
fetch_q='query($id: String!, $teamId: String!) { issue(id: $id) { id identifier description state { id name type } comments(first: 50) { nodes { id body } } } team(id: $teamId) { states { nodes { id name type } } } }'
fetch_v=$(jq -cn --arg id "$id" --arg teamId "$LINEAR_TEAM_ID" '{id: $id, teamId: $teamId}')
linear_gql "$fetch_q" "$fetch_v" "$work/fetch.json" || {
  printf 'sync-linear: fetch issue %s failed (%s)\n' "$id" "$(linear_gql_error "$work/fetch.json")" >&2
  exit 1
}
jq -e '.data.issue != null' "$work/fetch.json" >/dev/null 2>&1 || {
  printf 'sync-linear: no such issue: %s\n' "$id" >&2
  exit 1
}

issue_uuid=$(jq -r '.data.issue.id' "$work/fetch.json")
jq -r '.data.issue.description // ""' "$work/fetch.json" > "$work/body.md"
new_desc=$(linear_upsert_meta "$work/body.md" "$state" "" "$pr_url" "$adr_ref")

state_id=$(jq -r --arg st "$state" '
  ($st | ascii_downcase) as $low
  | (.data.team.states.nodes // []) as $nodes
  | (
      [$nodes[] | select((.name | ascii_downcase) == $low)][0]
      // (if ($low == "in-progress" or $low == "in_progress" or $low == "in progress" or $low == "started") then
            ([$nodes[] | select((.name | ascii_downcase) == "in progress" or .type == "started")][0])
          elif ($low == "open" or $low == "todo" or $low == "backlog") then
            ([$nodes[] | select((.name | ascii_downcase) == "todo" or (.name | ascii_downcase) == "backlog" or .type == "unstarted")][0])
          elif ($low == "closed" or $low == "done" or $low == "resolved" or $low == "merged") then
            ([$nodes[] | select((.name | ascii_downcase) == "done" or .type == "completed")][0])
          elif ($low == "canceled" or $low == "cancelled") then
            ([$nodes[] | select(.type == "canceled")][0])
          else null end)
    )
  | .id // empty
' "$work/fetch.json")

if [ -n "$state_id" ]; then
  # shellcheck disable=SC2016 # GraphQL variables use literal $
  upd_q='mutation($id: String!, $description: String!, $stateId: String!) { issueUpdate(id: $id, input: { description: $description, stateId: $stateId }) { success issue { id } } }'
  upd_v=$(jq -cn --arg id "$issue_uuid" --arg description "$new_desc" --arg stateId "$state_id" '{id: $id, description: $description, stateId: $stateId}')
else
  # shellcheck disable=SC2016 # GraphQL variables use literal $
  upd_q='mutation($id: String!, $description: String!) { issueUpdate(id: $id, input: { description: $description }) { success issue { id } } }'
  upd_v=$(jq -cn --arg id "$issue_uuid" --arg description "$new_desc" '{id: $id, description: $description}')
fi

linear_gql "$upd_q" "$upd_v" "$work/updated.json" || {
  printf 'sync-linear: issueUpdate %s failed (%s)\n' "$id" "$(linear_gql_error "$work/updated.json")" >&2
  exit 1
}
jq -e '.data.issueUpdate.success == true' "$work/updated.json" >/dev/null 2>&1 || {
  printf 'sync-linear: issueUpdate %s returned success=false\n' "$id" >&2
  exit 1
}

marker="<!-- lights-out-sync: status=$state pr=$pr_url adr=$adr_ref -->"
if ! jq -e --arg m "$marker" '[.data.issue.comments.nodes[]?.body // "" | contains($m)] | any' "$work/fetch.json" >/dev/null 2>&1; then
  comment_body="$marker
- Status: $state"
  [ -z "$pr_url" ] || comment_body="$comment_body
- PR: $pr_url"
  [ -z "$adr_ref" ] || comment_body="$comment_body
- ADR: $adr_ref"
  # shellcheck disable=SC2016 # GraphQL variables use literal $
  cmt_q='mutation($issueId: String!, $body: String!) { commentCreate(input: { issueId: $issueId, body: $body }) { success } }'
  cmt_v=$(jq -cn --arg issueId "$issue_uuid" --arg body "$comment_body" '{issueId: $issueId, body: $body}')
  linear_gql "$cmt_q" "$cmt_v" "$work/comment.json" || {
    printf 'sync-linear: commentCreate %s failed (%s)\n' "$id" "$(linear_gql_error "$work/comment.json")" >&2
    exit 1
  }
fi
