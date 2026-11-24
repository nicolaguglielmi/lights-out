#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-github-issues: polls GET /repos/$GITHUB_REPO until HTTP 200.
# Silent on success, one stderr line and exit 1 on failure or timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/github-issues/env.sh
. "$here/env.sh"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
start=$(date +%s)
code=000
while :; do
  code=$(gh_issues_api GET "/repos/$GITHUB_REPO" "$tmp")
  case "$code" in
    200) exit 0 ;;
    401 | 403 | 404) break ;;
  esac
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep 5
done
printf 'ready-github-issues: GET %s/repos/%s returned %s\n' "$GITHUB_API_URL" "$GITHUB_REPO" "$code" >&2
exit 1
