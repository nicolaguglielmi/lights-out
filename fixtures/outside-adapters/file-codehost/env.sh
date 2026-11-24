#!/bin/sh
# shellcheck disable=SC2034 # consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# file-codehost env.sh: bare-git + JSON state CodeHost adapter fixture for
# cold ADR 0058 / ADR 0104 outside/* conformance testing.
set -eu

FILE_CODEHOST_DIR=${FILE_CODEHOST_DIR:-}
if [ -z "$FILE_CODEHOST_DIR" ]; then
  printf 'file-codehost: FILE_CODEHOST_DIR is empty\n' >&2
  exit 1
fi

FILE_CODEHOST_REPO="$FILE_CODEHOST_DIR/repo.git"
FILE_CODEHOST_PRS="$FILE_CODEHOST_DIR/prs.json"
FILE_CODEHOST_STATUSES="$FILE_CODEHOST_DIR/statuses"

require_tools() {
  for _t in git jq; do
    command -v "$_t" >/dev/null 2>&1 || {
      printf 'file-codehost: %s is not on PATH\n' "$_t" >&2
      exit 1
    }
  done
}

init_store() {
  require_tools
  mkdir -p "$FILE_CODEHOST_DIR" "$FILE_CODEHOST_STATUSES"
  if [ ! -f "$FILE_CODEHOST_PRS" ]; then
    printf '[]\n' > "$FILE_CODEHOST_PRS"
  fi
}

write_prs() {
  _tmp=$(mktemp "$FILE_CODEHOST_DIR/.prs.XXXXXX")
  printf '%s\n' "$1" > "$_tmp"
  mv "$_tmp" "$FILE_CODEHOST_PRS"
}
