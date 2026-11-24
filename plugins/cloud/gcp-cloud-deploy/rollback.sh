#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# rollback.sh (gcp-cloud-deploy): idempotently returns 100% traffic to stable.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq

svc_json=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json)
has_canary=$(printf '%s' "$svc_json" | jq -r 'any(.status.traffic[]?; .tag == "canary" or (.percent // 0) > 0 and (.tag == "canary"))')
canary_w=$(printf '%s' "$svc_json" | jq -r '[.status.traffic[]? | select(.tag == "canary") | .percent] | first // 0')

if [ "$has_canary" = "true" ] || [ "$canary_w" != "0" ]; then
  gcloud deploy targets rollback "$CLOUD_DEPLOY_TARGET" \
    --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
    --region="$GCP_REGION" \
    --project="$GCP_PROJECT" \
    --quiet >/dev/null
fi

got=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json \
  | jq -r '[.status.traffic[]? | select(.tag == "canary") | .percent] | first // 0')
[ "$got" = "0" ] || {
  printf 'rollback-gcp-cloud-deploy: canary weight still %s after rollback\n' "$got" >&2
  exit 1
}
