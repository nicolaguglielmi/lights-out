#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
branch=${1:?usage: open-pr.sh <branch> <pr.json>}
pr_json=${2:?usage: open-pr.sh <branch> <pr.json>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

init_store
[ -s "$pr_json" ] || { printf 'file-codehost: %s is missing or empty\n' "$pr_json" >&2; exit 1; }
title=$(jq -r '.title // ""' "$pr_json")
body=$(jq -r '.body // ""' "$pr_json")
[ -n "$title" ] || { printf 'file-codehost: %s has no title\n' "$pr_json" >&2; exit 1; }

existing=$(jq -r --arg h "$branch" \
  '[.[] | select(.state == "open" and .head_ref == $h)] | .[0].number // empty' "$FILE_CODEHOST_PRS")

if [ -n "$existing" ]; then
  number=$existing
else
  head_sha=$(git --git-dir="$FILE_CODEHOST_REPO" rev-parse --verify "refs/heads/$branch" 2>/dev/null) \
    || { printf 'file-codehost: branch %s does not exist in %s\n' "$branch" "$FILE_CODEHOST_REPO" >&2; exit 1; }
  number=$(jq -r '(map(.number) | max // 0) + 1' "$FILE_CODEHOST_PRS")
  updated=$(jq -c \
    --argjson num "$number" \
    --arg head "$branch" \
    --arg title "$title" \
    --arg body "$body" \
    --arg sha "$head_sha" \
    '. + [{
      number: $num,
      state: "open",
      merged: false,
      head_ref: $head,
      base_ref: "main",
      title: $title,
      body: $body,
      head_sha: $sha,
      merge_sha: null
    }]' "$FILE_CODEHOST_PRS")
  write_prs "$updated"
fi

printf '%s file://%s/pulls/%s\n' "$number" "$FILE_CODEHOST_DIR" "$number"
