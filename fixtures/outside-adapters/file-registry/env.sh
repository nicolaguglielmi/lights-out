#!/bin/sh
# shellcheck disable=SC2034 # consumed by sourcing verb scripts
# SPDX-License-Identifier: Apache-2.0
#
# file-registry env.sh: file-backed Registry adapter fixture for cold
# ADR 0058 / ADR 0107 outside/* conformance testing.
set -eu

FILE_REGISTRY_DIR=${FILE_REGISTRY_DIR:-}
if [ -z "$FILE_REGISTRY_DIR" ]; then
  printf 'file-registry: FILE_REGISTRY_DIR is empty\n' >&2
  exit 1
fi

FILE_REGISTRY_REPO=${FILE_REGISTRY_REPO:-outside/file-registry}
FILE_REGISTRY_KEY=${FILE_REGISTRY_KEY:-trusted-key}

require_jq() {
  command -v jq >/dev/null 2>&1 || {
    printf 'file-registry: jq is not on PATH\n' >&2
    exit 1
  }
}

init_registry() {
  require_jq
  mkdir -p "$FILE_REGISTRY_DIR/tags" "$FILE_REGISTRY_DIR/sboms"
}
