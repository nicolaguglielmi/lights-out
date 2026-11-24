#!/bin/sh
# shellcheck disable=SC2034 # consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# file-delivery env.sh: file-backed Delivery adapter fixture for cold
# ADR 0058 / ADR 0104 outside/* conformance testing.
set -eu

FILE_DELIVERY_DIR=${FILE_DELIVERY_DIR:-}
if [ -z "$FILE_DELIVERY_DIR" ]; then
  printf 'file-delivery: FILE_DELIVERY_DIR is empty\n' >&2
  exit 1
fi

FILE_DELIVERY_STATE="$FILE_DELIVERY_DIR/state.json"
FILE_DELIVERY_CANARY_URL=${FILE_DELIVERY_CANARY_URL:-http://127.0.0.1:8080}

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    printf 'file-delivery: jq is not on PATH\n' >&2
    exit 1
  }
}

init_state() {
  require_jq
  mkdir -p "$FILE_DELIVERY_DIR"
  if [ ! -f "$FILE_DELIVERY_STATE" ]; then
    jq -cn --arg stable "${FILE_DELIVERY_INITIAL_STABLE:-0.1.0}" \
      '{stable: $stable, canary: null, weight: 0}' > "$FILE_DELIVERY_STATE"
  fi
}

write_state() {
  _tmp=$(mktemp "$FILE_DELIVERY_DIR/.state.XXXXXX")
  printf '%s\n' "$1" > "$_tmp"
  mv "$_tmp" "$FILE_DELIVERY_STATE"
}
