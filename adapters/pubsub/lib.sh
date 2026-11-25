#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# pubsub lib.sh: resolves Google Cloud Pub/Sub REST v1 endpoint, topic and
# subscription configuration, OIDC/ADC Bearer token (always fed to curl on
# stdin via -H @-, never in argv), and idempotent topic/subscription creation.
set -eu

GCP_PROJECT=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-${PUBSUB_PROJECT:-}}}
PUBSUB_API_URL=${PUBSUB_API_URL:-https://pubsub.googleapis.com}
PUBSUB_TOPIC=${PUBSUB_TOPIC:-tower}
PUBSUB_SUBSCRIPTION=${PUBSUB_SUBSCRIPTION:-tower-inproc}
PUBSUB_AUTO_CREATE=${PUBSUB_AUTO_CREATE:-1}
PUBSUB_ACK_DEADLINE=${PUBSUB_ACK_DEADLINE:-${CONFORMANCE_ACK_DEADLINE:-60}}
PUBSUB_RETURN_IMMEDIATELY=${PUBSUB_RETURN_IMMEDIATELY:-true}
READY_TIMEOUT=${READY_TIMEOUT:-60}

if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "replace-me" ]; then
  printf 'pubsub: GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) is empty or placeholder\n' >&2
  exit 1
fi

case "$PUBSUB_API_URL" in
  http://*|https://*) ;;
  *)
    printf 'pubsub: PUBSUB_API_URL=%s must start with http:// or https://\n' "$PUBSUB_API_URL" >&2
    exit 1 ;;
esac
case "$PUBSUB_API_URL" in
  *://*@*)
    printf 'pubsub: PUBSUB_API_URL must not embed credentials\n' >&2
    exit 1 ;;
esac
PUBSUB_API_URL=${PUBSUB_API_URL%/}

if [ -n "${PUBSUB_URL:-}" ]; then
  PUBSUB_URL=${PUBSUB_URL%/}
else
  PUBSUB_URL="$PUBSUB_API_URL/v1/projects/$GCP_PROJECT"
fi

case "$PUBSUB_ACK_DEADLINE" in
  ''|*[!0-9]*|0*)
    printf 'pubsub: PUBSUB_ACK_DEADLINE=%s must be a positive integer of seconds\n' "$PUBSUB_ACK_DEADLINE" >&2
    exit 1 ;;
esac

_PUBSUB_TOKEN=""

pubsub_resolve_token() {
  _PUBSUB_TOKEN=""
  for _cand in \
    "${PUBSUB_ACCESS_TOKEN:-}" \
    "${GCP_ACCESS_TOKEN:-}" \
    "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" \
    "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"; do
    if [ -n "$_cand" ] && [ "$_cand" != "replace-me" ]; then
      _PUBSUB_TOKEN=$_cand
      return 0
    fi
  done

  _cmd=${PUBSUB_AUTH_CMD:-${GCP_AUTH_CMD:-}}
  if [ -n "$_cmd" ]; then
    _tok=$(sh -c "$_cmd") || {
      printf 'pubsub: auth command failed\n' >&2
      return 1
    }
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _PUBSUB_TOKEN=$_tok
      return 0
    fi
  fi

  if command -v gcloud >/dev/null 2>&1; then
    _tok=$(gcloud auth application-default print-access-token 2>/dev/null \
      || gcloud auth print-access-token 2>/dev/null || true)
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _PUBSUB_TOKEN=$_tok
      return 0
    fi
  fi

  _meta_url=${GCP_METADATA_URL:-}
  if [ -z "$_meta_url" ]; then
    case "$PUBSUB_URL" in
      https://*) _meta_url="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" ;;
    esac
  fi
  if [ -n "$_meta_url" ]; then
    _meta_json=$(curl -fsS --connect-timeout 2 --max-time 5 \
      -H 'Metadata-Flavor: Google' "$_meta_url" 2>/dev/null) || _meta_json=""
    if [ -n "$_meta_json" ]; then
      _tok=$(printf '%s' "$_meta_json" | jq -r '.access_token // empty' 2>/dev/null) || _tok=""
      if [ -n "$_tok" ]; then
        _PUBSUB_TOKEN=$_tok
        return 0
      fi
    fi
  fi

  case "$PUBSUB_API_URL" in
    https://pubsub.googleapis.com*)
      printf 'pubsub: no access token available (set PUBSUB_ACCESS_TOKEN, PUBSUB_AUTH_CMD, ADC, or GCE metadata)\n' >&2
      return 1 ;;
  esac
  return 0
}

# pubsub_curl METHOD URL BODY_FILE OUT_FILE
# Emits HTTP status code (000 on network error); Bearer token passed only via stdin (-H @-).
pubsub_curl() {
  _method=$1 _url=$2 _body_file=$3 _out_file=$4
  set -- -sS --connect-timeout 5 --max-time 30 -X "$_method" -o "$_out_file" -w '%{http_code}'
  if [ -n "$_body_file" ]; then
    set -- "$@" -H 'Content-Type: application/json' --data-binary "@$_body_file"
  fi
  if [ -n "$_PUBSUB_TOKEN" ]; then
    _code=$(printf 'Authorization: Bearer %s\n' "$_PUBSUB_TOKEN" | curl "$@" -H @- "$_url" 2>/dev/null) || _code=000
  else
    _code=$(curl "$@" "$_url" 2>/dev/null) || _code=000
  fi
  printf '%s' "$_code"
}

ensure() {
  [ "$PUBSUB_AUTO_CREATE" = "1" ] || return 0
  _etmp=$(mktemp -d)
  printf '{}\n' > "$_etmp/topic.json"
  _code=$(pubsub_curl PUT "$PUBSUB_URL/topics/$PUBSUB_TOPIC" "$_etmp/topic.json" "$_etmp/topic.out")
  case "$_code" in
    200|409) : ;;
    *)
      printf 'pubsub: create topic %s returned %s\n' "$PUBSUB_TOPIC" "$_code" >&2
      rm -rf "$_etmp"
      return 1 ;;
  esac

  jq -cn --arg t "projects/$GCP_PROJECT/topics/$PUBSUB_TOPIC" --argjson d "$PUBSUB_ACK_DEADLINE" \
    '{topic: $t, ackDeadlineSeconds: $d}' > "$_etmp/sub.json"
  _code=$(pubsub_curl PUT "$PUBSUB_URL/subscriptions/$PUBSUB_SUBSCRIPTION" "$_etmp/sub.json" "$_etmp/sub.out")
  case "$_code" in
    200|409)
      rm -rf "$_etmp"
      return 0 ;;
    *)
      printf 'pubsub: create subscription %s returned %s\n' "$PUBSUB_SUBSCRIPTION" "$_code" >&2
      rm -rf "$_etmp"
      return 1 ;;
  esac
}
