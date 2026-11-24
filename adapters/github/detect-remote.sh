#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# detect-remote-github: matches a repository origin pointing to github.com
# (or $GITHUB_HOST) with a two-segment owner/repo path (docs/adr/0101).
set -eu

host=$(printf '%s' "${REMOTE_HOST:-}" | tr '[:upper:]' '[:lower:]')
custom=$(printf '%s' "${GITHUB_HOST:-}" | tr '[:upper:]' '[:lower:]')
[ -n "$host" ] || exit 1

if [ "$host" != "github.com" ] && { [ -z "$custom" ] || [ "$host" != "$custom" ]; }; then
  exit 1
fi

case "${REMOTE_PATH:-}" in
  '' | /* | */ | */*/*) exit 1 ;;
  */*) ;;
  *) exit 1 ;;
esac

printf 'CODE_HOST=github\n'
printf 'GITHUB_REPO=%s\n' "$REMOTE_PATH"
