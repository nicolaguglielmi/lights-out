#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# managed-prometheus lib.sh: resolves Google Cloud Managed Service for
# Prometheus /api/v1/query base URL and OIDC/ADC Bearer token (always passed
# to curl via stdin -H @-, never in argv).
set -eu

GCP_PROJECT=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}
GCP_PROMETHEUS_LOCATION=${GCP_PROMETHEUS_LOCATION:-global}
READY_TIMEOUT=${READY_TIMEOUT:-60}

if [ -n "${METRICS_URL:-}" ]; then
  case "$METRICS_URL" in
    http://*|https://*) ;;
    *)
      printf 'managed-prometheus: METRICS_URL=%s must start with http:// or https://\n' "$METRICS_URL" >&2
      exit 1 ;;
  esac
  case "$METRICS_URL" in
    *://*@*)
      printf 'managed-prometheus: METRICS_URL must not embed credentials\n' >&2
      exit 1 ;;
  esac
  METRICS_URL=${METRICS_URL%/}
else
  if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "replace-me" ]; then
    printf 'managed-prometheus: METRICS_URL or GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) must be set\n' >&2
    exit 1
  fi
  METRICS_URL="https://monitoring.googleapis.com/v1/projects/$GCP_PROJECT/location/$GCP_PROMETHEUS_LOCATION/prometheus"
fi

_MP_AUTH_HEADER=""

mp_resolve_auth() {
  _MP_AUTH_HEADER=""
  if [ -n "${METRICS_AUTH_HEADER:-}" ]; then
    _MP_AUTH_HEADER=$METRICS_AUTH_HEADER
    return 0
  fi

  _tok=""
  for _cand in \
    "${MANAGED_PROMETHEUS_ACCESS_TOKEN:-}" \
    "${GCP_ACCESS_TOKEN:-}" \
    "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" \
    "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"; do
    if [ -n "$_cand" ] && [ "$_cand" != "replace-me" ]; then
      _tok=$_cand
      break
    fi
  done

  if [ -z "$_tok" ]; then
    _cmd=${MANAGED_PROMETHEUS_AUTH_CMD:-${GCP_AUTH_CMD:-}}
    if [ -n "$_cmd" ]; then
      _tok=$(sh -c "$_cmd") || {
        printf 'managed-prometheus: auth command failed\n' >&2
        return 1
      }
      _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    fi
  fi

  if [ -z "$_tok" ] && command -v gcloud >/dev/null 2>&1; then
    _tok=$(gcloud auth application-default print-access-token 2>/dev/null \
      || gcloud auth print-access-token 2>/dev/null || true)
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
  fi

  if [ -z "$_tok" ]; then
    _meta_url=${GCP_METADATA_URL:-}
    if [ -z "$_meta_url" ]; then
      case "$METRICS_URL" in
        https://*) _meta_url="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" ;;
      esac
    fi
    if [ -n "$_meta_url" ]; then
      _meta_json=$(curl -fsS --connect-timeout 2 --max-time 5 \
        -H 'Metadata-Flavor: Google' "$_meta_url" 2>/dev/null) || _meta_json=""
      if [ -n "$_meta_json" ]; then
        _tok=$(printf '%s' "$_meta_json" | jq -r '.access_token // empty' 2>/dev/null) || _tok=""
      fi
    fi
  fi

  if [ -n "$_tok" ]; then
    _MP_AUTH_HEADER="Authorization: Bearer $_tok"
    return 0
  fi

  case "$METRICS_URL" in
    https://monitoring.googleapis.com*)
      printf 'managed-prometheus: no access token available (set METRICS_AUTH_HEADER, MANAGED_PROMETHEUS_ACCESS_TOKEN, ADC, or GCE metadata)\n' >&2
      return 1 ;;
  esac
  return 0
}

mp_query_raw() {
  _expr=$1
  if [ -n "$_MP_AUTH_HEADER" ]; then
    printf '%s\n' "$_MP_AUTH_HEADER" | curl -fsS --connect-timeout 5 --max-time 30 -H @- \
      -G "$METRICS_URL/api/v1/query" --data-urlencode "query=$_expr"
  else
    curl -fsS --connect-timeout 5 --max-time 30 \
      -G "$METRICS_URL/api/v1/query" --data-urlencode "query=$_expr"
  fi
}
