#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# push-gitlab: force-pushes <workspace>'s HEAD to <branch>, authenticated,
# the URL passed as an argument only (docs/adr/0017 §4). Not a gate.
set -eu
workspace=${1:?usage: push.sh <workspace> <branch>}
branch=${2:?usage: push.sh <workspace> <branch>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/gitlab/env.sh
. "$here/env.sh"
if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
  case "$branch" in
    ticket/* | conformance/*) : ;;
    *) printf 'push-gitlab: refusing to push %s: lights-out pushes only ticket/<id> branches to your repository\n' "$branch" >&2; exit 1 ;;
  esac
fi
git -C "$workspace" push --force "$GITLAB_PUSH_URL" "HEAD:refs/heads/$branch" >&2
