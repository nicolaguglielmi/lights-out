#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# detect-remote-gitlab: matches a repository origin pointing to gitlab.com
# (or $GITLAB_HOST), including nested group paths (docs/adr/0101).
set -eu

host=$(printf '%s' "${REMOTE_HOST:-}" | tr '[:upper:]' '[:lower:]')
port=${REMOTE_PORT:-}
custom=$(printf '%s' "${GITLAB_HOST:-}" | tr '[:upper:]' '[:lower:]')
[ -n "$host" ] || exit 1

matched=""
if [ "$host" = "gitlab.com" ]; then
  matched=1
elif [ -n "$custom" ]; then
  case "$custom" in
    *:*) [ "${host}${port:+:$port}" = "$custom" ] && matched=1 ;;
    *) [ "$host" = "$custom" ] && matched=1 ;;
  esac
fi
[ -n "$matched" ] || exit 1

case "${REMOTE_PATH:-}" in
  '' | /* | */) exit 1 ;;
  */*) ;;
  *) exit 1 ;;
esac

url="https://$host"
if [ "${REMOTE_SCHEME:-}" = "https" ] && [ -n "$port" ] && [ "$port" != "443" ]; then
  url="https://$host:$port"
fi

printf 'CODE_HOST=gitlab\n'
printf 'GITLAB_URL=%s\n' "$url"
printf 'GITLAB_PROJECT_PATH=%s\n' "$REMOTE_PATH"
