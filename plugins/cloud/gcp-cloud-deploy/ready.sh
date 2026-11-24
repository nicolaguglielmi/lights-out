#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
#
# ready.sh (gcp-cloud-deploy): verifies Cloud Deploy target, Cloud Run service
# Ready=True, and HTTP health probe. Silent on exit 0; 1 stderr line on exit 1.
set -eu
# shellcheck disable=SC1007 # CDPATH= neutralisation
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=/dev/null
. "$here/env.sh"
require_cli gcloud jq curl

probe=${WORKLOAD_HEALTH_PROBE:-http:/healthz}
case "$probe" in
  http:/*) probe_path=${probe#http:} ;;
  *) probe_path=/healthz ;;
esac

start=$(date +%s)
svc_state="absent"
check_url=""
while :; do
  if gcloud deploy targets describe "$CLOUD_DEPLOY_TARGET" \
      --delivery-pipeline="$CLOUD_DEPLOY_PIPELINE" \
      --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json >/dev/null 2>&1 \
     && svc_json=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
      --region="$GCP_REGION" --project="$GCP_PROJECT" --format=json 2>/dev/null); then
    svc_state=$(printf '%s' "$svc_json" | jq -r '[.status.conditions[]? | select(.type == "Ready") | .status] | first // "Unknown"')
    svc_url=$(printf '%s' "$svc_json" | jq -r '.status.url // empty')
    check_url=${APP_URL:-$svc_url}
    if [ "$svc_state" = "True" ] && [ -n "$check_url" ] \
       && curl -fsS --connect-timeout 2 --max-time 5 "$check_url$probe_path" >/dev/null 2>&1; then
      exit 0
    fi
  fi
  [ $(( $(date +%s) - start )) -lt "$READY_TIMEOUT" ] || break
  sleep "$CLOUD_DEPLOY_POLL_INTERVAL"
done
printf 'ready: gcp-cloud-deploy not ready after %ss (service %s Ready=%s url=%s)\n' \
  "$READY_TIMEOUT" "$CLOUD_RUN_SERVICE" "$svc_state" "${check_url:-none}" >&2
exit 1
