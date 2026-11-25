#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready-pubsub: polls Google Cloud Pub/Sub REST v1 (auto-creating topic and
# subscription when PUBSUB_AUTO_CREATE=1, or checking subscription GET when 0)
# up to READY_TIMEOUT seconds. Silent on exit 0; one stderr line on exit 1.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/pubsub/lib.sh
. "$here/lib.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

i=0
while [ "$i" -lt "$READY_TIMEOUT" ]; do
  if pubsub_resolve_token 2>/dev/null; then
    if [ "$PUBSUB_AUTO_CREATE" = "1" ]; then
      if ensure 2>/dev/null; then
        exit 0
      fi
    else
      code=$(pubsub_curl GET "$PUBSUB_URL/subscriptions/$PUBSUB_SUBSCRIPTION" "" "$tmp/resp.json")
      if [ "$code" = "200" ]; then
        exit 0
      fi
    fi
  fi
  i=$((i + 1))
  if [ "$i" -lt "$READY_TIMEOUT" ]; then
    sleep 1
  fi
done

printf 'ready: pubsub not ready after %ss: %s\n' "$READY_TIMEOUT" "$PUBSUB_URL" >&2
exit 1
