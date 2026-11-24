#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
number=${1:?usage: merge.sh <number> <head sha>}
head=${2:?usage: merge.sh <number> <head sha>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

init_store
entry=$(jq -ce --arg num "$number" '[.[] | select((.number | tostring) == $num)] | .[0] // empty' "$FILE_CODEHOST_PRS" 2>/dev/null) \
  || { printf 'file-codehost: PR #%s not found\n' "$number" >&2; exit 1; }

merged=$(printf '%s' "$entry" | jq -r '.merged // false')
if [ "$merged" = "true" ]; then
  merged_head=$(printf '%s' "$entry" | jq -r '.head_sha')
  merge_sha=$(printf '%s' "$entry" | jq -r '.merge_sha')
  if [ "$merged_head" != "$head" ]; then
    printf 'file-codehost: PR #%s was merged at head %s, CI judged %s\n' "$number" "$merged_head" "$head" >&2
    exit 1
  fi
  printf '%s %s\n' "$merge_sha" "$merged_head"
  exit 0
fi

head_ref=$(printf '%s' "$entry" | jq -r '.head_ref')
live_sha=$(git --git-dir="$FILE_CODEHOST_REPO" rev-parse --verify "refs/heads/$head_ref" 2>/dev/null) \
  || { printf 'file-codehost: branch %s missing for PR #%s\n' "$head_ref" "$number" >&2; exit 1; }

if [ "$live_sha" != "$head" ]; then
  printf 'file-codehost: PR #%s head moved to %s since CI judged %s; not merged\n' "$number" "$live_sha" "$head" >&2
  exit 1
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git clone -q --branch main "$FILE_CODEHOST_REPO" "$work/repo" >&2
git -C "$work/repo" fetch -q "$FILE_CODEHOST_REPO" "refs/heads/$head_ref" >&2
GIT_AUTHOR_NAME=file-codehost \
GIT_AUTHOR_EMAIL=file-codehost@localhost \
GIT_COMMITTER_NAME=file-codehost \
GIT_COMMITTER_EMAIL=file-codehost@localhost \
  git -C "$work/repo" merge --no-ff -q -m "Merge PR #$number ($head_ref)" FETCH_HEAD >&2
git -C "$work/repo" push -q "$FILE_CODEHOST_REPO" HEAD:refs/heads/main >&2
merge_sha=$(git -C "$work/repo" rev-parse HEAD)

updated=$(jq -c --arg num "$number" --arg h "$head" --arg m "$merge_sha" \
  '[.[] | if (.number | tostring) == $num then . + {state: "closed", merged: true, head_sha: $h, merge_sha: $m} else . end]' \
  "$FILE_CODEHOST_PRS")
write_prs "$updated"
printf '%s %s\n' "$merge_sha" "$head"
