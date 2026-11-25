#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# publish-pubsub: publishes one JSON message on Google Cloud Pub/Sub REST v1
# ($PUBSUB_URL/topics/$PUBSUB_TOPIC:publish). Silent on success; one stderr
# line and exit 1 on failure.
set -eu
data=${1:?usage: publish.sh '<json>'}
# shellcheck disable=SC1007 # CDPATH= is the portable way to neutralise CDPATH
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=adapters/pubsub/lib.sh
. "$here/lib.sh"

pubsub_resolve_token || exit 1
ensure || exit 1

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

jq -cn --arg d "$data" '{messages: [{data: ($d | @base64)}]}' > "$tmp/req.json"
code=$(pubsub_curl POST "$PUBSUB_URL/topics/$PUBSUB_TOPIC:publish" "$tmp/req.json" "$tmp/resp.json")
[ "$code" = "200" ] || {
  printf 'publish-pubsub: publish returned %s\n' "$code" >&2
  exit 1
}
jq -e '.messageIds | length > 0' "$tmp/resp.json" >/dev/null 2>&1 || {
  printf 'publish-pubsub: no messageIds in response\n' >&2
  exit 1
}
