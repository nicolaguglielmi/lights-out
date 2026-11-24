#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# detect-remote-gitea: matches a repository origin pointing to the local
# plane's Gitea instance ($PLANE_HOST:$CODE_HOST_PORT or localhost:$CODE_HOST_PORT).
set -eu

: "${PLANE_HOST:=host.containers.internal}"
: "${CODE_HOST_PORT:=3000}"

host=$(printf '%s' "${REMOTE_HOST:-}" | tr '[:upper:]' '[:lower:]')
plane=$(printf '%s' "$PLANE_HOST" | tr '[:upper:]' '[:lower:]')
port=${REMOTE_PORT:-}
[ -n "$host" ] && [ -n "${REMOTE_PATH:-}" ] || exit 1
[ "$port" = "$CODE_HOST_PORT" ] || exit 1

if [ "$host" != "$plane" ] && [ "$host" != "localhost" ]; then
  exit 1
fi

printf 'CODE_HOST=gitea\n'
