#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# cloud-workflows lib.sh: resolves Cloud Workflows REST v1 endpoints, polling
# parameters (WORKFLOW_POLL_INTERVAL_S=2, WORKFLOW_POLL_TIMEOUT_S=300), and
# OIDC/ADC Bearer token passed exclusively to curl via stdin (-H @-).
set -eu

GCP_PROJECT=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-}}
GCP_WORKFLOW_LOCATION=${GCP_WORKFLOW_LOCATION:-${GCP_REGION:-${GOOGLE_CLOUD_LOCATION:-us-central1}}}
CLOUD_WORKFLOW_NAME=${CLOUD_WORKFLOW_NAME:-lights-out-tower}
WORKFLOW_POLL_INTERVAL_S=${WORKFLOW_POLL_INTERVAL_S:-2}
WORKFLOW_POLL_TIMEOUT_S=${WORKFLOW_POLL_TIMEOUT_S:-300}
READY_TIMEOUT=${READY_TIMEOUT:-60}

_wf_api_explicit=${WORKFLOWS_API_URL:-}
WORKFLOWS_API_URL=${WORKFLOWS_API_URL:-https://workflowexecutions.googleapis.com}
if [ -n "${WORKFLOWS_ADMIN_API_URL:-}" ]; then
  WORKFLOWS_ADMIN_API_URL=${WORKFLOWS_ADMIN_API_URL%/}
elif [ -n "$_wf_api_explicit" ]; then
  WORKFLOWS_ADMIN_API_URL=${_wf_api_explicit%/}
else
  WORKFLOWS_ADMIN_API_URL=https://workflows.googleapis.com
fi

if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "replace-me" ]; then
  printf 'cloud-workflows: GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) is empty or placeholder\n' >&2
  exit 1
fi

for _u in "$WORKFLOWS_API_URL" "$WORKFLOWS_ADMIN_API_URL"; do
  case "$_u" in
    http://*|https://*) ;;
    *)
      printf 'cloud-workflows: API URL %s must start with http:// or https://\n' "$_u" >&2
      exit 1 ;;
  esac
  case "$_u" in
    *://*@*)
      printf 'cloud-workflows: API URL must not embed credentials\n' >&2
      exit 1 ;;
  esac
done
WORKFLOWS_API_URL=${WORKFLOWS_API_URL%/}

if ! awk -v s="$WORKFLOW_POLL_INTERVAL_S" 'BEGIN { exit !(s ~ /^[0-9]+(\.[0-9]+)?$/ && (s + 0) > 0) }'; then
  printf 'cloud-workflows: WORKFLOW_POLL_INTERVAL_S=%s must be a positive number\n' "$WORKFLOW_POLL_INTERVAL_S" >&2
  exit 1
fi
case "$WORKFLOW_POLL_TIMEOUT_S" in
  ''|*[!0-9]*|0*)
    printf 'cloud-workflows: WORKFLOW_POLL_TIMEOUT_S=%s must be a positive integer\n' "$WORKFLOW_POLL_TIMEOUT_S" >&2
    exit 1 ;;
esac

WORKFLOWS_PARENT="projects/$GCP_PROJECT/locations/$GCP_WORKFLOW_LOCATION/workflows/$CLOUD_WORKFLOW_NAME"
_WF_TOKEN=""

wf_resolve_token() {
  _WF_TOKEN=""
  for _cand in \
    "${WORKFLOWS_ACCESS_TOKEN:-}" \
    "${GCP_ACCESS_TOKEN:-}" \
    "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" \
    "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"; do
    if [ -n "$_cand" ] && [ "$_cand" != "replace-me" ]; then
      _WF_TOKEN=$_cand
      return 0
    fi
  done

  _cmd=${WORKFLOWS_AUTH_CMD:-${GCP_AUTH_CMD:-}}
  if [ -n "$_cmd" ]; then
    _tok=$(sh -c "$_cmd") || {
      printf 'cloud-workflows: auth command failed\n' >&2
      return 1
    }
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _WF_TOKEN=$_tok
      return 0
    fi
  fi

  if command -v gcloud >/dev/null 2>&1; then
    _tok=$(gcloud auth application-default print-access-token 2>/dev/null \
      || gcloud auth print-access-token 2>/dev/null || true)
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _WF_TOKEN=$_tok
      return 0
    fi
  fi

  _meta_url=${GCP_METADATA_URL:-}
  if [ -z "$_meta_url" ]; then
    case "$WORKFLOWS_API_URL" in
      https://*) _meta_url="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" ;;
    esac
  fi
  if [ -n "$_meta_url" ]; then
    _meta_json=$(curl -fsS --connect-timeout 2 --max-time 5 \
      -H 'Metadata-Flavor: Google' "$_meta_url" 2>/dev/null) || _meta_json=""
    if [ -n "$_meta_json" ]; then
      _tok=$(printf '%s' "$_meta_json" | jq -r '.access_token // empty' 2>/dev/null) || _tok=""
      if [ -n "$_tok" ]; then
        _WF_TOKEN=$_tok
        return 0
      fi
    fi
  fi

  case "$WORKFLOWS_API_URL" in
    https://workflowexecutions.googleapis.com*)
      printf 'cloud-workflows: no access token available (set WORKFLOWS_ACCESS_TOKEN, WORKFLOWS_AUTH_CMD, ADC, or GCE metadata)\n' >&2
      return 1 ;;
  esac
  return 0
}

# wf_curl METHOD URL BODY_FILE OUT_FILE
# Emits HTTP status code (000 on network error); Bearer token passed only via stdin (-H @-).
wf_curl() {
  _method=$1 _url=$2 _body_file=$3 _out_file=$4
  set -- -sS --connect-timeout 5 --max-time 30 -X "$_method" -o "$_out_file" -w '%{http_code}'
  if [ -n "$_body_file" ]; then
    set -- "$@" -H 'Content-Type: application/json' --data-binary "@$_body_file"
  fi
  if [ -n "$_WF_TOKEN" ]; then
    _code=$(printf 'Authorization: Bearer %s\n' "$_WF_TOKEN" | curl "$@" -H @- "$_url" 2>/dev/null) || _code=000
  else
    _code=$(curl "$@" "$_url" 2>/dev/null) || _code=000
  fi
  printf '%s' "$_code"
}
