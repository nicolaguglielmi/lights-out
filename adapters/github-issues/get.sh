#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# get-github-issues: prints GitHub issue <id> rendered as canonical Markdown.
set -eu
id=${1:-}
[ -n "$id" ] || { printf 'get-github-issues: usage: get.sh <issue-number>\n' >&2; exit 1; }
case "$id" in
  *[!0-9]*)
    printf 'get-github-issues: no such issue: %s\n' "$id" >&2
    exit 1 ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/github-issues/env.sh
. "$here/env.sh"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
code=$(gh_issues_api GET "/repos/$GITHUB_REPO/issues/$id" "$tmp")
[ "$code" = 200 ] || {
  printf 'get-github-issues: GET issue #%s on %s returned %s\n' "$id" "$GITHUB_REPO" "$code" >&2
  exit 1
}
if jq -e 'type != "object" or has("pull_request") or (.number == null)' "$tmp" >/dev/null 2>&1; then
  printf 'get-github-issues: #%s is not an issue on %s\n' "$id" "$GITHUB_REPO" >&2
  exit 1
fi
gh_issues_render "$tmp"
