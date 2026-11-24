#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# canary-url.sh (gcp-cloud-deploy): prints the direct canary tag URL on stdout.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq

url=${CANARY_URL:-}
if [ -z "$url" ]; then
  url=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
    --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json \
    | jq -r '[.status.traffic[]? | select(.tag == "canary") | .url] | first // empty')
fi
[ -n "$url" ] || {
  printf 'canary-url-gcp-cloud-deploy: no canary tag URL found on Cloud Run service %s\n' "$CLOUD_RUN_SERVICE" >&2
  exit 1
}
printf '%s\n' "$url"
