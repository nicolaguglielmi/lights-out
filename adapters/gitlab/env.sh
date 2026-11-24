#!/bin/sh
# shellcheck disable=SC2034 # every var here is for the sourcing script
# SPDX-License-Identifier: Apache-2.0
#
# gitlab-env: identity and endpoints for GitLab — either a remote repository
# when GITLAB_PROJECT_PATH is set (docs/adr/0101, using GITLAB_TOKEN via
# curl stdin and adapters/gitlab/credential.sh), or the local GitLab CE
# conformance instance beside the plane at GITLAB_PORT (docs/adr/0058 §8)
# when GITLAB_PROJECT_PATH is unset. Sourced, never executed.
if [ -n "${GITLAB_PROJECT_PATH:-}" ]; then
  if [ -z "${GITLAB_TOKEN:-}" ] || [ "$GITLAB_TOKEN" = replace-me ]; then
    printf 'gitlab: GITLAB_TOKEN is empty; put a token for your repository in .env (see .env.example, docs/adr/0101)\n' >&2
    exit 1
  fi
  case "$GITLAB_PROJECT_PATH" in
    /* | */ | *//* | *..* )
      printf 'gitlab: GITLAB_PROJECT_PATH=%s is not <owner>/<repo>\n' "$GITLAB_PROJECT_PATH" >&2; exit 1 ;;
    */*) : ;;
    *) printf 'gitlab: GITLAB_PROJECT_PATH=%s is not <owner>/<repo>\n' "$GITLAB_PROJECT_PATH" >&2; exit 1 ;;
  esac
  : "${GITLAB_URL:=https://gitlab.com}"
  GITLAB_URL=${GITLAB_URL%/}
  case "$GITLAB_URL" in
    https://* | http://*) ;;
    *) printf 'gitlab: GITLAB_URL=%s must start with http:// or https://\n' "$GITLAB_URL" >&2; exit 1 ;;
  esac
  case "$GITLAB_URL" in
    *://*@*) printf 'gitlab: GITLAB_URL must not embed credentials\n' >&2; exit 1 ;;
  esac
  GITLAB_OWNER=${GITLAB_PROJECT_PATH%/*}
  GITLAB_REPO=${GITLAB_PROJECT_PATH##*/}
  GITLAB_API="$GITLAB_URL/api/v4"
  if command -v jq >/dev/null 2>&1; then
    _gitlab_enc=$(printf '%s' "$GITLAB_PROJECT_PATH" | jq -Rr @uri)
  else
    _gitlab_enc=$(printf '%s' "$GITLAB_PROJECT_PATH" | sed 's/%/%25/g; s|/|%2F|g; s/ /%20/g; s/+/%2B/g')
  fi
  GITLAB_PROJECT="$GITLAB_API/projects/$_gitlab_enc"
  GITLAB_CLONE_URL="$GITLAB_URL/$GITLAB_PROJECT_PATH.git"
  GITLAB_PUSH_URL="$GITLAB_CLONE_URL"
  CODEHOST_SECRET=$GITLAB_TOKEN
else
  : "${PLANE_HOST:=host.containers.internal}"
  : "${GITLAB_PORT:=8929}"
  GITLAB_OWNER=root
  # The workload's repository, as Gitea's (docs/adr/0037 §9).
  GITLAB_REPO=workload
  GITLAB_TOKEN=lights-out-local-gitlab-not-a-secret
  GITLAB_URL="http://$PLANE_HOST:$GITLAB_PORT"
  GITLAB_API="$GITLAB_URL/api/v4"
  # The project id GitLab's API takes: the path, URL-encoded.
  GITLAB_PROJECT="$GITLAB_API/projects/$GITLAB_OWNER%2F$GITLAB_REPO"
  # Authenticated URL for a push, passed as an argument only, never written to
  # a .git/config (docs/adr/0017 §4); oauth2 is the user GitLab takes a token as.
  GITLAB_PUSH_URL="http://oauth2:$GITLAB_TOKEN@$PLANE_HOST:$GITLAB_PORT/$GITLAB_OWNER/$GITLAB_REPO.git"
  # Anonymous clone URL: the project is public.
  GITLAB_CLONE_URL="$GITLAB_URL/$GITLAB_OWNER/$GITLAB_REPO.git"
  # The secret gates/codehost-conformance.sh case 10 looks for.
  CODEHOST_SECRET=$GITLAB_TOKEN
fi

# api CURL-ARGS...: one call to the API, the token on curl's stdin, never in
# an argv (the docs/adr/0043 §2 rule the GitHub verbs keep). -f is the
# caller's choice: some answers are expected failures.
api() {
  printf 'PRIVATE-TOKEN: %s\n' "$GITLAB_TOKEN" | curl -sS --connect-timeout 2 --max-time 30 -H @- "$@"
}
