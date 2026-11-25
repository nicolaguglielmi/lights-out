#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ack-pubsub: acknowledges one message by ackId on Google Cloud Pub/Sub REST v1.
# Silent on success; one stderr line and exit 1 on failure.
set -eu
ackid=${1:?usage: ack.sh <ackId>}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/pubsub/lib.sh
. "$here/lib.sh"

pubsub_resolve_token || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

jq -cn --arg a "$ackid" '{ackIds: [$a]}' > "$tmp/req.json"
code=$(pubsub_curl POST "$PUBSUB_URL/subscriptions/$PUBSUB_SUBSCRIPTION:acknowledge" "$tmp/req.json" "$tmp/resp.json")
[ "$code" = "200" ] || {
  printf 'ack-pubsub: acknowledge returned %s\n' "$code" >&2
  exit 1
}
