#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# status.sh (gcp-cloud-deploy): emits exact 3-key JSON {stable, canary, weight}.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq

svc_json=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json)

stable_rev=$(printf '%s' "$svc_json" | jq -r '[.status.traffic[]? | select((.tag // "") != "canary") | .revisionName] | first // empty')
[ -n "$stable_rev" ] || {
  printf 'status-gcp-cloud-deploy: no stable revision found in Cloud Run service %s\n' "$CLOUD_RUN_SERVICE" >&2
  exit 1
}
stable_ver=$(resolve_revision_version "$stable_rev") || {
  printf 'status-gcp-cloud-deploy: could not resolve version for stable revision %s\n' "$stable_rev" >&2
  exit 1
}

canary_rev=$(printf '%s' "$svc_json" | jq -r '[.status.traffic[]? | select(.tag == "canary") | .revisionName] | first // empty')
canary_w=$(printf '%s' "$svc_json" | jq -r '[.status.traffic[]? | select(.tag == "canary") | .percent] | first // 0')
if [ -n "$canary_rev" ]; then
  canary_ver=$(resolve_revision_version "$canary_rev") || {
    printf 'status-gcp-cloud-deploy: could not resolve version for canary revision %s\n' "$canary_rev" >&2
    exit 1
  }
  canary_json=$(printf '%s' "$canary_ver" | jq -R .)
else
  canary_json=null
fi

jq -cn --arg s "$stable_ver" --argjson c "$canary_json" --argjson w "$canary_w" \
  '{stable: $s, canary: $c, weight: $w}'
