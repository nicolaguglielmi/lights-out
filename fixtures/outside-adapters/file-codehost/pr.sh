#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
number=${1:?usage: pr.sh <number>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

init_store
entry=$(jq -ce --arg num "$number" '[.[] | select((.number | tostring) == $num)] | .[0] // empty' "$FILE_CODEHOST_PRS" 2>/dev/null) \
  || { printf 'file-codehost: PR #%s not found\n' "$number" >&2; exit 1; }

merged=$(printf '%s' "$entry" | jq -r '.merged // false')
head_ref=$(printf '%s' "$entry" | jq -r '.head_ref')
head_sha=$(printf '%s' "$entry" | jq -r '.head_sha')
if [ "$merged" != "true" ]; then
  live_sha=$(git --git-dir="$FILE_CODEHOST_REPO" rev-parse --verify "refs/heads/$head_ref" 2>/dev/null || printf '%s' "$head_sha")
  head_sha=$live_sha
fi

printf '%s' "$entry" | jq -c --arg h "$head_sha" '{
  number: .number,
  state: .state,
  merged: (.merged // false),
  head_sha: $h,
  merge_sha: (if .merged then .merge_sha else null end)
}'
