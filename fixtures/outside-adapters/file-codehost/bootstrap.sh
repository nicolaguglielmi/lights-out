#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"
repo_root=$(CDPATH='' cd -- "$here/../../.." && pwd)

init_store
if [ ! -d "$FILE_CODEHOST_REPO" ]; then
  git init --bare -q --initial-branch=main "$FILE_CODEHOST_REPO" 2>/dev/null \
    || { git init --bare -q "$FILE_CODEHOST_REPO" && git --git-dir="$FILE_CODEHOST_REPO" symbolic-ref HEAD refs/heads/main; }
fi

if [ -z "${CODE_HOST_KEEP_MAIN:-}" ]; then
  git -C "${WORKLOAD:-$repo_root}" push --force -q "$FILE_CODEHOST_REPO" HEAD:refs/heads/main >&2 \
    || { printf 'file-codehost: push to main failed\n' >&2; exit 1; }
fi
