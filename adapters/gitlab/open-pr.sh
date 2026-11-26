#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# open-pr-gitlab: opens a merge request from <branch> onto main, title and
# body from pr.json (docs/adr/0017 §1). A merge request is the PR, its iid
# the number. Idempotent: an open one from this branch is reused. Prints
# "<number> <url>". Not a gate.
set -eu
branch=${1:?usage: open-pr.sh <branch> <pr.json>}
pr_json=${2:?usage: open-pr.sh <branch> <pr.json>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/gitlab/env.sh
. "$here/env.sh"
die() { printf 'open-pr-gitlab: %s\n' "$*" >&2; exit 1; }

[ -s "$pr_json" ] || die "$pr_json is missing or empty"
title=$(jq -r '.title // ""' "$pr_json")
[ -n "$title" ] || die "$pr_json has no title"

mr=$(api -f -G "$GITLAB_PROJECT/merge_requests" --data-urlencode state=opened --data-urlencode "source_branch=$branch" \
  | jq -c 'first // empty') || die "listing merge requests failed"
if [ -z "$mr" ]; then
  target=main
  if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
    target=$(api -f "$GITLAB_PROJECT" | jq -er '.default_branch // empty') \
      || die "reading default branch for $GITLAB_PROJECT_PATH failed"
  fi
  payload=$(jq -c --arg b "$branch" --arg t "$target" '{source_branch: $b, target_branch: $t, title: .title, description: (.body // "")}' "$pr_json")
  # GitLab takes a push in the background: for a moment after it the new
  # branch is unknown to the merge request API, a 400 naming the source
  # branch. That one is retried, once a second, READY_TIMEOUT times (as
  # bootstrap.sh and merge.sh wait); any other answer fails, with its body.
  i=0
  while :; do
    tmp=$(mktemp)
    code=$(api -o "$tmp" -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d "$payload" \
      "$GITLAB_PROJECT/merge_requests") || code=000
    mr=$(cat "$tmp"); rm -f "$tmp"
    case "$code" in
      201) break ;;
      400) case "$mr" in *[Ss]ource*branch*) ;; *) die "create merge request failed: $code $mr" ;; esac
        i=$((i + 1))
        [ "$i" -lt "${READY_TIMEOUT:-60}" ] || die "create merge request failed: $code $mr, ${i}s after the push"
        sleep 1 ;;
      *) die "create merge request failed: $code $mr" ;;
    esac
  done
fi
printf '%s' "$mr" | jq -er '"\(.iid) \(.web_url)"' || die "no merge request in the answer: $mr"
