#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# unleash env.sh: validates UNLEASH_URL and UNLEASH_API_TOKEN and provides
# curl helpers that pass Authorization exclusively via stdin (-H @-), never argv.
set -eu

for _tool in curl jq; do
  command -v "$_tool" >/dev/null 2>&1 || {
    printf 'unleash: %s is not on PATH\n' "$_tool" >&2
    exit 1
  }
done

UNLEASH_URL=${UNLEASH_URL:-}
UNLEASH_API_TOKEN=${UNLEASH_API_TOKEN:-}
UNLEASH_PROJECT=${UNLEASH_PROJECT:-default}
UNLEASH_ENVIRONMENT=${UNLEASH_ENVIRONMENT:-development}
READY_TIMEOUT=${READY_TIMEOUT:-60}

case "$UNLEASH_URL" in
  ''|replace-me)
    printf 'unleash: UNLEASH_URL is empty or placeholder\n' >&2
    exit 1 ;;
  http://*|https://*) ;;
  *)
    printf 'unleash: UNLEASH_URL=%s must start with http:// or https://\n' "$UNLEASH_URL" >&2
    exit 1 ;;
esac
case "$UNLEASH_URL" in
  *://*@*)
    printf 'unleash: UNLEASH_URL must not embed credentials\n' >&2
    exit 1 ;;
esac
UNLEASH_URL=${UNLEASH_URL%/}

case "$UNLEASH_API_TOKEN" in
  ''|replace-me)
    printf 'unleash: UNLEASH_API_TOKEN is empty; set a token in .env\n' >&2
    exit 1 ;;
esac

if [ -z "$UNLEASH_PROJECT" ]; then
  printf 'unleash: UNLEASH_PROJECT must not be empty\n' >&2
  exit 1
fi
if [ -z "$UNLEASH_ENVIRONMENT" ]; then
  printf 'unleash: UNLEASH_ENVIRONMENT must not be empty\n' >&2
  exit 1
fi
case "$READY_TIMEOUT" in
  ''|*[!0-9]*|0*)
    printf 'unleash: READY_TIMEOUT=%s must be a positive integer\n' "$READY_TIMEOUT" >&2
    exit 1 ;;
esac

unleash_encode() {
  printf '%s' "$1" | jq -Rr @uri
}

# unleash_curl METHOD URL OUT_FILE -> prints HTTP status code (000 on network error)
unleash_curl() {
  _method=$1 _url=$2 _out=$3
  _code=$(printf 'Authorization: %s\n' "$UNLEASH_API_TOKEN" \
    | curl -sS --connect-timeout 2 --max-time 5 -X "$_method" \
        -H 'Accept: application/json' -H @- -o "$_out" -w '%{http_code}' "$_url" 2>/dev/null) || _code=000
  printf '%s' "${_code:-000}"
}
