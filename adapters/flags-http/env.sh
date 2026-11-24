#!/bin/sh
# shellcheck disable=SC2034 # variables and helpers are consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# flags-http env.sh: validates OFREP endpoint URL and provides a curl helper
# that passes optional FLAGS_AUTH_HEADER via a 0600 temp file (never argv).
set -eu

FLAGS_URL=${FLAGS_URL:-}
FLAGS_AUTH_HEADER=${FLAGS_AUTH_HEADER:-}
FLAGS_SET_URL=${FLAGS_SET_URL:-}
READY_TIMEOUT=${READY_TIMEOUT:-60}

validate_http_url() {
  _name=$1 _val=$2
  case "$_val" in
    ''|replace-me)
      printf 'flags-http: %s is empty or placeholder\n' "$_name" >&2
      return 1 ;;
    http://* | https://*) ;;
    *)
      printf 'flags-http: %s=%s must start with http:// or https://\n' "$_name" "$_val" >&2
      return 1 ;;
  esac
  case "$_val" in
    *://*@*)
      printf 'flags-http: %s must not embed credentials in URL\n' "$_name" >&2
      return 1 ;;
  esac
  return 0
}

validate_http_url FLAGS_URL "$FLAGS_URL" || exit 1
FLAGS_URL=${FLAGS_URL%/}

if [ -n "$FLAGS_SET_URL" ]; then
  validate_http_url FLAGS_SET_URL "$FLAGS_SET_URL" || exit 1
  FLAGS_SET_URL=${FLAGS_SET_URL%/}
fi

flags_http_encode() {
  printf '%s' "$1" | jq -Rr @uri
}

flags_http_post() {
  _url=$1 _json=$2 _out=$3
  _hdr_file=""
  if [ -n "$FLAGS_AUTH_HEADER" ]; then
    _hdr_file=$(mktemp)
    chmod 600 "$_hdr_file"
    printf '%s\n' "$FLAGS_AUTH_HEADER" > "$_hdr_file"
  fi
  set -- -fsS --connect-timeout 2 --max-time 5 -X POST -H 'Content-Type: application/json'
  [ -z "$_hdr_file" ] || set -- "$@" -H @"$_hdr_file"
  set -- "$@" -d "$_json" -o "$_out" "$_url"
  _rc=0
  curl "$@" >/dev/null 2>&1 || _rc=$?
  [ -z "$_hdr_file" ] || rm -f "$_hdr_file"
  return "$_rc"
}
