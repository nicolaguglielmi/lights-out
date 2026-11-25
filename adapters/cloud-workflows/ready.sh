#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-cloud-workflows: polls Google Cloud Workflows resource readiness up to
# READY_TIMEOUT seconds. Silent on exit 0; one stderr line on timeout.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/cloud-workflows/lib.sh
. "$here/lib.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if wf_resolve_token 2>/dev/null; then
    code=$(wf_curl GET "$WORKFLOWS_ADMIN_API_URL/v1/$WORKFLOWS_PARENT" "" "$tmp/resp.json")
    if [ "$code" = "200" ] \
        && jq -e '(.state // "ACTIVE") == "ACTIVE"' "$tmp/resp.json" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready: cloud-workflows not ready after %ss: %s/v1/%s\n' \
  "$READY_TIMEOUT" "$WORKFLOWS_ADMIN_API_URL" "$WORKFLOWS_PARENT" >&2
exit 1
