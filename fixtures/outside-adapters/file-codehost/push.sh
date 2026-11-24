#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu
workspace=${1:?usage: push.sh <workspace> <branch>}
branch=${2:?usage: push.sh <workspace> <branch>}
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# shellcheck source=fixtures/outside-adapters/file-codehost/env.sh
. "$here/env.sh"

git -C "$workspace" push --force -q "$FILE_CODEHOST_REPO" "HEAD:refs/heads/$branch" >&2
