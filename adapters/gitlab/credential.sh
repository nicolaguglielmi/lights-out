#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# credential-gitlab: a git credential helper (gitcredentials(7)). On `get`
# for GITLAB_URL (default https://gitlab.com) it answers with oauth2 and
# GITLAB_TOKEN from the environment, so the token is never in a URL, an argv
# or a .git/config (docs/adr/0043 §2, 0101); for any other host, and on
# `store` or `erase`, it prints nothing.
[ "${1:-}" = get ] || exit 0
protocol="" host=""
while IFS= read -r line; do
  [ -n "$line" ] || break
  case "$line" in
    protocol=*) protocol=${line#protocol=} ;;
    host=*) host=${line#host=} ;;
  esac
done
_url=${GITLAB_URL:-https://gitlab.com}
_url=${_url%/}
_target_proto=${_url%%://*}
_rest=${_url#*://}
_target_host=${_rest%%/*}
[ "$protocol" = "$_target_proto" ] && [ "$host" = "$_target_host" ] || exit 0
[ -n "${GITLAB_TOKEN:-}" ] && [ "$GITLAB_TOKEN" != replace-me ] || exit 0
printf 'username=oauth2\npassword=%s\n' "$GITLAB_TOKEN"
