#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# cloud-monitoring env.sh: resolves MONITORING_API_URL (overridden by
# CHARTS_URL during port conformance), project id, and optional bearer token
# passed exclusively to curl via stdin (-H @-), never in argv.
set -eu

CM_BASE_URL=${CHARTS_URL:-${MONITORING_API_URL:-}}
CM_PROJECT=${MONITORING_PROJECT:-${GOOGLE_CLOUD_PROJECT:-${GCP_PROJECT:-}}}
CM_TOKEN=${MONITORING_ACCESS_TOKEN:-${CLOUDSDK_AUTH_ACCESS_TOKEN:-${GOOGLE_OAUTH_ACCESS_TOKEN:-${MONITORING_TOKEN:-}}}}
MONITORING_AUTH_HEADER=${MONITORING_AUTH_HEADER:-}
READY_TIMEOUT=${READY_TIMEOUT:-60}

case "$CM_BASE_URL" in
  ''|replace-me)
    printf 'cloud-monitoring: MONITORING_API_URL is empty or placeholder\n' >&2
    exit 1 ;;
  http://*|https://*) ;;
  *)
    printf 'cloud-monitoring: MONITORING_API_URL=%s must start with http:// or https://\n' "$CM_BASE_URL" >&2
    exit 1 ;;
esac
case "$CM_BASE_URL" in
  *://*@*)
    printf 'cloud-monitoring: MONITORING_API_URL must not embed credentials\n' >&2
    exit 1 ;;
esac
CM_BASE_URL=${CM_BASE_URL%/}

case "$CM_BASE_URL" in
  */timeSeries:query)
    CM_QUERY_URL=$CM_BASE_URL ;;
  */v3/projects/*)
    CM_QUERY_URL="$CM_BASE_URL/timeSeries:query" ;;
  *)
    CM_QUERY_URL="$CM_BASE_URL/v3/projects/${CM_PROJECT:-default}/timeSeries:query" ;;
esac

if [ -n "$MONITORING_AUTH_HEADER" ]; then
  CM_AUTH_HEADER=$MONITORING_AUTH_HEADER
elif [ -n "$CM_TOKEN" ] && [ "$CM_TOKEN" != replace-me ]; then
  CM_AUTH_HEADER="Authorization: Bearer $CM_TOKEN"
else
  CM_AUTH_HEADER=""
fi

cm_curl_post() {
  _url=$1 _body=$2 _out=$3
  if [ -n "$CM_AUTH_HEADER" ]; then
    printf '%s\n' "$CM_AUTH_HEADER" | curl -fsS --connect-timeout 2 --max-time 5 \
      -X POST -H 'Content-Type: application/json' -H @- --data-binary "$_body" -o "$_out" "$_url"
  else
    curl -fsS --connect-timeout 2 --max-time 5 \
      -X POST -H 'Content-Type: application/json' --data-binary "$_body" -o "$_out" "$_url"
  fi
}
