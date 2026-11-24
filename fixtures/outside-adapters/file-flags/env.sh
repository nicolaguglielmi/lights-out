#!/bin/sh
# shellcheck disable=SC2034 # consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# file-flags env.sh: file-backed Flags adapter fixture for cold
# ADR 0058 / ADR 0107 outside/* conformance testing.
set -eu

FILE_FLAGS_DIR=${FILE_FLAGS_DIR:-}
if [ -z "$FILE_FLAGS_DIR" ]; then
  printf 'file-flags: FILE_FLAGS_DIR is empty\n' >&2
  exit 1
fi

FILE_FLAGS_PATH="$FILE_FLAGS_DIR/flags.json"

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    printf 'file-flags: jq is not on PATH\n' >&2
    exit 1
  }
}

init_flags() {
  require_jq
  mkdir -p "$FILE_FLAGS_DIR"
  if [ ! -f "$FILE_FLAGS_PATH" ]; then
    jq -cn '{flags: {"release-freeze": {state: "ENABLED", variants: {on: true, off: false}, defaultVariant: "off"}}}' > "$FILE_FLAGS_PATH"
  fi
}

write_flags() {
  _tmp=$(mktemp "$FILE_FLAGS_DIR/.flags.XXXXXX")
  printf '%s\n' "$1" > "$_tmp"
  mv "$_tmp" "$FILE_FLAGS_PATH"
}
