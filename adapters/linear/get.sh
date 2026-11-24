#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# get-linear: fetches Linear issue <id> via GraphQL and prints canonical Markdown.
set -eu
id=${1:-}
[ -n "$id" ] || { printf 'get-linear: usage: get.sh <issue-id>\n' >&2; exit 1; }
case "$id" in
  *[[:space:]]*) printf 'get-linear: invalid issue id: %s\n' "$id" >&2; exit 1 ;;
esac

# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/linear/env.sh
. "$here/env.sh"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
vars=$(jq -cn --arg id "$id" '{id: $id}')
# shellcheck disable=SC2016 # GraphQL variables use literal $
query='query($id: String!) { issue(id: $id) { id identifier title description state { name type } comments(first: 50) { nodes { body } } } }'

linear_gql "$query" "$vars" "$tmp" || {
  printf 'get-linear: query issue %s failed (%s)\n' "$id" "$(linear_gql_error "$tmp")" >&2
  exit 1
}
jq -e '.data.issue != null' "$tmp" >/dev/null 2>&1 || {
  printf 'get-linear: no such issue: %s\n' "$id" >&2
  exit 1
}
linear_render "$tmp"
