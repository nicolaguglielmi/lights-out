#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# firestore lib.sh: resolves Cloud Firestore REST v1 endpoint, Bearer token
# (passed exclusively to curl via stdin -H @-, never in argv), and plain JSON
# <-> Firestore typed field encoding (matching adapters/firestore-emulator/lib.sh).
set -eu

GCP_PROJECT=${GCP_PROJECT:-${GOOGLE_CLOUD_PROJECT:-${FIRESTORE_PROJECT:-}}}
FIRESTORE_API_URL=${FIRESTORE_API_URL:-https://firestore.googleapis.com}
FIRESTORE_DATABASE=${FIRESTORE_DATABASE:-(default)}
READY_TIMEOUT=${READY_TIMEOUT:-60}

if [ -z "$GCP_PROJECT" ] || [ "$GCP_PROJECT" = "replace-me" ]; then
  printf 'firestore: GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) is empty or placeholder\n' >&2
  exit 2
fi

case "$FIRESTORE_API_URL" in
  http://*|https://*) ;;
  *)
    printf 'firestore: FIRESTORE_API_URL=%s must start with http:// or https://\n' "$FIRESTORE_API_URL" >&2
    exit 2 ;;
esac
case "$FIRESTORE_API_URL" in
  *://*@*)
    printf 'firestore: FIRESTORE_API_URL must not embed credentials\n' >&2
    exit 2 ;;
esac
FIRESTORE_API_URL=${FIRESTORE_API_URL%/}

if [ -n "${FIRESTORE_URL:-}" ]; then
  FIRESTORE_URL=${FIRESTORE_URL%/}
else
  FIRESTORE_URL="$FIRESTORE_API_URL/v1/projects/$GCP_PROJECT/databases/$FIRESTORE_DATABASE/documents"
fi

FS_ENCODE='
def enc:
  if type == "string" then {stringValue: .}
  elif type == "number" then (if . == floor then {integerValue: (tostring)} else {doubleValue: .} end)
  elif type == "boolean" then {booleanValue: .}
  elif type == "null" then {nullValue: null}
  elif type == "array" then {arrayValue: {values: map(enc)}}
  else {mapValue: {fields: with_entries(.value |= enc)}}
  end;
if type == "object" then {fields: with_entries(.value |= enc)} else error("not an object") end'

FS_DECODE='
def dec:
  if has("stringValue") then .stringValue
  elif has("integerValue") then (.integerValue | tonumber)
  elif has("doubleValue") then .doubleValue
  elif has("booleanValue") then .booleanValue
  elif has("nullValue") then null
  elif has("arrayValue") then ((.arrayValue.values // []) | map(dec))
  elif has("mapValue") then ((.mapValue.fields // {}) | with_entries(.value |= dec))
  else . end;
(.fields // {}) | with_entries(.value |= dec)'

_FIRESTORE_TOKEN=""

firestore_validate_segment() {
  _seg=$1 _label=$2
  case "$_seg" in
    ''|.|..|*/*|*[[:space:]]*)
      printf 'firestore: invalid %s %s\n' "$_label" "$_seg" >&2
      return 1 ;;
  esac
  return 0
}

firestore_resolve_token() {
  _FIRESTORE_TOKEN=""
  for _cand in \
    "${FIRESTORE_ACCESS_TOKEN:-}" \
    "${GCP_ACCESS_TOKEN:-}" \
    "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" \
    "${GOOGLE_OAUTH_ACCESS_TOKEN:-}"; do
    if [ -n "$_cand" ] && [ "$_cand" != "replace-me" ]; then
      _FIRESTORE_TOKEN=$_cand
      return 0
    fi
  done

  _cmd=${FIRESTORE_AUTH_CMD:-${GCP_AUTH_CMD:-}}
  if [ -n "$_cmd" ]; then
    _tok=$(sh -c "$_cmd") || {
      printf 'firestore: auth command failed\n' >&2
      return 1
    }
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _FIRESTORE_TOKEN=$_tok
      return 0
    fi
  fi

  if command -v gcloud >/dev/null 2>&1; then
    _tok=$(gcloud auth application-default print-access-token 2>/dev/null \
      || gcloud auth print-access-token 2>/dev/null || true)
    _tok=$(printf '%s' "$_tok" | tr -d '\r\n')
    if [ -n "$_tok" ]; then
      _FIRESTORE_TOKEN=$_tok
      return 0
    fi
  fi

  _meta_url=${GCP_METADATA_URL:-}
  if [ -z "$_meta_url" ]; then
    case "$FIRESTORE_URL" in
      https://*) _meta_url="http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" ;;
    esac
  fi
  if [ -n "$_meta_url" ]; then
    _meta_json=$(curl -fsS --connect-timeout 2 --max-time 5 \
      -H 'Metadata-Flavor: Google' "$_meta_url" 2>/dev/null) || _meta_json=""
    if [ -n "$_meta_json" ]; then
      _tok=$(printf '%s' "$_meta_json" | jq -r '.access_token // empty' 2>/dev/null) || _tok=""
      if [ -n "$_tok" ]; then
        _FIRESTORE_TOKEN=$_tok
        return 0
      fi
    fi
  fi

  case "$FIRESTORE_API_URL" in
    https://firestore.googleapis.com*)
      printf 'firestore: no access token available (set FIRESTORE_ACCESS_TOKEN, FIRESTORE_AUTH_CMD, ADC, or GCE metadata)\n' >&2
      return 1 ;;
  esac
  return 0
}

# firestore_curl METHOD URL BODY_FILE OUT_FILE
# Emits HTTP status code (000 on network error); Bearer token passed only via stdin (-H @-).
firestore_curl() {
  _method=$1 _url=$2 _body_file=$3 _out_file=$4
  set -- -sS --connect-timeout 5 --max-time 30 -X "$_method" -o "$_out_file" -w '%{http_code}'
  if [ -n "$_body_file" ]; then
    set -- "$@" -H 'Content-Type: application/json' --data-binary "@$_body_file"
  fi
  if [ -n "$_FIRESTORE_TOKEN" ]; then
    _code=$(printf 'Authorization: Bearer %s\n' "$_FIRESTORE_TOKEN" | curl "$@" -H @- "$_url" 2>/dev/null) || _code=000
  else
    _code=$(curl "$@" "$_url" 2>/dev/null) || _code=000
  fi
  printf '%s' "$_code"
}
