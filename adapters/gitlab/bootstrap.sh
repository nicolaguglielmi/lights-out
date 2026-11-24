#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# bootstrap-gitlab: in local conformance mode (GITLAB_PROJECT_PATH unset),
# creates the public project root/workload and force-pushes the workload
# repository's HEAD to its main (docs/adr/0058 §8). In remote mode
# (GITLAB_PROJECT_PATH set), creates and pushes nothing (docs/adr/0043 §3,
# 0101): checks attached mode, token access, and that the remote default
# branch head matches the attached clone's HEAD.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/gitlab/env.sh
. "$here/env.sh"
die() { printf 'bootstrap-gitlab: %s\n' "$*" >&2; exit 1; }

if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
  [ -n "${WORKLOAD_ATTACHED:-}" ] \
    || die "remote CODE_HOST=gitlab serves an attached repository: ./go attach <your clone>"
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  code=$(api -o "$tmp" -w '%{http_code}' "$GITLAB_PROJECT") || code=000
  case "$code" in
    200) : ;;
    401) die "GitLab refused GITLAB_TOKEN (401); check the token in .env" ;;
    404) die "$GITLAB_PROJECT_PATH not found with this token (404): check GITLAB_PROJECT_PATH and GITLAB_TOKEN in .env" ;;
    *) die "GET $GITLAB_PROJECT_PATH returned $code" ;;
  esac
  base=$(jq -r '.default_branch // empty' "$tmp")
  [ -n "$base" ] || die "$GITLAB_PROJECT_PATH reports no default branch"
  base_enc=$(printf '%s' "$base" | jq -Rr @uri)
  code=$(api -o "$tmp" -w '%{http_code}' "$GITLAB_PROJECT/repository/branches/$base_enc") || code=000
  [ "$code" = 200 ] || die "GET branch $base of $GITLAB_PROJECT_PATH returned $code"
  remote=$(jq -r '.commit.id // empty' "$tmp")
  local=$(git -C "${WORKLOAD:?WORKLOAD not set; run this via ./go}" rev-parse HEAD)
  [ "$remote" = "$local" ] \
    || die "$GITLAB_PROJECT_PATH's $base is at $remote, the attached clone at $local: pull, then ./go attach again"
  exit 0
fi

# 1. The project, public. 201 created, 400 "has already been taken".
tmp=$(mktemp)
code=$(api -o "$tmp" -w '%{http_code}' -X POST "$GITLAB_API/projects" \
  -d "name=$GITLAB_REPO" -d "path=$GITLAB_REPO" -d visibility=public -d initialize_with_readme=false) || code=000
body=$(cat "$tmp"); rm -f "$tmp"
case "$code:$body" in
  201:* | 400:*"has already been taken"*) ;;
  *) die "create project returned $code: $body" ;;
esac

unprotect() { api -o /dev/null -X DELETE "$GITLAB_PROJECT/protected_branches/main" || :; }

# 2. main, a mirror of the workload repository's HEAD.
if [ -z "${CODE_HOST_KEEP_MAIN:-}" ]; then
  unprotect
  git -C "${WORKLOAD:-.}" push --force "$GITLAB_PUSH_URL" HEAD:refs/heads/main >&2 || die "push to main failed"
  unprotect
fi

# 3. GitLab takes the push in the background: wait until the project has a
# repository and main is where the push left it.
want=$(git -C "${WORKLOAD:-.}" rev-parse HEAD)
: "${READY_TIMEOUT:=60}"
i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  got=$(api -f "$GITLAB_PROJECT/repository/branches/main" 2>/dev/null | jq -r '.commit.id // empty' 2>/dev/null) || got=""
  if [ -n "$got" ] && { [ -n "${CODE_HOST_KEEP_MAIN:-}" ] || [ "$got" = "$want" ]; }; then
    exit 0
  fi
  i=$((i + 1)); sleep 1
done
die "main is at [${got:-nothing}] ${READY_TIMEOUT}s after the push, want $want"
