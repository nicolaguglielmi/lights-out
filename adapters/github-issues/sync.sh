#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# sync-github-issues: idempotently updates issue state/labels/PR/ADR metadata
# and posts a deduplicated sync comment. Usage: sync.sh <id> <state> [pr_url] [adr_ref]
set -eu
id=${1:-}
state=${2:-}
pr_url=${3:-}
adr_ref=${4:-}
[ -n "$id" ] && [ -n "$state" ] || {
  printf 'sync-github-issues: usage: sync.sh <issue-number> <state> [pr_url] [adr_ref]\n' >&2
  exit 1
}
case "$id" in
  *[!0-9]*)
    printf 'sync-github-issues: no such issue: %s\n' "$id" >&2
    exit 1 ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/github-issues/env.sh
. "$here/env.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

code=$(gh_issues_api GET "/repos/$GITHUB_REPO/issues/$id" "$work/issue.json")
[ "$code" = 200 ] || {
  printf 'sync-github-issues: GET issue #%s on %s returned %s\n' "$id" "$GITHUB_REPO" "$code" >&2
  exit 1
}
jq -r '.body // ""' "$work/issue.json" > "$work/body.md"
new_body=$(gh_issues_upsert_meta "$work/body.md" "$state" "" "$pr_url" "$adr_ref")

lower_state=$(printf '%s' "$state" | tr '[:upper:]' '[:lower:]')
case "$lower_state" in
  closed|done|resolved|merged) gh_state=closed ;;
  *) gh_state=open ;;
esac

labels=$(jq -c --arg st "$state" \
  '[(.labels // [])[]?.name | select(startswith("status:") | not)] + ["status:" + $st]' \
  "$work/issue.json")
jq -cn --arg st "$gh_state" --arg b "$new_body" --argjson lbls "$labels" \
  '{state: $st, body: $b, labels: $lbls}' > "$work/patch.json"

code=$(gh_issues_api PATCH "/repos/$GITHUB_REPO/issues/$id" "$work/patched.json" "$work/patch.json")
[ "$code" = 200 ] || {
  printf 'sync-github-issues: PATCH issue #%s on %s returned %s\n' "$id" "$GITHUB_REPO" "$code" >&2
  exit 1
}

code=$(gh_issues_api GET "/repos/$GITHUB_REPO/issues/$id/comments?per_page=100" "$work/comments.json")
[ "$code" = 200 ] || {
  printf 'sync-github-issues: GET comments #%s on %s returned %s\n' "$id" "$GITHUB_REPO" "$code" >&2
  exit 1
}

marker="<!-- lights-out-sync: status=$state pr=$pr_url adr=$adr_ref -->"
if ! jq -e --arg m "$marker" '[.[]?.body // "" | contains($m)] | any' "$work/comments.json" >/dev/null 2>&1; then
  comment_body="$marker
- Status: $state"
  [ -z "$pr_url" ] || comment_body="$comment_body
- PR: $pr_url"
  [ -z "$adr_ref" ] || comment_body="$comment_body
- ADR: $adr_ref"
  jq -cn --arg b "$comment_body" '{body: $b}' > "$work/comment.json"
  code=$(gh_issues_api POST "/repos/$GITHUB_REPO/issues/$id/comments" "$work/comment_out.json" "$work/comment.json")
  [ "$code" = 201 ] || {
    printf 'sync-github-issues: POST comment #%s on %s returned %s\n' "$id" "$GITHUB_REPO" "$code" >&2
    exit 1
  }
fi
