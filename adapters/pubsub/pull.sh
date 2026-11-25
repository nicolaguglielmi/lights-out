#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# pull-pubsub: pulls at most one message from Google Cloud Pub/Sub REST v1
# ($PUBSUB_URL/subscriptions/$PUBSUB_SUBSCRIPTION:pull) and prints
# "<ackId> <data>" on one stdout line, or nothing when empty.
set -eu
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/pubsub/lib.sh
. "$here/lib.sh"

pubsub_resolve_token || exit 1
ensure || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

if [ "$PUBSUB_RETURN_IMMEDIATELY" = "false" ]; then
  printf '{"maxMessages":1}\n' > "$tmp/req.json"
else
  printf '{"returnImmediately":true,"maxMessages":1}\n' > "$tmp/req.json"
fi

code=$(pubsub_curl POST "$PUBSUB_URL/subscriptions/$PUBSUB_SUBSCRIPTION:pull" "$tmp/req.json" "$tmp/resp.json")
[ "$code" = "200" ] || {
  printf 'pull-pubsub: pull returned %s\n' "$code" >&2
  exit 1
}
jq -r '.receivedMessages[0]? // empty | "\(.ackId) \(.message.data | @base64d)"' "$tmp/resp.json"
