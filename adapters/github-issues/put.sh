#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# put-github-issues: files a GitHub issue idempotently on `- Origin: <origin>`.
# Usage: put.sh <title> <origin> < body.md
set -eu
[ $# -ge 2 ] && [ -n "$1" ] && [ -n "$2" ] || {
  printf 'put-github-issues: usage: put.sh <title> <origin> < body\n' >&2
  exit 1
}
title=$1
origin=$2

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/github-issues/env.sh
. "$here/env.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
cat > "$work/body.md"

page=1
while [ "$page" -le 50 ]; do
  code=$(gh_issues_api GET "/repos/$GITHUB_REPO/issues?state=all&per_page=100&page=$page" "$work/list.json")
  [ "$code" = 200 ] || {
    printf 'put-github-issues: list issues on %s returned %s\n' "$GITHUB_REPO" "$code" >&2
    exit 1
  }
  existing=$(jq -r --arg orig "$origin" '
    [ .[] | select(has("pull_request") | not)
      | select(((.body // "") | split("\n") | map(rtrimstr("\r")) | index("- Origin: " + $orig)) != null)
      | .number ][0] // empty
  ' "$work/list.json")
  if [ -n "$existing" ]; then
    printf '%s\n' "$existing"
    exit 0
  fi
  count=$(jq 'length' "$work/list.json")
  [ "$count" -eq 100 ] || break
  page=$((page + 1))
done

full_body=$(gh_issues_upsert_meta "$work/body.md" "open" "$origin" "" "")
jq -cn --arg t "$title" --arg b "$full_body" '{title: $t, body: $b, labels: ["status:open"]}' > "$work/payload.json"
code=$(gh_issues_api POST "/repos/$GITHUB_REPO/issues" "$work/created.json" "$work/payload.json")
[ "$code" = 201 ] || {
  printf 'put-github-issues: create issue on %s returned %s: %s\n' "$GITHUB_REPO" "$code" \
    "$(jq -r '.message // empty' "$work/created.json" 2>/dev/null)" >&2
  exit 1
}
num=$(jq -r '.number // empty' "$work/created.json")
[ -n "$num" ] || { printf 'put-github-issues: created issue missing number\n' >&2; exit 1; }
printf '%s\n' "$num"
