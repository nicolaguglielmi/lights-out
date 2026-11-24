#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-linear: verifies Linear GraphQL viewer + LINEAR_TEAM_ID accessibility.
# Silent on success, one stderr line and exit 1 on failure or timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/linear/env.sh
. "$here/env.sh"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
vars=$(jq -cn --arg teamId "$LINEAR_TEAM_ID" '{teamId: $teamId}')
# shellcheck disable=SC2016 # GraphQL variables use literal $
query='query($teamId: String!) { viewer { id } team(id: $teamId) { id name } }'

start=$(date +%s)
while :; do
  if linear_gql "$query" "$vars" "$tmp" \
     && jq -e '(.data.viewer.id != null) and (.data.team.id != null)' "$tmp" >/dev/null 2>&1; then
    exit 0
  fi
  case "${LINEAR_LAST_HTTP:-000}" in
    401 | 403 | 404) break ;;
  esac
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep 5
done
printf 'ready-linear: %s for team %s (%s)\n' "$LINEAR_API_URL" "$LINEAR_TEAM_ID" "$(linear_gql_error "$tmp")" >&2
exit 1
