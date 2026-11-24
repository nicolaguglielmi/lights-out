#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# shift.sh (gcp-cloud-deploy): shifts canary traffic to 5, 25, or 100 percent
# by advancing the Cloud Deploy rollout phase and verifying Cloud Run traffic.
set -eu
percent=${1:?usage: shift.sh <canary-percent>}
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq

case "$percent" in
  5) phase_id=canary-5 ;;
  25) phase_id=canary-25 ;;
  100) phase_id=stable ;;
  *)
    printf 'shift-gcp-cloud-deploy: invalid canary percent %s (allowed: 5, 25, 100)\n' "$percent" >&2
    exit 1
    ;;
esac

rel_full=$(gcloud deploy releases list \
  --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT" \
  --format=json | jq -r '.[0].name // empty')
[ -n "$rel_full" ] || {
  printf 'shift-gcp-cloud-deploy: no release found in pipeline %s\n' "$CLOUD_DEPLOY_PIPELINE" >&2
  exit 1
}
rel_name=${rel_full##*/}

rollout_full=$(gcloud deploy rollouts list \
  --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
  --release="$rel_name" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT" \
  --format=json | jq -r '.[0].name // empty')
[ -n "$rollout_full" ] || {
  printf 'shift-gcp-cloud-deploy: no rollout found for release %s\n' "$rel_name" >&2
  exit 1
}
rollout_name=${rollout_full##*/}

gcloud deploy rollouts advance "$rollout_name" \
  --release="$rel_name" \
  --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
  --region="$GCP_REGION" \
  --project="$GCP_PROJECT" \
  --phase-id="$phase_id" \
  --quiet >/dev/null

got=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
  --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json \
  | jq -r '[.status.traffic[]? | select(.tag == "canary") | .percent] | first // 0')
[ "$got" = "$percent" ] || {
  printf 'shift-gcp-cloud-deploy: Cloud Run reports canary weight %s after advancing %s, want %s\n' \
    "$got" "$phase_id" "$percent" >&2
  exit 1
}
